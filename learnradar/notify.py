"""Deterministically render stored structured results and send with stdlib SMTP.
Do not call models, scrape CLI output, or add a summary stage.
Persist mail before delivery; retries use saved content only.
SMTP acceptance is sent, ambiguous disconnection is unknown; neither proves receipt.
Exclude local URLs/paths, profiles, prompts, audit logs, credentials, and full feedback."""

from __future__ import annotations

import os
import re
import smtplib
import socket
import ssl
import tomllib
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path
from typing import Any

from .config import ConfigError, Settings

DECISION_LABELS = {"recommend": "推荐", "watch": "待观察", "not_recommend": "不推荐"}
STATUS_LABELS = {
    "all-candidates-in-cooldown": "本次候选都在冷却期内",
    "no-suitable-candidate": "本轮没有合适候选",
    "screening-failed": "初筛失败，未进入分析",
    "insufficient-comparators": "对照项目不足，未生成报告",
    "no-evidence": "没有取到证据，停止（不伪造结论）",
    "model-failed": "模型生成失败",
    "failed": "运行失败",
    "rejected": "结构检查未通过（未发布）",
    "render-failed": "报告渲染失败（未发布）",
    "published": "已发布",
}
KIND_REPORT = "report"
KIND_STATUS = "status"
KIND_FAILURE = "failure"

SENSITIVE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("本机地址", re.compile(r"127\.0\.0\.1|localhost", re.I)),
    ("磁盘路径", re.compile(r"[A-Za-z]:\\\\|/(?:home|Users|srv|var/lib|etc|opt)/")),
    ("内部路径", re.compile(r"data/runs|config/profile|learning-radar\.sqlite|\.env\b")),
    ("凭据字样", re.compile(r"sk-[A-Za-z0-9]{8,}|LEARNRADAR_[A-Z_]*KEY|api_key|Bearer\s+\S+", re.I)),
    ("内部材料", re.compile(r"原始提示词|审计日志|evidence_ids|prompt_block", re.I)),
)
URL_PATTERN = re.compile(r"https?://[^\s<>()（）]+")


@dataclass(frozen=True)
class EmailConfig:
    host: str
    port: int
    security: str
    sender: str
    recipient: str
    subject_prefix: str = "[Learning Radar]"
    username: str = ""
    password_env: str = "LEARNRADAR_SMTP_PASSWORD"
    timeout_seconds: int = 30
    attach_markdown: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "host": self.host,
            "port": self.port,
            "security": self.security,
            "sender": self.sender,
            "recipient": self.recipient,
            "subject_prefix": self.subject_prefix,
            "username": self.username,
            "password_env": self.password_env,
            "timeout_seconds": self.timeout_seconds,
            "attach_markdown": self.attach_markdown,
        }


def email_config_path(settings: Settings) -> Path:
    override = os.environ.get("LEARNRADAR_EMAIL_CONFIG", "").strip()
    return Path(override) if override else settings.root / "config" / "email.toml"


def load_email_config(settings: Settings, *, require_secret: bool = True) -> EmailConfig:
    path = email_config_path(settings)
    if not path.exists():
        raise ConfigError(
            f"缺少邮件配置 {path}：可从 config/email.example.toml 复制，"
            "或用环境变量 LEARNRADAR_EMAIL_CONFIG 指向受限配置文件"
        )
    raw = tomllib.loads(path.read_text(encoding="utf-8")).get("email", {})
    config = EmailConfig(
        host=str(raw.get("host", "")).strip(),
        port=int(raw.get("port", 587)),
        security=str(raw.get("security", "starttls")).strip().lower() or "starttls",
        sender=str(raw.get("sender", "")).strip(),
        recipient=str(raw.get("recipient", "")).strip(),
        subject_prefix=str(raw.get("subject_prefix", "[Learning Radar]")).strip(),
        username=str(raw.get("username", "")).strip(),
        password_env=str(raw.get("password_env", "LEARNRADAR_SMTP_PASSWORD")).strip(),
        timeout_seconds=int(raw.get("timeout_seconds", 30)),
        attach_markdown=bool(raw.get("attach_markdown", True)),
    )
    missing = [name for name, value in (("host", config.host), ("sender", config.sender), ("recipient", config.recipient)) if not value]
    if missing:
        raise ConfigError(f"邮件配置缺少字段：{'、'.join(missing)}（文件 {path}）")
    if config.security not in {"starttls", "ssl"}:
        raise ConfigError(f"邮件配置 security 只能是 starttls 或 ssl，实际是 {config.security!r}")
    if require_secret and not smtp_password(config):
        raise ConfigError(
            f"缺少 SMTP 密码：环境变量 {config.password_env} 为空。"
            "密码只从环境变量读取，不要写进配置文件，也不要贴在聊天里"
        )
    return config


