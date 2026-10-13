"""本地报告页面：Markdown 报告展示 + 页内反馈，绑定 127.0.0.1。

页面由 Markdown 报告渲染，保证工作台与导出内容一致；
未发布的运行保留“未发布/检查未通过”状态并展示未发布草稿，结构损坏到无法渲染正文的运行只显示诊断。
"""

from __future__ import annotations

import html as html_lib
import json
import re
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit, urlencode

from .evidence import Evidence, normalize_repo_name
from .feedback import CHOICES, ITEMS, clean_answers, clean_note
from .net import now_iso
from .report import render_markdown
from .store import Store
from .validate import REQUIRED_TOP_LEVEL, ValidationResult
from .browse import history_records

STYLE = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body { margin: 0; padding: 16px; font-family: -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif;
       line-height: 1.6; color: #1c1c1e; background: #f7f7f8; }
main { max-width: 900px; margin: 0 auto; }
body { overflow-wrap: anywhere; }
main, .card, form, fieldset { min-width: 0; max-width: 100%; }
nav { display:flex; gap:20px; flex-wrap:wrap; margin-bottom:20px; }
nav a { padding:8px 0; text-decoration:none; font-weight:600; }
button, select, textarea { font:inherit; max-width:100%; }
button { min-height:44px; padding:8px 16px; border:1px solid #b8cce4; border-radius:8px; cursor:pointer; background:#eef4ff; color:#174778; }
select { min-height:44px; width:100%; padding:6px; }
textarea { padding:8px; border:1px solid #aaa; border-radius:6px; }
fieldset { min-inline-size:0; }
fieldset label { display:inline-flex; align-items:center; min-height:44px; }
.submit-form { display:flex; flex-wrap:wrap; align-items:end; gap:12px; }
.submit-form label { flex:1 1 240px; }
details { margin:10px 0; }
summary { cursor:pointer; min-height:36px; color:#59616b; }
pre { max-width:100%; overflow-x:auto; white-space:pre; padding:14px; background:#ececf0; border-radius:6px; overflow-wrap:normal; }
pre code { padding:0; white-space:pre; }
h1 { font-size: 1.4rem; line-height: 1.35; }
h2 { font-size: 1.1rem; margin-top: 1.6em; border-bottom: 1px solid #e3e3e6; padding-bottom: 4px; }
h3, h4 { font-size: 1rem; }
a { color: #0b62d0; word-break: break-all; }
table { width: 100%; border-collapse: collapse; margin: 12px 0; font-size: 0.9rem; display: block;
        overflow-x: auto; max-width:100%; }
th, td { border: 1px solid #dcdce0; padding: 6px 8px; text-align: left; vertical-align: top; }
th { background: #efeff2; white-space: nowrap; }
code { background: #ececf0; padding: 1px 4px; border-radius: 4px; font-size: 0.88em; }
blockquote { margin: 8px 0; padding: 8px 12px; background: #eef4ff; border-left: 3px solid #7aa7e0; }
ul { padding-left: 1.2em; }
.card { background: #fff; border: 1px solid #e3e3e6; border-radius: 10px; padding: 14px; margin: 12px 0; }
.tag { display: inline-block; font-size: 0.78rem; padding: 1px 7px; border-radius: 999px;
       background: #e6f0e6; color: #256029; margin-right: 6px; }
.tag.rejected { background: #fde8e8; color: #8b1d1d; }
.tag.fixture { background: #fdf0d5; color: #8a5a06; }
.muted { color: #6a6a72; font-size: 0.86rem; }
@media (max-width: 600px) { body { padding: 10px; } h1 { font-size: 1.22rem; } }
"""


def escape(text: Any) -> str:
    return html_lib.escape(str(text), quote=True)


def _inline(text: str) -> str:
    escaped = escape(text)
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    escaped = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r'<a href="\2">\1</a>', escaped)
    escaped = re.sub(r"(?<![\"'=])(https?://[^\s<>\"]+)", r'<a href="\1">\1</a>', escaped)
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)
    return escaped


def markdown_to_html(markdown_text: str) -> str:
    """极简 Markdown 子集渲染：标题、表格、列表、引用、分隔线、段落、行内代码/链接/加粗。"""
    out: list[str] = []
    lines = markdown_text.splitlines()
    index = 0
    paragraph: list[str] = []

    def flush_paragraph() -> None:
        if paragraph:
            out.append("<p>" + "<br>".join(_inline(line) for line in paragraph) + "</p>")
            paragraph.clear()

    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if not stripped:
            flush_paragraph()
            index += 1
            continue
        if stripped == "---":
            flush_paragraph()
            out.append("<hr>")
            index += 1
            continue
        if stripped.startswith("```"):
            flush_paragraph()
            index+=1
            code=[]
            while index<len(lines) and not lines[index].strip().startswith("```"):
                code.append(lines[index]);index+=1
            index+=1
            out.append('<pre tabindex="0"><code>'+escape("\n".join(code))+'</code></pre>')
            continue
        heading = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if heading:
            flush_paragraph()
            level = len(heading.group(1))
            out.append(f"<h{level}>{_inline(heading.group(2))}</h{level}>")
            index += 1
            continue
        if stripped.startswith(">"):
            flush_paragraph()
            quoted: list[str] = []
            while index < len(lines) and lines[index].strip().startswith(">"):
                quoted.append(lines[index].strip().lstrip(">").strip())
                index += 1
            out.append("<blockquote>" + "<br>".join(_inline(item) for item in quoted) + "</blockquote>")
            continue
        if stripped.startswith("|") and index + 1 < len(lines) and re.match(r"^\|[\s:|-]+\|$", lines[index + 1].strip()):
            flush_paragraph()
            header = [cell.strip() for cell in stripped.strip("|").split("|")]
            index += 2
            rows: list[list[str]] = []
            while index < len(lines) and lines[index].strip().startswith("|"):
                rows.append([cell.strip() for cell in lines[index].strip().strip("|").split("|")])
                index += 1
            html_rows = ["<table><thead><tr>" + "".join(f"<th>{_inline(c)}</th>" for c in header) + "</tr></thead><tbody>"]
            for row in rows:
                html_rows.append("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in row) + "</tr>")
            html_rows.append("</tbody></table>")
            out.append("".join(html_rows))
            continue
        bullet = re.match(r"^(\s*)[-*]\s+(.*)$", line)
        if bullet:
            flush_paragraph()
            items: list[str] = []
            while index < len(lines):
                match = re.match(r"^(\s*)[-*]\s+(.*)$", lines[index])
                if not match:
                    break
                items.append(_inline(match.group(2)))
                index += 1
            out.append("<ul>" + "".join(f"<li>{item}</li>" for item in items) + "</ul>")
            continue
        paragraph.append(stripped)
        index += 1
    flush_paragraph()
    return "\n".join(out)


def page(title: str, body: str, *, kind=None, data=None, fixture=False) -> str:
    from .ui_shell import shell
    kind = kind or {'登录':'login', '登录暂不可用':'login', '请求被拒绝':'protection_error'}.get(title, 'message')
    return shell(title, body, STYLE, kind=kind, data=data, fixture=fixture)


def render_index(rows: list[dict[str, Any]]) -> str:
    parts = [
        "<h1>learning-radar 报告</h1>",
        "<p class=\"muted\">增量 A：真实证据 -> 项目对比 -> 可读报告。仅本机可访问。</p>",
    ]
    if not rows:
        parts.append("<div class=\"card\">还没有报告。先运行 <code>run</code> 命令。</div>")
    for row in rows:
        tags = f"<span class=\"tag{' rejected' if row['status'] != 'published' else ''}\">{escape(report_label(row['status']))}</span>"
        model = row.get("model_id") or "未知模型"
        parts.append(
            f'<div class="card" data-lr-role="report" data-run-id="{escape(row["run_id"])}">'
            f"{tags}<strong><a href=\"/report/{escape(row['run_id'])}\">{escape(row['title'] or row['run_id'])}</a></strong>"
            f"<div class=\"muted\">{escape(row['created_at'])} ｜ 模型 {escape(model)}</div>"
            "</div>"
        )
    from .ui_projection import home_data
    return page("learning-radar 报告", "\n".join(parts), kind='home',
                data={**home_data([], rows), 'submit': {'available':False}})


def _load_json(raw: Any) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _load_validation(row: dict[str, Any]) -> ValidationResult:
    payload = _load_json(row.get("validation"))
    return ValidationResult(
        ok=bool(payload.get("ok")),
        errors=[str(item) for item in payload.get("errors") or []],
        warnings=[str(item) for item in payload.get("warnings") or []],
        rules_version=str(payload.get("rules_version") or ""),
        content_review=str(payload.get("content_review") or "not_performed"),
    )


def _displayable(payload: dict[str, Any]) -> bool:
    """结构损坏到无法展示正文时不要假装有报告。"""
    if not payload or payload.get("error"):
        return False
    return all(key in payload for key in REQUIRED_TOP_LEVEL)


def _load_evidence(store: Store, run_id: str) -> list[Evidence]:
    path = Path(store.path).parent / "runs" / run_id / "evidence.json"
    if not path.exists():
        return []
    try:
        return [Evidence(**item) for item in json.loads(path.read_text(encoding="utf-8"))]
    except (TypeError, ValueError, json.JSONDecodeError):
        return []


def _repo_for_run(store: Store, row: dict[str, Any]) -> str:
    payload = _load_json(row.get("payload"))
    target = payload.get("target")
    repo = normalize_repo_name(target.get("repo")) if isinstance(target, dict) else ""
    if repo:
        return repo
    context = Path(store.path).parent / "runs" / str(row.get("run_id") or "") / "run-context.json"
    if not context.exists():
        return ""
    try:
        return normalize_repo_name(json.loads(context.read_text(encoding="utf-8")).get("target"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return ""


def _preview_meta(row: dict[str, Any], status: str, validation: ValidationResult) -> dict[str, Any]:
    return {
        "运行 ID": row.get("run_id"),
        "状态": f"未发布（{status}）",
        "生成时间": row.get("created_at"),
        "规则版本": validation.rules_version,
        "说明": "这是从已保存的模型原始输出重新渲染的未发布草稿：没有人工润色，也没有补证据，不代表内容已核验。",
    }


def build_report_view(store: Store, run_id: str) -> dict[str, Any] | None:
    """页面视图三选一：已发布正文 / 未发布草稿 / 只有诊断。"""
    row = store.get_report(run_id)
    if not row:
        return None
    status = str(row.get("status") or "")
    validation = _load_validation(row)
    payload = _load_json(row.get("payload"))
    md_path = Path(str(row.get("markdown_path") or ""))
    stored = ""
    if str(row.get("markdown_path") or "").strip() and md_path.is_file():
        stored = md_path.read_text(encoding="utf-8")
    view: dict[str, Any] = {
        "row": row,
        "status": status,
        "validation": validation,
        "repo": _repo_for_run(store, row),
        "feedback": store.get_feedback(run_id),
        "task": store.conn.execute("SELECT task_id,status FROM tasks WHERE run_id=?",(run_id,)).fetchone(),
        "email": store.latest_email_for_run(run_id),
    }
    if status == "published" and stored.strip():
        view.update(document=stored, document_kind="published", can_feedback=True)
        return view
    if _displayable(payload):
        try:
            draft = render_markdown(
                report=payload,
                evidence=_load_evidence(store, run_id),
                meta=_preview_meta(row, status, validation),
                validation=validation,
                stage_usage=[],
            )
        except Exception:  # pragma: no cover - 任何渲染异常都退回诊断视图
            draft = ""
        if draft.strip():
            view.update(document=draft, document_kind="draft", can_feedback=True)
            return view
    view.update(
        document=stored or "（该运行没有可展示的报告正文：结构损坏或渲染失败，只保留诊断。）",
        document_kind="diagnostics",
        can_feedback=False,
    )
    return view


def _status_card(view: dict[str, Any]) -> str:
    validation: ValidationResult = view["validation"]
    status = str(view["status"])
    label_text = {
        "rejected": "未发布：结构检查未通过",
        "render-failed": "未发布：渲染失败",
    }.get(status, f"未发布（{status}）")
    parts = [
        '<div class="card">',
        f'<span class="tag rejected">{escape(label_text)}</span>',
        f'<div class="muted">规则版本 {escape(validation.rules_version or "未知")}｜错误 {len(validation.errors)} 条｜'
        f"警告 {len(validation.warnings)} 条｜内容复核：未完成</div>",
    ]
    if view["document_kind"] == "draft":
        parts.append(
            '<div class="muted">下面是从已保存的模型原始输出重新渲染的<strong>未发布草稿</strong>：'
            "没有人工润色，也没有补证据。</div>"
        )
    else:
        parts.append('<div class="muted">本次没有可展示的报告正文，只显示诊断信息。</div>')
    if validation.errors:
        parts.append("<ul>" + "".join(f"<li>{escape(item)}</li>" for item in validation.errors) + "</ul>")
    if validation.warnings:
        parts.append("<ul>" + "".join(f"<li>警告：{escape(item)}</li>" for item in validation.warnings) + "</ul>")
    if view["can_feedback"]:
        parts.append('<div class="muted"><a href="#feedback">跳到反馈</a></div>')
    parts.append("</div>")
    return "\n".join(parts)


def render_feedback_section(view: dict[str, Any]) -> str:
    row = view["row"]
    run_id = str(row.get("run_id") or "")
    if not view["can_feedback"]:
        return (
            '<h2 id="feedback">你的反馈</h2>'
            '<div class="card muted">该运行没有可展示的报告正文（结构损坏或渲染失败），'
            "因此不提供反馈表单。</div>"
        )
    feedback = view["feedback"] or {}
    saved = str(feedback.get("updated_at") or "")
    parts = [
        '<h2 id="feedback">你的反馈</h2>',
        '<div class="card">',
        f'<div class="muted">绑定项目：{escape(view["repo"] or "（未能确定项目）")}｜运行 {escape(run_id)}｜'
        + (f"最近保存：{escape(saved)}" if saved else "尚未填写")
        + "</div>",
        f'<form data-lr-action="feedback" method="post" action="/feedback/{escape(run_id)}">',
    ]
    for key, text in ITEMS:
        current = str(feedback.get(key) or "")
        parts.append(f'<fieldset style="border:0;padding:0;margin:14px 0"><legend><strong>{escape(text)}</strong></legend>')
        for code, choice_text in (("", "未填写"), *CHOICES):
            checked = " checked" if current == code else ""
            parts.append(
                f'<label style="margin-right:14px"><input type="radio" name="{escape(key)}"'
                f' value="{escape(code)}"{checked}> {escape(choice_text)}</label>'
            )
        parts.append("</fieldset>")
    note = str(feedback.get("note") or "")
    parts.append(
        '<label>可选备注（不想解释原因可以留空）<br>'
        f'<textarea name="note" rows="3" style="width:100%">{escape(note)}</textarea></label>'
    )
    parts.append(
        '<p><button type="submit">保存反馈</button> '
        '<span class="muted">重复提交只覆盖同一条记录，不会新增；只保存本次反馈，不推断长期偏好。</span></p>'
    )
    parts.append("</form>")
    parts.append("</div>")
    return "\n".join(parts)


def render_report(view: dict[str, Any], *, saved: bool = False) -> str:
    row = view["row"]
    status = str(view["status"])
    body = ['<p><a href="/">← 返回列表</a></p>']
    task=view.get("task")
    email=view.get("email") or {}
    body.append(f'<div class="card">报告：{escape(report_label(status))} · 任务：{escape(TASK_STATES.get(task["status"],task["status"]) if task else "旧记录未保存任务状态")} · 通知：{escape(notification_label(email.get("status")))}</div>')
    if email.get("status")=="unknown": body.append('<p>发送结果不明：请人工核对，不自动重发。</p>')
    if saved:
        feedback = view["feedback"] or {}
        body.append(
            '<div class="card"><strong>反馈已保存</strong>'
            f'<div class="muted">{escape(feedback.get("updated_at") or "")}</div></div>'
        )
    if status != "published":
        body.append(_status_card(view))
    body.append('<article data-lr-role="report-body" data-run-id="'+escape(row['run_id'])+'">'+markdown_to_html(str(view["document"]))+'</article>')
    body.append(render_feedback_section(view))
    from .ui_projection import report_data
    return page(row.get("title") or row.get("run_id") or "报告", "\n".join(body),
                kind='report', data=report_data(view,saved=saved))


PHASES = {"queued":"排队","search":"搜索","screening":"初筛","evidence":"取证",
          "analysis":"分析","check":"检查","archive":"归档","notification":"通知"}
TASK_STATES = {"queued":"排队等待","running":"正在处理","waiting_quota":"等待当天额度",
               "completed":"处理完成","failed":"处理失败","interrupted":"中断，需人工处理","cancelled":"已取消"}
RUN_STATES={"running":"正在执行","failed":"处理失败","model-failed":"模型阶段失败",
            "screening-failed":"初筛失败","screening-ok":"初筛完成（无报告）","no-evidence":"证据不足",
            "published":"处理完成","rejected":"处理完成","render-failed":"报告渲染失败","interrupted":"执行中断",
            "all-candidates-in-cooldown":"候选均处于冷却期","no-suitable-candidate":"没有合适候选",
            "insufficient-comparators":"对照项目不足"}


def report_label(value):
    return {"published":"已发布","rejected":"未通过检查（未发布）","render-failed":"渲染失败（未发布）"}.get(value,value or "无报告")


def notification_label(value):
    return {"sent":"已发送","failed":"发送失败","unknown":"发送结果不明","queued":"等待发送"}.get(value,value or "尚未通知")


def history_link(topic=None,project=None):
    fields={}
    if topic is not None: fields["topic"]=topic
    if project is not None: fields["project"]=project
    return "/history"+("?"+urlencode(fields) if fields else "")


def render_history(records,topics,query,demo=False):
    labels={str(t["id"]):t.get("label") or t["id"] for t in topics}
    labels[""]="未分类"
    topic=(query.get("topic") or [None])[0]
    project=(query.get("project") or [None])[0]
    body=[demo_banner(demo),"<h1>历史</h1>",'<p class="muted">主题 → 项目 → 历次报告。归属来自保存记录；缺失归属列为未分类，反馈不推断长期偏好。</p>']
    if topic is None:
        for key,label in labels.items():
            count=sum(r["topic"]==key for r in records)
            body.append(f'<div class="card"><a href="{escape(history_link(key))}">{escape(label)}</a> · {count} 条记录</div>')
    elif project is None:
        body.append(f'<h2>{escape(labels.get(topic,"未分类"))} · 项目</h2>')
        rows=[r for r in records if r["topic"]==topic]
        for name in dict.fromkeys(r["project"] for r in rows):
            count=sum(r["project"]==name for r in rows)
            body.append(f'<div class="card"><a href="{escape(history_link(topic,name))}">{escape(name or "未分类")}</a> · {count} 条记录</div>')
        if not rows: body.append('<p>该主题尚无保存记录。</p>')
    else:
        body.append(f'<p><a href="{escape(history_link(topic))}">← 项目列表</a></p><h2>{escape(project or "未分类")} · 历次报告</h2>')
        rows=[r for r in records if r["topic"]==topic and r["project"]==project]
        for row in rows:
            title=escape(row["title"] or "保存的运行记录")
            if row["report_id"]:
                title=f'<a href="/report/{escape(row["run_id"])}">{title}</a>'
            elif row["task_id"]:
                title=f'<a href="/task/{escape(row["task_id"])}">{title}</a>'
            processing=TASK_STATES.get(row["task_status"]) or RUN_STATES.get(row["run_status"],"旧记录未保存处理状态")
            body.append(f'<div class="card"><strong>{title}</strong><p>{escape(row["date"])} · {escape(report_label(row["report_status"]))}</p><p>任务：{escape(processing)} · 通知：{escape(notification_label(row["email_status"]))}</p>')
            if row["email_status"]=="unknown": body.append('<p>请人工核对，不自动重发。</p>')
            if not row["report_id"] and row["notes"]: body.append(f'<p>{escape(row["notes"])}</p>')
            if row["feedback_at"]:
                choices=dict(CHOICES)
                body.append('<p>已有反馈：'+"；".join(f'{escape(label)}：{escape(choices.get(row[key],"未填写"))}' for key,label in ITEMS)+f'</p><p>{escape(row["note"] or "")}</p>')
            else: body.append('<p class="muted">尚无反馈</p>')
            body.append(f'<details><summary>技术详情</summary>运行编号：{escape(row["run_id"])}；运行状态：{escape(row["run_status"] or "未记录")}</details></div>')
        if not rows: body.append('<p>没有保存记录。</p>')
    from .ui_projection import report_summary, MAX_ITEMS, text
    return page("Learning Radar 历史","\n".join(body), kind='legacy_history',
                data={'reports':[report_summary(r) for r in rows[:MAX_ITEMS]] if topic is not None else [],
                      'topic':text(topic,80),'project':text(project,200)})


def demo_banner(demo):
    return '<div class="card"><strong>演示模式 · fixture</strong>：示例数据、假模型、假 SMTP，未实测，不发送真实邮件。</div>' if demo else ''


def render_task_index(settings, queue, reports):
    options="".join(f'<option value="{escape(t["id"])}">{escape(t.get("label") or t["id"])}</option>' for t in settings.topics)
    body=["<h1>Learning Radar</h1>",demo_banner(queue.demo),
          '<div class="card"><h2>发起一次推荐</h2><form data-lr-action="submit" class="submit-form" method="post" action="/tasks">',
          f'<input type="hidden" name="submission_key" value="{uuid.uuid4().hex}">',
          '<input type="hidden" name="discovery_mode" value="broad"><button type="submit">帮我发现新项目</button></form>',
          '<details><summary>指定方向</summary><form data-lr-action="submit" method="post" action="/tasks">',
          f'<input type="hidden" name="submission_key" value="{uuid.uuid4().hex}"><input type="hidden" name="discovery_mode" value="focused">',
          f'<label>方向 <select name="topic">{options}</select></label> <button type="submit">按指定方向发现</button></form></details>',
          '<p class="muted">每天最多 3 个进入模型请求的手动任务，单任务最多 4 次请求（失败与重试计入）。提交先预留；关闭页面后执行进程继续。</p></div>',
          '<h2>正在处理与排队</h2>']
    tasks=queue.list()
    active=[task for task in tasks if task["status"] in ("queued","running","waiting_quota")]
    recent=[task for task in tasks if task not in active]
    for task in [*active,*recent]:
        if recent and task is recent[0]: body.append('<h2>最近任务</h2>')
        task_type="定时周报任务 · " if task.get("cycle_id") or task.get("budget_scope")=="weekly" else ""
        body.append(f'<div class="card" data-lr-role="task" data-task-id="{escape(task["task_id"])}"><a href="/task/{escape(task["task_id"])}">查看任务</a> · '
                    f'{task_type}{escape(task["topic"])} · {escape(TASK_STATES.get(task["status"],task["status"]))} · '
                    f'{escape(PHASES.get(task["phase"],task["phase"]))} · 请求 {task["model_requests"]}/4</div>')
    body.append('<h2>最新保存结果</h2>')
    for report in reports:
        body.append(f'<div class="card" data-lr-role="report" data-run-id="{escape(report["run_id"])}"><a href="/report/{escape(report["run_id"])}">{escape(report["title"])}</a> · {escape(report_label(report["status"]))}</div>')
    if not reports:
        body.append('<p>尚无结果。提交后需启动独立执行进程。</p>')
    from .ui_projection import home_data
    return page("Learning Radar", "\n".join(body), kind='home', data=home_data(tasks,reports),fixture=queue.demo)


def render_task(task, demo):
    report=task.get("report") or {}
    email=task.get("email") or {}
    status=task["status"]
    weekly=bool(task.get("cycle_id")) or task.get("budget_scope")=="weekly"
    body=['<p><a href="/">← 首页与任务列表</a></p>',demo_banner(demo),
          '<h1>任务进度</h1>',
          f'<div class="card" data-lr-role="task" data-task-id="{escape(task["task_id"])}"><p>处理状态：<strong>{escape(TASK_STATES.get(status,status))}</strong></p>',
          f'<p>阶段：{escape(PHASES.get(task["phase"],task["phase"]))} · 主题：{escape(task["topic"])}</p>',
          f'<details><summary>技术编号</summary>任务编号：{escape(task["task_id"])}<br>运行编号：{escape(task["run_id"])}</details>',
          f'<p>模型请求：{task["model_requests"]}/4 · 消费日期：{escape(task.get("consumed_day") or "尚未消费")} · '
          f'预留日期：{escape(task.get("reserved_day") or "无")}</p>',
          f'<p>{escape(task["detail"])}</p></div>']
    if weekly:
        body.append(f'<div class="card">定时周报任务 · 周期：{escape(task.get("cycle_id") or "未记录")}；暂不支持取消。</div>')
    if task["uncertain"]:
        body.append('<div class="card">有请求结果不确定或正在请求，已保留占用次数，不会自动按零处理。</div>')
    if report:
        explanation="报告已生成" if report["status"]=="published" else "处理已保存，但报告未发布/未通过检查"
        body.append(f'<div class="card">{explanation}：{escape(report_label(report["status"]))} · '
                    f'<a href="/report/{escape(task["run_id"])}">查看报告或拒绝原因，并填写反馈</a></div>')
    elif task.get("run"):
        body.append(f'<div class="card">运行结果：{escape(RUN_STATES.get(task["run"]["status"],"已保存，详见状态代码"))} · {escape(task["run"].get("notes") or "")}<details><summary>运行状态代码</summary>{escape(task["run"]["status"])}</details></div>')
    notification=email.get("status") or "尚未通知"
    body.append(f'<div class="card">通知状态：<strong>{escape(notification_label(notification))}</strong>'
                + ('（假 SMTP，未真实发送）' if demo else '')
                + f'<p>{escape(email.get("error") or "")}</p><details><summary>通知状态代码</summary>{escape(notification)}</details>')
    if notification in ("failed","queued","尚未通知") and status in ("completed","failed","interrupted") and task.get("run"):
        body.append(f'<form data-lr-action="retry-email" method="post" action="/task/{escape(task["task_id"])}/retry-email"><button name="action" value="retry-email">仅重试通知</button></form>')
    elif notification=="unknown":
        body.append('<p>发送结果不明：请人工核对，不自动重发；邮件重试不会重新分析。</p>')
    if task.get("notify_pending"):
        body.append('<p>通知重试已排队，等待执行进程。</p>')
    body.append('</div>')
    if not weekly and status in ("queued","waiting_quota") and not task["model_requests"]:
        body.append(f'<form data-lr-action="cancel" method="post" action="/task/{escape(task["task_id"])}/cancel"><button name="action" value="cancel">取消未执行任务并释放预留</button></form>')
    if task["events"]:
        body.append('<h2>已记录的执行阶段</h2><ul>'+''.join(f'<li>{escape(event["ts"])} · {escape(PHASES.get(event["phase"],event["phase"]))}</li>' for event in task["events"])+'</ul>')
    if status in ("queued","running","waiting_quota") or task.get("notify_pending"):
        body.append('<script>setTimeout(()=>location.reload(),2000)</script>')
    from .ui_projection import task_summary
    return page("Learning Radar 任务", "\n".join(body), kind='task',data={'task':task_summary(task)},fixture=demo)


def build_handler(store_path: Path, *, settings=None, queue=None, gate, resources=None) -> type[BaseHTTPRequestHandler]:
    from .ui_assets import AssetRegistry, CSP, PREFIX
    from .ui_shell import finalize
    resources = resources or AssetRegistry(Path(__file__).resolve().parents[1] / 'frontend/dist')
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(10)

        def version_string(self):
            return ""

        def end_headers(self):
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            # Native same-origin POST forms need their Origin preserved; do not
            # disclose referrers to other origins.
            self.send_header("Referrer-Policy", "same-origin")
            self.send_header("Content-Security-Policy", CSP)
            if gate.auth.config.secure:
                self.send_header("Strict-Transport-Security", "max-age=31536000")
            if getattr(self, "pending_cookie", None):
                self.send_header("Set-Cookie", self.pending_cookie)
                self.pending_cookie = None
            super().end_headers()

        def _send(self, status: int, html: str) -> None:
            document = gate.decorate(self, html)
            payload = finalize(document,status=status,fixture=gate.demo,resources=resources).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self) -> None:  # noqa: N802
            resource_path = urlsplit(self.path).path
            if resource_path.startswith(PREFIX):
                if not gate.request_allowed(self):
                    gate.reject(self)
                    return
                asset = resources.read(resource_path[len(PREFIX):])
                payload, mime = asset if asset else (b'UI resource not found', 'text/plain; charset=utf-8')
                self.send_response(200 if asset else 404)
                self.send_header('Content-Type', mime)
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return
            if not gate.get(self):
                return
            path = self.path.split("?")[0]
            if queue and path.startswith("/task/"):
                task=queue.get(path[len("/task/"):])
                self._send(200 if task else 404,render_task(task,queue.demo) if task else page("未找到","未找到任务"))
                return
            with Store(store_path) as store:
                if path in ("/", "/index.html"):
                    self._send(200, render_task_index(settings,queue,store.list_reports()) if queue else render_index(store.list_reports()))
                    return
                if path=="/history":
                    query=parse_qs(urlsplit(self.path).query,keep_blank_values=True)
                    topics=settings.topics if settings else []
                    records = history_records(store,topics)
                    if 'topic' in query:  # Preserve existing deep links, not the default archive structure.
                        rendered = render_history(records,topics,query,bool(queue and queue.demo))
                    else:
                        from .archive import project_catalog
                        from .archive_web import render_projects
                        query['tag'] = query.get('tag', []) + [t.strip() for t in (query.get('tags') or [''])[0].split(',') if t.strip()]
                        projects = project_catalog(store,records,keyword=(query.get('q') or [''])[0], tags=query['tag'])
                        rendered = render_projects(projects,query,bool(queue and queue.demo))
                    self._send(200, rendered)
                    return
                if path.startswith("/report/"):
                    run_id = path[len("/report/") :]
                    view = build_report_view(store, run_id)
                    if view is None:
                        self._send(404, page("未找到", "<h1>未找到该报告</h1>"))
                        return
                    query = parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
                    saved = "1" in (query.get("saved") or [])
                    rendered=render_report(view,saved=saved)
                    if queue and queue.demo:
                        rendered=rendered.replace('<main>','<main>'+demo_banner(True),1)
                    self._send(200, rendered)
                    return
                if path == "/health":
                    self._send(200, page("ok", "<p>ok</p>"))
                    return
            self._send(404, page("未找到", "<h1>未找到</h1>"))

        def do_POST(self) -> None:  # noqa: N802
            path = self.path.split("?")[0]
            if self.headers.get("Transfer-Encoding") or len(self.headers.get_all("Content-Length", [])) != 1 or self.headers.get_content_type() != "application/x-www-form-urlencoded":
                gate.reject(self, 400)
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = 0
            if length <= 0 or length > 20000:
                self._send(400, page("请求无效", "<h1>请求无效</h1>"))
                return
            raw = self.rfile.read(length).decode("utf-8", errors="replace")
            fields = parse_qs(raw, keep_blank_values=True)
            if not gate.post(self, fields):
                return
            if queue and (path=="/tasks" or path.startswith("/task/")):
                from .tasks import TaskError, QuotaWait
                try:
                    if path=="/tasks":
                        topic=(fields.get("topic") or [""])[0]
                        from .discovery import request_options
                        from .config import ConfigError
                        try:
                            topic, options = request_options(settings, mode=(fields.get('discovery_mode') or [None])[0], topic=topic or None)
                        except ConfigError as exc:
                            raise TaskError(str(exc)) from None
                        task=queue.submit(topic,(fields.get("submission_key") or [""])[0],options=options)
                        identifier=task["task_id"]
                    else:
                        parts=path.split("/")
                        if len(parts)!=4:
                            raise TaskError("任务操作无效")
                        identifier=parts[2]
                        if parts[3]=="cancel":
                            queue.cancel(identifier)
                        elif parts[3]=="retry-email":
                            queue.retry_email(identifier)
                        else:
                            raise TaskError("任务操作无效")
                except TaskError as exc:
                    self._send(429 if isinstance(exc,QuotaWait) else 400,page("任务未提交",f'<p>{escape(exc)}</p><a href="/">返回首页</a>'))
                    return
                self.send_response(303)
                self.send_header("Location",f"/task/{identifier}")
                self.send_header("Content-Length","0")
                self.end_headers()
                return
            if not path.startswith("/feedback/"):
                self._send(404, page("未找到", "<h1>未找到</h1>"))
                return
            run_id = path[len("/feedback/") :]
            with Store(store_path) as store:
                view = build_report_view(store, run_id)
                if view is None:
                    self._send(404, page("未找到", "<h1>未找到该报告</h1>"))
                    return
                if not view["can_feedback"]:
                    self._send(
                        400,
                        page(
                            "不接受反馈",
                            "<h1>该运行没有可展示的报告正文</h1>"
                            "<p>结构损坏或渲染失败的运行只保留诊断，不提供反馈。</p>",
                        ),
                    )
                    return
                answers = clean_answers({key: (fields.get(key) or [""])[0] for key, _text in ITEMS})
                note = clean_note((fields.get("note") or [""])[0])
                store.save_feedback(run_id, str(view["repo"]), answers, note, now=now_iso())
            self.send_response(303)
            self.send_header("Location", f"/report/{run_id}?saved=1")
            self.send_header("Content-Length", "0")
            self.end_headers()
        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return

    return Handler


def serve(store_path: Path, *, host: str = "127.0.0.1", port: int = 8765, settings=None, queue=None, web_config=None, demo=False) -> ThreadingHTTPServer:
    from .auth import AuthService
    from .auth_http import HttpGate
    from .config import ConfigError
    from .web_config import load_web_config
    host = "127.0.0.1" if host == "localhost" else host
    if host != "127.0.0.1":
        raise ConfigError("网页后端仅监听 127.0.0.1；远程访问必须经 HTTPS 反向代理")
    demo = demo or bool(queue and queue.demo)
    if demo and not queue:
        from .tasks import TaskQueue
        TaskQueue(store_path.parent, demo=True)
    config = (web_config or load_web_config(settings, demo=demo)).validate()
    with Store(store_path):
        pass
    auth = AuthService(store_path, config, demo=demo)
    gate = HttpGate(auth, page, demo=demo)
    from .ui_assets import AssetRegistry
    asset_root = (settings.root if settings else Path(__file__).resolve().parents[1]) / 'frontend/dist'
    server = ThreadingHTTPServer((host, port), build_handler(store_path,settings=settings,queue=queue,gate=gate,resources=AssetRegistry(asset_root)))
    server.daemon_threads = True
    server.auth = auth
    return server
