"""云端每周任务与邮件相关命令（systemd timer 调用；跑完即退出）。

- weekly：一个周期一次分析 + 一封邮件；同一周期（含失败）不重复分析；
- email-preview：只读已保存结果打印邮件，不分析、不发送、不写库；
- email-retry：重发已保存的邮件内容，不重新分析；
- cycles / backup：人工恢复与一致性备份。

CLI 只做参数解析与转发，实现在这里；`cmd_run` 等仍复用 learnradar.cli 的实现。
"""

from __future__ import annotations

import argparse
import hashlib
import re
import traceback
import json
import shutil
from pathlib import Path
from typing import Any

from .config import ConfigError, Settings
from .cycle import (
    CYCLE_COMPLETED,
    CYCLE_FAILED,
    CYCLE_INTERRUPTED,
    CYCLE_RUNNING,
    CYCLE_TERMINAL,
    CYCLE_UNKNOWN,
    TaskLock,
    TaskTimeout,
    classify_cycle,
    clear_task_timeout,
    collect_outcome,
    current_time,
    cycle_id_for,
    install_task_timeout,
    scheduled_instant,
    is_stale,
    schedule_config,
    stale_seconds,
)
from .net import now_iso
from .report import render_markdown
from .notify import (
    KIND_REPORT,
    EmailConfig,
    build_email,
    load_email_config,
    scan_sensitive,
    send_email,
)
from .store import Store, backup_database
from .validate import ValidationResult

# 与 learnradar.cli 的退出码保持一致
EXIT_OK = 0
EXIT_USAGE = 1
EXIT_GATE_FAILED = 2


def _print(message: str = "") -> None:
    print(message, flush=True)


def _latest_run_id(store: Store) -> str:
    """最近一次运行：优先有报告的，其次只有运行记录的。"""
    row = store.conn.execute(
        "SELECT run_id FROM reports ORDER BY created_at DESC, rowid DESC LIMIT 1"
    ).fetchone()
    if row:
        return str(row["run_id"])
    row = store.conn.execute(
        "SELECT run_id FROM runs ORDER BY started_at DESC, rowid DESC LIMIT 1"
    ).fetchone()
    return str(row["run_id"]) if row else ""


# 脱敏：只看类别，不回显路径与疑似凭据
_SECRET_LIKE = re.compile(
    r"sk-[A-Za-z0-9_\-]{8,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}"
    r"|(?i:bearer)\s+\S+|(?i:password|passwd|token|api[_-]?key)\s*[:=]\s*\S+"
)
_PATH_LIKE = re.compile(r"(?:[A-Za-z]:\\[^\s:;]*|/(?:home|srv|var|opt|tmp|Users)/[^\s:;]*)")


def sanitize_text(text: str, settings: Settings) -> str:
    """把绝对路径与疑似凭据替换掉，保留能定位问题的信息。"""
    cleaned = str(text or "")
    for raw, replacement in (
        (str(settings.data_dir), "<数据目录>"),
        (str(settings.root), "<代码目录>"),
        (str(Path.home()), "<用户目录>"),
    ):
        if raw:
            cleaned = cleaned.replace(raw, replacement)
    cleaned = _SECRET_LIKE.sub("<已隐藏>", cleaned)
    cleaned = _PATH_LIKE.sub("<路径>", cleaned)
    return cleaned


def sanitize_error(exc: BaseException, settings: Settings) -> str:
    text = re.sub(r"\s+", " ", sanitize_text(f"{type(exc).__name__}: {exc}", settings)).strip()
    return text[:300]

def _validation_result(summary: dict[str, Any]) -> ValidationResult:
    return ValidationResult(
        ok=bool(summary.get("ok")),
        errors=[str(item) for item in summary.get("errors") or []],
        warnings=[str(item) for item in summary.get("warnings") or []],
        rules_version=str(summary.get("rules_version") or ""),
        content_review=str(summary.get("content_review") or "not_performed"),
    )