def smtp_password(config: EmailConfig) -> str:
    return os.environ.get(config.password_env, "")


# ---------------------------------------------------------------- Rendering

def _http_links(text: Any) -> list[str]:
    found: list[str] = []
    for match in URL_PATTERN.finditer(str(text or "")):
        url = match.group(0).rstrip(".,;）)")
        if "127.0.0.1" in url or "localhost" in url:
            continue
        if url not in found:
            found.append(url)
    return found


def _bullet(lines: list[str], label: str, value: Any) -> None:
    text = str(value or "").strip()
    if text:
        lines.append(f"- {label}：{text}")


def _first_highlight(payload: dict[str, Any]) -> dict[str, Any]:
    items = ((payload.get("highlight") or {}).get("items") or []) if isinstance(payload.get("highlight"), dict) else []
    for item in items:
        if isinstance(item, dict):
            return item
    return {}


def render_published_email(outcome: dict[str, Any], config: EmailConfig) -> tuple[str, str]:
    payload = outcome.get("payload") or {}
    target = payload.get("target") or {}
    repo = str(target.get("repo") or outcome.get("target") or "")
    decision = str((payload.get("recommendation") or {}).get("decision") or "")
    label = DECISION_LABELS.get(decision, "待观察")
    subject = f"{config.subject_prefix} {repo}：{label}"
    lines: list[str] = [f"本轮目标：{repo}（{label}）", ""]
    version = str(target.get("version") or "").strip()
    categories = "、".join(str(item) for item in (target.get("category") or []))
    meta = "；".join(part for part in (f"版本 {version}" if version else "", categories) if part)
    if meta:
        lines += [meta, ""]
    lines.append("【它做什么】")
    lines.append(str(target.get("summary_one_line") or "（报告里没有一句话说明）"))
    lines.append("")
    highlight = _first_highlight(payload)
    lines.append("【一个具体亮点】")
    if highlight:
        lines.append(f"- {str(highlight.get('name') or '亮点').strip()}")
        _bullet(lines, "是什么", highlight.get("plain"))
        _bullet(lines, "没有它会怎样", highlight.get("scenario"))
        _bullet(lines, "项目怎么做（含未核验部分）", highlight.get("how"))
        _bullet(lines, "与相关方案的差别", highlight.get("difference"))
    else:
        lines.append("- 报告里没有可展示的亮点条目。")
    lines.append("")
    value = payload.get("value_for_user") or {}
    lines.append("【对我的可能价值】")
    _bullet(lines, "能学到", value.get("learn"))
    _bullet(lines, "可能做的演示", value.get("demo"))
    _bullet(lines, "可能形成的个人成果", value.get("artifact"))
    lines.append("")
    rec = payload.get("recommendation") or {}
    lines.append("【需要知道的缺口】")
    gaps = [str(item) for item in (rec.get("evidence_gaps") or [])]
    uncertainties = [str(item) for item in (rec.get("uncertainties") or [])]
    for item in gaps:
        lines.append(f"- 证据缺口：{item}")
    for item in uncertainties:
        lines.append(f"- 不确定：{item}")
    if not gaps and not uncertainties:
        lines.append("- 报告没有写出缺口（这本身也是一个缺口）。")
    lines.append("")
    lines.append("【结论】")
    lines.append(f"- {label}：{str(rec.get('reason') or '（未写理由）')}")
    if decision == "watch":
        lines.append("- “待观察”不等于“值得马上学”：上面列出的缺口仍需先解决。")
    if decision == "not_recommend":
        lines.append("- 本轮判定为不推荐，仅作记录。")
    lines.append("")
    explore = payload.get("explore_further") or {}
    links: list[str] = []
    for key in ("repo", "docs", "example_entry"):
        links.extend(_http_links(explore.get(key)))
    lines.append("【来源】")
    lines += [f"- {url}" for url in links] or ["- 报告没有给出可点击来源。"]
    lines.append("")
    lines.append(
        "说明：结构检查通过只代表字段齐全、引文能在本次已取材料中逐字匹配，"
        "不代表内容已全面核验，也不代表项目方宣称已被验证。"
    )
    if config.attach_markdown and outcome.get("markdown_path"):
        lines.append("完整报告见附件（Markdown，可直接在手机上阅读）。")
    return subject, "\n".join(lines)