def sanitized_markdown(outcome: dict[str, Any]) -> str:
    """对外可发送的 Markdown：只从允许字段渲染。

    排除的内容：用户反馈（报告附录 E）、真实档案、原始提示词、审计与运行元信息（附录 B）。
    保留的内容：项目一句话、亮点、价值、缺口、结论、来源（附录 A）与核验状态（附录 C）。
    """
    payload = outcome.get("payload") or {}
    if not payload:
        return ""
    try:
        return render_markdown(
            report=payload,
            evidence=list(outcome.get("evidence") or []),
            meta={},
            validation=_validation_result(outcome.get("validation") or {}),
            stage_usage=[],
            feedback_rows=None,
            include_run_meta=False,
        )
    except Exception:  # 渲染失败就不带附件，绝不退回原报告文件
        return ""


def write_email_attachment(settings: Settings, outcome: dict[str, Any]) -> tuple[str, str]:
    """把对外版本落盘（供 email-retry 重发），返回 (路径, 文本)。"""
    text = sanitized_markdown(outcome)
    if not text.strip():
        return "", ""
    path = settings.data_dir / "runs" / str(outcome.get("run_id") or "") / "email-report.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return str(path), text


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()

def _handle_unexpected_error(
    settings: Settings,
    store: Store,
    cycle_id: str,
    run_id: str,
    exc: BaseException,
) -> int:
    """周期占位之后出现未捕获异常：落下脱敏诊断、标成 failed、不发邮件、不自动重跑。

    标 failed（而不是 interrupted）是因为我们在这里亲眼看到了异常；interrupted 只用于
    “另一个进程已经不在了、但周期还是 running”的情况。failed 可以由 --force-redo 人工恢复。
    """
    summary = sanitize_error(exc, settings)
    run_dir = settings.data_dir / "runs" / run_id
    try:
        run_dir.mkdir(parents=True, exist_ok=True)
        detail = sanitize_text(
            "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)), settings
        )
        exc.__traceback__ = None  # 及时释放帧引用：cmd_run 里未关闭的 SQLite 连接会随之回收
        (run_dir / "weekly-error.txt").write_text(
            f"时间：{now_iso()}\n周期：{cycle_id}\n运行：{run_id}\n"
            "（下面的路径与疑似凭据已脱敏）\n\n"
            + detail
            + "\n",
            encoding="utf-8",
        )
    except OSError:
        pass
    usage_path = run_dir / "usage.json"
    if usage_path.exists():
        try:
            totals = json.loads(usage_path.read_text(encoding="utf-8")).get("totals") or {}
            usage_note = (
                f"已保留用量快照：{int(totals.get('calls') or 0)} 次模型调用，输入 "
                f"{int(totals.get('input_tokens') or 0)} / 输出 {int(totals.get('output_tokens') or 0)} token"
            )
        except (OSError, json.JSONDecodeError, ValueError):
            usage_note = "用量快照存在但读不出来（请看运行目录）"
    else:
        usage_note = "本次没有已落盘的模型用量（异常发生在任何模型调用落盘之前）"
        try:
            run_dir.mkdir(parents=True, exist_ok=True)
            usage_path.write_text(
                json.dumps(
                    {
                        "status": "unexpected-error",
                        "usage": [],
                        "totals": {"calls": 0},
                        "note": "异常发生在任何模型调用落盘之前；真实请求数以 journalctl 为准",
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        except OSError:
            pass
    store.save_cycle(
        cycle_id,
        status=CYCLE_FAILED,
        run_id=run_id,
        finished_at=now_iso(),
        notes=f"未捕获异常：{summary}；{usage_note}"[:500],
    )
    _print("")
    _print(f"[周任务] 未捕获异常，已停止：{summary}")
    _print(f"  周期 {cycle_id} 已记为 failed（不是 interrupted：异常在本进程里被捕获）。")
    _print(f"  诊断文件：{run_dir / 'weekly-error.txt'}（路径与疑似凭据已脱敏）")
    _print(f"  {usage_note}")
    _print("  没有发送邮件，也没有自动重跑模型。人工确认后用：")
    _print(f"    python -m learnradar.cli weekly --cycle {cycle_id} --force-redo")
    return EXIT_GATE_FAILED

def cmd_weekly(settings: Settings, args: argparse.Namespace) -> int:
    """提交唯一周期任务并同步等到处理/通知终态（systemd oneshot）。"""
    from .cli import _find_topic
    from .submission import queue_for, fixture_options
    from .tasks import TaskError
    from .worker import wait_for_task, task_exit_code

    try:
        schedule=schedule_config(settings,topic_id=args.topic or "")
        topic=_find_topic(settings,schedule.topic or None)
        moment=current_time(schedule)
        anchor=scheduled_instant(schedule,moment)
        cycle_id=args.cycle or cycle_id_for(anchor)
        _print(f"# Learning Radar 周任务（周期 {cycle_id}）")
        _print(f"  计划：每周 {schedule.weekday} {schedule.at}（{schedule.timezone}）；归属周期：{cycle_id}；最近一次计划时刻 {anchor.isoformat(timespec='minutes')}")
        queue=queue_for(settings)
        # 旧版本的锁若仍存在，不推测持有者已停止，也不与旧入口各跑一次。
        legacy_lock=settings.data_dir/"locks"/"weekly.lock"
        if legacy_lock.exists():
            _print("旧 weekly 锁仍存在：未创建分析；请确认旧执行者状态后人工处理，未返回执行成功。")
            return EXIT_GATE_FAILED
        with queue.store() as store:
            previous=store.get_cycle(cycle_id)
        from .discovery import request_options
        mode = 'focused' if args.topic else (getattr(args, 'discovery_mode', None) or settings.schedule.get('discovery_mode', 'focused'))
        task_topic, discovery = request_options(settings, mode=mode, topic=str(topic['id']))
        task=queue.submit_weekly(task_topic,cycle_id,force_redo=args.force_redo,options={**fixture_options(args), **discovery})
        if previous:
            _print(f"  该周期已经处理过/已接单：{previous['status']}；同周期复用持久任务，不重复分析或发送。")
        if task is None:
            state=str((previous or {}).get("status") or "")
            _print(f"  旧周期记录状态 {state}；没有队列关联，不猜测恢复。失败/中断/unknown 需人工 --force-redo；running 需先人工核对。")
            with queue.store() as store:
                email=store.latest_email_for_cycle(cycle_id) or {}
            return EXIT_OK if state==CYCLE_COMPLETED and email.get("status")=="sent" and email.get("kind")!="failure" else EXIT_GATE_FAILED
        _print(f"  任务 {task['task_id']} · run_id={task['run_id']}；同步等待，尚未完成时不返回成功。")
        _print(f"  等待总超时：{install_task_timeout(schedule.task_timeout_seconds)}")
        try:
            finished=wait_for_task(settings,queue,task["task_id"])
        except TaskTimeout as exc:
            _print(f"  等待超时：{exc}；由执行锁与保存结果判断状态，未据超时重跑或重发。")
            return EXIT_GATE_FAILED
        finally:
            clear_task_timeout()
        return task_exit_code(finished)
    except (ConfigError,ValueError,TaskError) as exc:
        _print(f"未执行：{exc}")
        return EXIT_USAGE


def _finish_weekly(
    settings: Settings,
    store: Store,
    schedule: Any,
    cycle_id: str,
    run_id: str,
    code: int,
    *, config_loader=None, sender=None, before_send=None,
) -> int:
    """收尾：写周期状态 → 渲染并保存待发邮件 → 发送一次（不自动重试）。"""
    outcome = collect_outcome(store, settings, run_id, cycle_id=cycle_id)
    cycle_status, email_kind = classify_cycle(
        outcome["run_status"], outcome["report_status"], outcome["decision"]
    )
    store.save_cycle(
        cycle_id,
        status=cycle_status,
        run_id=run_id,
        finished_at=now_iso(),
        notes=(
            f"run={run_id}；run_status={outcome['run_status'] or '-'}；"
            f"report_status={outcome['report_status'] or '-'}；每周任务退出码 {code}"
        ),
    )
    _print(
        f"\n[周任务] 周期状态 {cycle_status}；运行终态 "
        f"{outcome['run_status'] or outcome['report_status'] or '未知'}；邮件类型 {email_kind}"
    )
    try:
        config = (config_loader or load_email_config)(settings, require_secret=False)
    except ConfigError as exc:
        _print(f"  邮件未生成：{exc}")
        _print("  分析结果已保存；配好邮件配置后用 email-preview 查看，再决定是否发送。")
        return EXIT_GATE_FAILED
    attachment_path, attachment_text = write_email_attachment(settings, outcome)
    email = build_email(outcome, config, kind=email_kind, attachment_text=attachment_text)
    email_id = run_id
    store.create_email(
        email_id,
        cycle_id=cycle_id,
        run_id=run_id,
        kind=email["kind"],
        recipient=config.recipient,
        subject=email["subject"],
        body=email["body"],
        attachment_path=attachment_path,
        status="queued",
        now=now_iso(),
    )
    _print(f"  待发邮件已保存：{config.recipient}｜{email['subject']}")
    if email["attachments"]:
        _print(f"  附件：{email['attachments'][0][0]}（{len(email['attachments'][0][1])} 字符）")
    record = store.get_email(email_id) or {}
    if str(record.get("status") or "") == "sent":
        _print("  这封邮件此前已发送成功：不重复发送。")
        return EXIT_OK if cycle_status == CYCLE_COMPLETED else EXIT_GATE_FAILED
    if str(record.get("status") or "") == "unknown":
        _print("  通知结果不明，未自动重发；请人工核对。")
        return EXIT_GATE_FAILED
    try:
        (config_loader or load_email_config)(settings, require_secret=True)
    except ConfigError as exc:
        _print(f"  未发送：{exc}")
        _print(f"  配好凭据后单独重发：email-retry --run-id {run_id}（不会重新分析）")
        return EXIT_GATE_FAILED
    if before_send:
        before_send(store,email_id)
    status, detail = (sender or send_email)(
        config,
        subject=email["subject"],
        body=email["body"],
        attachments=email["attachments"],
    )
    store.update_email(
        email_id,
        status=status,
        error=detail[:400],
        now=now_iso(),
        sent_at=now_iso() if status == "sent" else "",
    )
    if status == "sent":
        _print("  邮件：SMTP 服务器已接受（这不等于你已收到；已接受的邮件不会自动重发）。")
    elif status == "unknown":
        _print(f"  邮件：发送结果不确定（{detail}）。默认不自动重发：先查收件箱，再决定是否 --force 重发。")
    else:
        _print(f"  邮件发送失败：{detail}")
        _print(f"  可单独重发（不会重新分析）：email-retry --run-id {run_id}")
    if status != "sent":
        return EXIT_GATE_FAILED
    if email["kind"] == "failure":
        # 失败通知可以发送成功，但进程仍然报“需要处理”，避免被当成正常一周
        return EXIT_GATE_FAILED
    return EXIT_OK if cycle_status == CYCLE_COMPLETED else EXIT_GATE_FAILED


def cmd_email_preview(settings: Settings, args: argparse.Namespace) -> int:
    """只读已保存结果，打印将要发送的邮件（不分析、不发送、不写库）。"""
    store = Store(settings.data_dir / "learning-radar.sqlite")
    try:
        run_id = args.run_id or ""
        if not run_id and args.cycle:
            cycle = store.get_cycle(args.cycle)
            run_id = str((cycle or {}).get("run_id") or "")
        if not run_id:
            run_id = _latest_run_id(store)
        if not run_id:
            _print("还没有可预览的运行记录；先跑一次 run 或 weekly。")
            return EXIT_USAGE
        outcome = collect_outcome(store, settings, run_id, cycle_id=args.cycle or "")
        if not outcome.get("found"):
            _print(f"找不到该运行的记录：{run_id}（不猜测内容，也不会去分析）")
            return EXIT_USAGE
        try:
            config = load_email_config(settings, require_secret=False)
        except ConfigError as exc:
            _print(f"（邮件配置不可用：{exc}）")
            config = EmailConfig(
                host="", port=0, security="starttls", sender="（未配置）", recipient="（未配置）"
            )
        attachment_text = sanitized_markdown(outcome)
        email = build_email(outcome, config, attachment_text=attachment_text)
        text = "\n".join([email["subject"], email["body"]] + [item[1] for item in email["attachments"]])
        findings = scan_sensitive(text)
        _print("# 邮件预览（只读已保存结果；未分析、未发送、未写库）")
        _print(f"  运行：{run_id}　周期：{outcome.get('cycle_id') or '（未标注）'}")
        _print(
            f"  运行终态：{outcome.get('run_status') or '-'}　"
            f"报告状态：{outcome.get('report_status') or '-'}　类型：{email['kind']}"
        )
        _print(f"  收件人：{config.recipient}　发件人：{config.sender}")
        _print(f"  主题：{email['subject']}")
        _print(
            f"  附件：{'、'.join(name for name, _ in email['attachments']) or '无'}"
            + (f"（{len(email['attachments'][0][1])} 字符，预览在内存里不落盘）" if email["attachments"] else "")
        )
        _print("")
        _print(email["body"])
        _print("")
        if findings:
            _print("敏感内容检查：发现问题（必须先修掉再发送）")
            for item in findings:
                _print(f"  - {item}")
            return EXIT_GATE_FAILED
        _print("敏感内容检查：未发现本机地址、磁盘路径、凭据字样或不可用链接。")
        return EXIT_OK
    finally:
        store.close()


def cmd_email_test(settings: Settings, args: argparse.Namespace) -> int:
    """只测 SMTP：不发报告内容、不调用模型、不占周期、不改报告状态。"""
    try:
        config = load_email_config(settings, require_secret=True)
    except ConfigError as exc:
        _print(f"无法测试：{exc}")
        return EXIT_USAGE
    stamp = now_iso()
    subject = f"{config.subject_prefix} SMTP 测试（{stamp}）"
    body = "\n".join([
        "这是一封 SMTP 连通性测试邮件（由 learning-radar 手动触发）。",
        f"发件人：{config.sender}",
        f"收件人：{config.recipient}",
        f"加密方式：{config.security}（证书校验开启）",
        f"时间：{stamp}",
        "",
        "它不含报告、档案或反馈内容，也不占用每周周期、不改动任何报告状态。",
        "收到说明 SMTP 配置可用；之后真正的周报才会带项目推荐。",
    ])
    email_id = "smtp-test-" + "".join(ch for ch in stamp if ch.isalnum())
    store = Store(settings.data_dir / "learning-radar.sqlite")
    try:
        store.create_email(
            email_id,
            cycle_id="",
            run_id="",
            kind="test",
            recipient=config.recipient,
            subject=subject,
            body=body,
            attachment_path="",
            status="queued",
            now=stamp,
        )
        status, detail = send_email(config, subject=subject, body=body)
        store.update_email(
            email_id,
            status=status,
            error=detail[:400],
            now=now_iso(),
            sent_at=now_iso() if status == "sent" else "",
        )
        _print(f"SMTP 测试结果：{status} {detail}".strip())
        if status == "sent":
            _print("  服务器已接受：请到收件箱确认（这不等于已收到）。测试邮件不占用周期记录。")
        elif status == "unknown":
            _print("  结果不确定：默认不重发，先查收件箱。")
        return EXIT_OK if status == "sent" else EXIT_GATE_FAILED
    finally:
        store.close()

def cmd_email_retry(settings: Settings, args: argparse.Namespace) -> int:
    """重发已保存的邮件：不重新分析、不调用模型。"""
    return _send_saved_or_new(settings, args, retry=True)


def cmd_email_send(settings: Settings, args: argparse.Namespace) -> int:
    """给一个已保存的运行手动发送邮件：不启动分析、不占 weekly 周期。

    复用现有的邮件渲染（含隐私过滤：附件只由允许对外发送的字段渲染）、SMTP 配置、
    `emails` 表与 email-retry。已发布报告发推荐邮件；被拒/渲染失败只发不含草稿的失败通知。
    """
    return _send_saved_or_new(settings, args, retry=False)


def _send_saved_or_new(
    settings: Settings, args: argparse.Namespace, *, retry: bool,
    config_loader=None, sender=None, before_send=None,
) -> int:
    """两个入口共用：retry=True 只重发已保存邮件；retry=False 从已保存运行现渲染一封。"""
    store = Store(settings.data_dir / "learning-radar.sqlite")
    try:
        outcome: dict[str, Any] = {}
        if retry:
            record = _find_saved_email(store, args)
            if record is None:
                _print("没有找到已保存的邮件；用 email-preview 或 cycles 确认运行 ID。")
                return EXIT_USAGE
            email_id = str(record.get("email_id") or "")
            run_id = str(record.get("run_id") or "")
            subject = str(record.get("subject") or "")
            body = str(record.get("body") or "")
            attachment_path = str(record.get("attachment_path") or "")
            intro = "重发结果"
        else:
            run_id = str(getattr(args, "run_id", "") or "")
            outcome = collect_outcome(store, settings, run_id)
            if not outcome.get("found"):
                _print(f"找不到该运行的记录：{run_id}（不猜测内容，也不会去分析）")
                return EXIT_USAGE
            email_id = run_id
            subject = ""
            body = ""
            attachment_path = ""
            intro = "发送结果"
            record = store.get_email(email_id) or store.latest_email_for_run(run_id)

        status = str((record or {}).get("status") or "")
        if status == "sent":
            _print("这个运行已经发送成功过：不重复发送（是否收到请直接查收件箱）。")
            return EXIT_OK
        if status == "unknown" and not args.force:
            _print(
                "上一次发送结果不确定（连接中断/超时）：默认不重发。"
                "确认收件箱里没有这封邮件后，再加 --force。"
            )
            return EXIT_GATE_FAILED
        try:
            config = (config_loader or load_email_config)(settings)
        except ConfigError as exc:
            _print(f"无法发送：{exc}")
            return EXIT_USAGE

        if not retry:
            # 从已保存的结构化结果现渲染：已发布→推荐邮件；被拒/渲染失败→不含草稿的失败通知
            email = build_email(outcome, config)
            attachment_path = ""
            if email["kind"] == KIND_REPORT:
                # 只有推荐邮件带附件；失败/状态邮件不生成、也不留附件文件
                attachment_path, attachment_text = write_email_attachment(settings, outcome)
                email = build_email(outcome, config, attachment_text=attachment_text)
            subject = email["subject"]
            body = email["body"]
            store.create_email(
                email_id,
                cycle_id=str(getattr(args,"cycle","") or ""),
                run_id=run_id,
                kind=email["kind"],
                recipient=config.recipient,
                subject=subject,
                body=body,
                attachment_path=attachment_path,
                status="queued",
                now=now_iso(),
                replace=True,
            )
            _print("# 手动发送邮件（不启动分析、不占 weekly 周期）")
            _print(
                f"  运行：{run_id}　终态：{outcome.get('report_status') or outcome.get('run_status') or '-'}"
                f"　邮件类型：{email['kind']}"
            )
            _print(f"  收件人：{config.recipient}｜主题：{subject}")
            if email["attachments"]:
                _print(
                    f"  附件：{email['attachments'][0][0]}（{len(email['attachments'][0][1])} 字符；"
                    "只含允许对外发送的字段）"
                )
            if str((store.get_email(email_id) or {}).get("status") or "") == "sent":
                _print("  这封邮件已经发送成功：不重复发送。")
                return EXIT_OK

        attachments: list[tuple[str, str]] = []
        path = Path(attachment_path)
        if config.attach_markdown and path.is_file():
            attachments.append((f"report-{run_id}.md", path.read_text(encoding="utf-8")))
        if before_send:
            before_send(store, email_id)
        send_status, detail = (sender or send_email)(
            config,
            subject=subject,
            body=body,
            attachments=attachments,
        )
        store.update_email(
            email_id,
            status=send_status,
            error=detail[:400],
            now=now_iso(),
            sent_at=now_iso() if send_status == "sent" else "",
        )
        if send_status == "sent":
            _print(f"{intro}：sent（SMTP 服务器已接受；这不等于你已收到，已接受的邮件不会自动重发）")
            return EXIT_OK
        if send_status == "unknown":
            _print(f"{intro}：unknown（{detail}）")
            _print("  默认不自动重发：先查收件箱，确认没有这封邮件后再加 --force。")
            return EXIT_GATE_FAILED
        _print(f"{intro}：failed（{detail}）")
        _print("  已保留失败原因；可单独重试（不会重新分析）。")
        return EXIT_GATE_FAILED
    finally:
        store.close()


def _find_saved_email(store: Store, args: argparse.Namespace) -> dict[str, Any] | None:
    """按 --email-id / --run-id / --cycle 找一封已保存的邮件。"""
    if getattr(args, "email_id", ""):
        return store.get_email(args.email_id)
    if getattr(args, "run_id", ""):
        return store.latest_email_for_run(args.run_id)
    if getattr(args, "cycle", ""):
        return store.latest_email_for_cycle(args.cycle)
    return None


def _cycle_usage_summary(settings: Settings, cycle_id: str) -> tuple[bool, str]:
    """该周期有没有已经落盘的模型用量。有调用记录时要先人工确认，不自动改状态。"""
    run_root = settings.data_dir / "runs"
    found: list[str] = []
    total_calls = 0
    for path in sorted(run_root.glob(f"{cycle_id}-*/usage.json")):
        found.append(str(path))
        try:
            totals = json.loads(path.read_text(encoding="utf-8")).get("totals") or {}
            total_calls += int(totals.get("calls") or 0)
        except (OSError, json.JSONDecodeError, ValueError):
            total_calls += 0
    if not found:
        return True, "该周期没有用量文件"
    if total_calls == 0:
        return True, f"用量文件存在但调用数为 0（{len(found)} 个）"
    return False, f"已有 {total_calls} 次模型调用记录（{len(found)} 个用量文件）：先确认是否要保留"


def cmd_cycle_recover(settings: Settings, args: argparse.Namespace) -> int:
    """一次性恢复：把卡在 running 的周期明确记为 interrupted（先做条件检查 + 一致性备份）。"""
    cycle_id = args.cycle
    store = Store(settings.data_dir / "learning-radar.sqlite")
    try:
        row = store.get_cycle(cycle_id)
        if not row:
            _print(f"没有找到周期 {cycle_id}；用 cycles 看一下现有记录。")
            return EXIT_USAGE
        status = str(row.get("status") or "")
        run_id = str(row.get("run_id") or "")
        lock = TaskLock(settings.data_dir / "locks" / "weekly.lock")
        alive = lock.holder_alive()
        usage_ok, usage_detail = _cycle_usage_summary(settings, cycle_id)
        email = store.latest_email_for_cycle(cycle_id) or {}
        email_status = str(email.get("status") or "")
        checks = [
            ("锁没有被活着的进程持有", not alive, f"锁文件{'存在' if lock.path.exists() else '不存在'}；持有进程存活：{'是' if alive else '否'}"),
            ("周期上没有 run_id", run_id in ("", "-"), f"run_id={run_id or '-'}"),
            ("没有已发送或结果不明的邮件", email_status not in {"sent", "unknown"}, f"邮件状态={email_status or '-'}"),
            ("没有需要保留的模型用量", usage_ok, usage_detail),
            (f"当前状态是 running（实际：{status}）", status == CYCLE_RUNNING, ""),
        ]
        for label, ok, detail in checks:
            mark = "OK  " if ok else "不满足"
            _print(f"  [{mark}] {label}" + (f"；{detail}" if detail else ""))
        if not all(ok for _label, ok, _detail in checks):
            _print("有检查项不满足：不做任何修改。")
            _print("  请先确认：systemctl is-active learnradar-weekly.service 是 inactive、没有 weekly 在跑、")
            _print("  该周期确实没有 run_id/用量/邮件。")
            return EXIT_GATE_FAILED
        backup_path = Path(
            args.backup_out
            or settings.data_dir / "backups" / f"{cycle_id}-before-recover.sqlite"
        ).expanduser()
        backup_database(settings.data_dir / "learning-radar.sqlite", backup_path)
        _print(f"  已做一致性备份：{backup_path}")
        store.save_cycle(
            cycle_id,
            status=CYCLE_INTERRUPTED,
            notes=(
                f"人工确认后用 cycle-recover 标为 interrupted（{now_iso()}）："
                "占位后没有 run_id/用量/已发送邮件，服务已确认停止"
            ),
        )
        _print(f"  已把 {cycle_id} 记为 interrupted。")
        _print("  下一步（会真实分析并占用该周期）：")
        _print(f"    python -m learnradar.cli weekly --cycle {cycle_id} --force-redo")
        return EXIT_OK
    finally:
        store.close()

def cmd_cycles(settings: Settings, args: argparse.Namespace) -> int:
    store = Store(settings.data_dir / "learning-radar.sqlite")
    try:
        rows = store.list_cycles(limit=args.limit)
        if not rows:
            _print("还没有周期记录；weekly 跑过之后这里会显示。")
            return EXIT_OK
        _print(f"{'周期':<12} {'状态':<12} {'运行 ID':<34} {'邮件':<9} 主题")
        for row in rows:
            email = store.latest_email_for_cycle(str(row["cycle_id"])) or {}
            _print(
                f"{row['cycle_id']:<12} {row['status']:<12} {(row['run_id'] or '-'):<34}"
                f" {(email.get('status') or '-'):<9} {row.get('topic') or '-'}"
            )
        return EXIT_OK
    finally:
        store.close()


def cmd_backup(settings: Settings, args: argparse.Namespace) -> int:
    # The online API guarantees the DB snapshot; the accompanying files also
    # require a quiescent executor. Web/manual writers must be stopped separately.
    if getattr(args, 'bundle_dir', None):
        from .tasks import WorkerLock
        lock = WorkerLock(settings.data_dir)
        if not lock.acquire():
            _print('文件包备份未执行：统一执行进程仍持锁；先暂停入口并停止空闲 worker，不删除锁文件。')
            return EXIT_USAGE
        try:
            if (settings.data_dir/'locks'/'weekly.lock').exists():
                _print('文件包备份未执行：旧 weekly 锁仍存在，需人工核对旧执行者。')
                return EXIT_USAGE
            return _backup_quiescent(settings, args)
        finally:
            lock.release()
    return _backup_quiescent(settings, args)


def _backup_quiescent(settings: Settings, args: argparse.Namespace) -> int:
    source = settings.data_dir / "learning-radar.sqlite"
    if not source.exists():
        _print(f"没有数据库可备份：{source}")
        return EXIT_USAGE
    target = Path(args.out).expanduser()
    bundle_dir = str(getattr(args, 'bundle_dir', '') or '')
    if bundle_dir:
        from .bundle_manifest import inventory, verify_inventory
        bundle = Path(bundle_dir).expanduser()
        if bundle.exists() and (not bundle.is_dir() or any(bundle.iterdir())):
            _print('文件包备份未执行：目标非空；请使用新的备份目录，不覆盖旧备份。')
            return EXIT_USAGE
        try:
            file_manifest = inventory(settings.data_dir)
        except (OSError, ValueError):
            _print('文件包备份未执行：无法读取完整的 runs/reports 普通文件清单。')
            return EXIT_USAGE
    backup_database(source, target)
    _print(f"一致性备份已写出：{target}")
    _print("  用 SQLite 在线备份 API（不是直接复制文件），运行中也保证快照一致。")
    if bundle_dir:
        bundle.mkdir(parents=True, exist_ok=True)
        try:
            for name in file_manifest['included']:
                shutil.copytree(settings.data_dir / name, bundle / name, symlinks=True)
            verify_inventory(settings.data_dir, file_manifest)
            verify_inventory(bundle, file_manifest)
        except (OSError, ValueError):
            _print('文件包备份失败：源文件变化、缺失或复制后完整性不一致；未写成功清单。')
            return EXIT_USAGE
        included = file_manifest['included']
        manifest = {
            **file_manifest,
            "source_data_dir": str(settings.data_dir.resolve()),
            "created_at": now_iso(),
            "database": str(target),
            "database_sha256": _sha256(target),
            "needed_by": {
                "runs/reports 表 + data/runs/": "历史与冷却（run-context.json）、周期、邮件状态",
                "data/reports/*.md": "报告查看与邮件附件",
                "data/runs/<id>/evidence.json": "未发布草稿页的正文",
                "feedback 表": "反馈（在选择与查看里读取）",
                "tasks/task_requests/task_events/task_environment 表": "队列、持久额度、请求不确定性与隔离模式",
                "web_account/web_sessions 表": "账号校验值与会话：私密备份，恢复后必须撤销会话",
            },
            "excluded": [
                "config/profile.toml（真实档案：单独复制）",
                "/etc/learning-radar/secret.env（密钥：单独保管，不进普通备份包）",
                "logs/、out/、backups/",
            ],
        }
        (bundle / "MANIFEST.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        _print(f"  文件包已写出：{bundle}")
        _print(
            f"    包含 reports {included.get('reports', 0)} 个文件、runs {included.get('runs', 0)} 个文件；"
            "清单见 MANIFEST.json"
        )
        _print("  备份含账号校验值、反馈和私密产物，必须受限保管；档案与密钥单独保管。")
        _print('  已自动核对所有已保存文件的相对路径、大小及 SHA-256；无需逐个手工核对。')
    _print("  请把备份再复制到服务器之外的介质；这一步不由本命令代做。")
    return EXIT_OK