def render_status_email(outcome: dict[str, Any], config: EmailConfig) -> tuple[str, str]:
    run_status = str(outcome.get("run_status") or "")
    summary = STATUS_LABELS.get(run_status, run_status or "未知状态")
    subject = f"{config.subject_prefix} 本周状态：{summary}"
    lines = [
        f"周期：{outcome.get('cycle_id') or '（未标注）'}　运行：{outcome.get('run_id')}",
        f"结果：{summary}",
        "",
    ]
    cooldown = (outcome.get("run_context") or {}).get("cooldown") or {}
    if cooldown.get("skipped"):
        lines.append("冷却期内的项目（到期后自然重新参与）：")
        for item in cooldown["skipped"][:5]:
            lines.append(
                f"- {item.get('repo')}：{item.get('reason')}；最早可再评估 {item.get('cooldown_until') or '未记录'}"
            )
        lines.append("")
    lines.append("本轮没有推荐：这不等于网上没有合适项目，也不代表已有项目不值得学习。")
    lines.append("下一次定时运行会照常执行；手动指定项目可以提前重新分析。")
    return subject, "\n".join(lines)


def render_failure_email(outcome: dict[str, Any], config: EmailConfig) -> tuple[str, str]:
    report_status = str(outcome.get("report_status") or "")
    run_status = str(outcome.get("run_status") or "")
    summary = STATUS_LABELS.get(report_status) or STATUS_LABELS.get(run_status) or "本轮没有生成可发布的报告"
    subject = f"{config.subject_prefix} 本周未生成推荐：{summary}"
    lines = [
        f"周期：{outcome.get('cycle_id') or '（未标注）'}　运行：{outcome.get('run_id')}",
        f"结果：{summary}",
        "",
    ]
    validation = outcome.get("validation") or {}
    errors = [str(item) for item in (validation.get("errors") or [])][:3]
    if errors:
        lines.append("原因（来自本次结构检查）：")
        lines += [f"- {item}" for item in errors]
        lines.append("")
    lines.append("没有把未通过检查的草稿当正式推荐发送；原始输出与诊断留在服务器的运行记录里。")
    lines.append("人工修复后可以单独重发这一封邮件，或下一周期重新分析。")
    return subject, "\n".join(lines)


def build_email(
    outcome: dict[str, Any], config: EmailConfig, *, kind: str = "", attachment_text: str = ""
) -> dict[str, Any]:
    report_status = str(outcome.get("report_status") or "")
    run_status = str(outcome.get("run_status") or "")
    decision = str(outcome.get("decision") or "")
    if not kind:
        if report_status == "published":
            kind = KIND_REPORT if decision in {"recommend", "watch"} else KIND_STATUS
        elif report_status in {"rejected", "render-failed"}:
            kind = KIND_FAILURE
        elif run_status in {"all-candidates-in-cooldown", "no-suitable-candidate"}:
            kind = KIND_STATUS
        else:
            kind = KIND_FAILURE
    if kind == KIND_REPORT and report_status == "published":
        subject, body = render_published_email(outcome, config)
    elif kind == KIND_STATUS:
        subject, body = render_status_email(outcome, config)
    else:
        subject, body = render_failure_email(outcome, config)
    attachments: list[tuple[str, str]] = []
    # The caller renders attachments from outbound-safe fields, excluding feedback and run metadata.
    # Use that rendered content; never attach a server report file verbatim.
    if config.attach_markdown and kind == KIND_REPORT and attachment_text.strip():
        attachments.append((f"report-{outcome.get('run_id')}.md", attachment_text))
    return {"kind": kind, "subject": subject, "body": body, "attachments": attachments}


def scan_sensitive(text: str) -> list[str]:
    """Find content forbidden in outbound mail for preview and tests."""
    findings: list[str] = []
    for label, pattern in SENSITIVE_PATTERNS:
        match = pattern.search(text or "")
        if match:
            findings.append(f"{label}：{match.group(0)[:60]}")
    for url in URL_PATTERN.findall(text or ""):
        if not url.startswith(("http://", "https://")):
            findings.append(f"不可用链接：{url[:60]}")
    return findings


# ---------------------------------------------------------------- Delivery

def _smtp_diagnostic_factory(config: EmailConfig, record):
    """Observe stdlib SMTP phases/status codes without enabling AUTH debug output."""
    base = smtplib.SMTP_SSL if config.security == "ssl" else smtplib.SMTP

    class TracedSMTP(base):
        phase = "connect"

        def mark(self, phase):
            self.phase = phase
            record(phase)

        def _get_socket(self, host, port, timeout):
            self.mark("dns")
            socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
            self.mark("tcp")
            sock = smtplib.SMTP._get_socket(self, host, port, timeout)
            if config.security == "ssl":
                self.mark("tls")
                try:
                    sock = self.context.wrap_socket(sock, server_hostname=self._host)
                except BaseException:
                    sock.close()
                    raise
            self.mark("welcome")
            return sock

        def getreply(self):
            code, message = super().getreply()
            record(self.phase, code)
            return code, message

        def ehlo(self, name=""):
            self.mark("ehlo")
            return super().ehlo(name)

        def starttls(self, **kwargs):
            self.mark("tls")
            return super().starttls(**kwargs)

        def auth(self, *args, **kwargs):
            self.mark("authentication")
            return super().auth(*args, **kwargs)

        def send_message(self, *args, **kwargs):
            self.mark("sending")
            return super().send_message(*args, **kwargs)

        def docmd(self, cmd, args=""):
            if cmd.upper() == "QUIT":
                self.mark("quit")
            return super().docmd(cmd, args)

    return TracedSMTP


def diagnose_smtp(config: EmailConfig, *, password: str | None = None, smtp_factory=None) -> dict[str, Any]:
    """Check connection/authentication once; do not send MAIL/RCPT/DATA or retry."""
    stages: list[dict[str, Any]] = []
    def record(stage, code=None):
        row = {"stage": stage}
        if code is not None:
            row["code"] = int(code)
        stages.append(row)
    context = ssl.create_default_context()
    server = None
    report: dict[str, Any] = {"ok": False, "authenticated": False, "stages": stages}
    try:
        factory = smtp_factory or _smtp_diagnostic_factory(config, record)
        record("connect")
        kwargs = {"timeout": config.timeout_seconds}
        if config.security == "ssl":
            kwargs["context"] = context
        server = factory(config.host, config.port, **kwargs)
        record("ehlo")
        code, _ = server.ehlo()
        if code != 250:
            raise smtplib.SMTPHeloError(code, b"EHLO rejected")
        if config.security == "starttls":
            record("tls")
            server.starttls(context=context)
            record("ehlo")
            code, _ = server.ehlo()
            if code != 250:
                raise smtplib.SMTPHeloError(code, b"EHLO rejected")
        record("authentication")
        if not config.username:
            raise ConfigError("SMTP authentication username is missing")
        server.login(config.username, password if password is not None else smtp_password(config))
        report["authenticated"] = True
        record("quit")
        server.quit()
        report["ok"] = True
    except (OSError, smtplib.SMTPException, ConfigError) as exc:
        report["failure_stage"] = stages[-1]["stage"] if stages else "connect"
        report["exception"] = type(exc).__name__
        code = getattr(exc, "smtp_code", None)
        if isinstance(code, int):
            report["code"] = code
        # Do not record exception text: server messages may contain private data.
    finally:
        if server is not None:
            server.close()
    return report

def send_email(
    config: EmailConfig,
    *,
    subject: str,
    body: str,
    attachments: list[tuple[str, str]] | None = None,
    password: str | None = None,
    smtp_factory: Any = None,
) -> tuple[str, str]:
    """Send once without automatic retry; return sent, failed, or unknown and detail."""
    message = EmailMessage()
    message["From"] = config.sender
    message["To"] = config.recipient
    message["Subject"] = subject
    message.set_content(body, charset="utf-8")
    for name, text in attachments or []:
        message.add_attachment(
            text.encode("utf-8"), maintype="text", subtype="markdown", filename=name
        )
    context = ssl.create_default_context()  # Keep certificate verification enabled.
    try:
        if config.security == "ssl":
            server: smtplib.SMTP = (smtp_factory or smtplib.SMTP_SSL)(
                config.host, config.port, timeout=config.timeout_seconds, context=context
            )
        else:
            server = (smtp_factory or smtplib.SMTP)(config.host, config.port, timeout=config.timeout_seconds)
    except (OSError, smtplib.SMTPException) as exc:
        return "failed", f"连接失败：{type(exc).__name__}: {exc}"
    accepted = False
    try:
        with server:
            if config.security == "starttls":
                server.ehlo()
                server.starttls(context=context)
                server.ehlo()
            if config.username:
                server.login(config.username, password if password is not None else smtp_password(config))
            accepted = True
            server.send_message(message)
    except smtplib.SMTPServerDisconnected as exc:
        # A disconnect during delivery leaves acceptance unknown; never automatically retry.
        return ("unknown" if accepted else "failed"), f"{type(exc).__name__}: {exc}"
    except TimeoutError as exc:
        return ("unknown" if accepted else "failed"), f"超时：{type(exc).__name__}: {exc}"
    except smtplib.SMTPException as exc:
        # SMTPException subclasses OSError and must be caught first.
        # Otherwise authentication/recipient/data rejection would be misclassified as unknown.
        return "failed", f"发送被拒绝：{type(exc).__name__}: {exc}"
    except OSError as exc:
        return ("unknown" if accepted else "failed"), f"{type(exc).__name__}: {exc}"
    return "sent", ""
