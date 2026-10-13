"""邮件渲染与发送分类：只读已保存结果，假 SMTP，不联网、不调用模型。"""

from __future__ import annotations

import io
import json
import smtplib
import ssl
import tempfile
import unittest
import socket
from pathlib import Path
from unittest import mock

from learnradar import notify
from learnradar.notify import EmailConfig, build_email, scan_sensitive, send_email

FIXTURES = Path(__file__).parent / "fixtures"


def fixture_payload() -> dict:
    return json.loads((FIXTURES / "offline_analysis.json").read_text(encoding="utf-8"))


def make_outcome(*, report_status: str = "published", decision: str = "watch", markdown: str = "") -> dict:
    payload = fixture_payload()
    payload["recommendation"]["decision"] = decision
    return {
        "run_id": "2026-W39-agent-memory",
        "cycle_id": "2026-W39",
        "topic": "agent-memory",
        "target": "example-offline/target",
        "run_status": report_status,
        "report_status": report_status,
        "decision": decision,
        "payload": payload,
        "validation": {"ok": True, "errors": [], "warnings": [], "rules_version": "qa-5"},
        "run_context": {},
        "usage": {},
        "markdown_path": markdown,
        "created_at": "2026-09-24 20:30:00 中国标准时间",
    }


class RenderingTests(unittest.TestCase):
    def test_published_email_keeps_plain_language_and_sources(self) -> None:
        outcome = make_outcome()
        email = build_email(outcome, EmailConfig(host="", port=0, security="starttls", sender="", recipient=""))
        self.assertEqual(email["kind"], "report")
        body = email["body"]
        for section in ("【它做什么】", "【一个具体亮点】", "【对我的可能价值】", "【需要知道的缺口】", "【结论】", "【来源】"):
            self.assertIn(section, body)
        self.assertIn("结构检查通过只代表字段齐全", body)
        self.assertIn("不等于", body)
        self.assertIn("“待观察”不等于“值得马上学”", body)
        self.assertIn("https://github.com/example-offline/target", body)

    def test_recommended_email_has_no_watch_sentence(self) -> None:
        email = build_email(
            make_outcome(decision="recommend"),
            EmailConfig(host="", port=0, security="starttls", sender="", recipient=""),
        )
        self.assertNotIn("“待观察”不等于“值得马上学”", email["body"])
        self.assertIn("推荐", email["subject"])

    def test_status_email_for_cooldown_week(self) -> None:
        outcome = make_outcome()
        outcome.update(
            report_status="",
            run_status="all-candidates-in-cooldown",
            payload={},
            decision="",
            run_context={
                "cooldown": {
                    "skipped": [
                        {
                            "repo": "owner/proj",
                            "reason": "近期已作为目标完成分析并通过结构检查（已发布）",
                            "cooldown_until": "2026-10-24T12:17:00+00:00",
                        }
                    ]
                }
            },
        )
        email = build_email(outcome, EmailConfig(host="", port=0, security="starttls", sender="", recipient=""))
        self.assertEqual(email["kind"], "status")
        self.assertIn("本次候选都在冷却期内", email["body"])
        self.assertIn("owner/proj", email["body"])
        self.assertIn("这不等于网上没有合适项目", email["body"])

    def test_failure_email_does_not_leak_draft_content(self) -> None:
        outcome = make_outcome(report_status="rejected")
        outcome["validation"] = {
            "ok": False,
            "errors": ["claims[1].quote 无法在所引证据中匹配", "第二处错误"],
            "warnings": [],
        }
        email = build_email(outcome, EmailConfig(host="", port=0, security="starttls", sender="", recipient=""))
        self.assertEqual(email["kind"], "failure")
        self.assertIn("结构检查未通过", email["subject"])
        self.assertIn("claims[1].quote", email["body"])
        self.assertNotIn("夹具亮点", email["body"], "未通过的草稿内容不能进邮件")
        self.assertEqual(email["attachments"], [], "失败邮件不带附件")

    def test_attachment_uses_provided_sanitized_text(self) -> None:
        email = build_email(
            make_outcome(),
            EmailConfig(host="", port=0, security="starttls", sender="", recipient=""),
            attachment_text="# 对外版报告\n\n只含允许字段",
        )
        self.assertEqual(len(email["attachments"]), 1)
        self.assertIn("2026-W39-agent-memory", email["attachments"][0][0])
        self.assertIn("对外版报告", email["attachments"][0][1])

    def test_no_attachment_without_sanitized_text(self) -> None:
        email = build_email(
            make_outcome(), EmailConfig(host="", port=0, security="starttls", sender="", recipient="")
        )
        self.assertEqual(email["attachments"], [], "没有渲染好的对外版本就不带附件")

    def test_preview_has_no_sensitive_fields_or_dead_links(self) -> None:
        email = build_email(make_outcome(), EmailConfig(host="", port=0, security="starttls", sender="", recipient=""))
        findings = scan_sensitive(email["subject"] + "\n" + email["body"])
        self.assertEqual(findings, [], findings)

    def test_scan_sensitive_catches_paths_and_localhost(self) -> None:
        findings = scan_sensitive("见 http://localhost:8765/report/x 和 C:\\\\Users\\\\me 以及 data/runs/xyz")
        self.assertTrue(any("本机地址" in item for item in findings), findings)
        self.assertTrue(any("磁盘路径" in item for item in findings), findings)
        self.assertTrue(any("内部路径" in item for item in findings), findings)

    def test_scan_sensitive_catches_credential_like_text(self) -> None:
        findings = scan_sensitive("key=LEARNRADAR_FALLBACK_API_KEY 与 token=FAKE-NOT-A-REAL-CREDENTIAL")
        self.assertTrue(any("凭据" in item for item in findings), findings)


class FakeSMTP:
    """最小 SMTP 替身：记录调用，按脚本抛错。"""

    mode = "ok"          # ok | connect_error | auth_error | disconnect | data_error
    instances: list["FakeSMTP"] = []

    def __init__(self, host: str, port: int, timeout: int | None = None, context: object | None = None) -> None:
        if FakeSMTP.mode == "connect_error":
            raise OSError("connection refused")
        self.host = host
        self.port = port
        self.timeout = timeout
        self.sent: list[object] = []
        self.tls_context = None
        self.logged_in = None
        FakeSMTP.instances.append(self)

    def ehlo(self) -> None:
        return None

    def starttls(self, context: object | None = None) -> None:
        self.tls_context = context

    def login(self, user: str, password: str) -> None:
        if FakeSMTP.mode == "auth_error":
            raise smtplib.SMTPAuthenticationError(535, b"bad credentials")
        self.logged_in = (user, password)

    def send_message(self, message: object) -> None:
        if FakeSMTP.mode == "disconnect":
            raise smtplib.SMTPServerDisconnected("connection reset by peer")
        if FakeSMTP.mode == "data_error":
            raise smtplib.SMTPDataError(451, b"try later")
        self.sent.append(message)

    def quit(self) -> None:
        return None

    def close(self) -> None:
        return None

    def __enter__(self) -> "FakeSMTP":
        return self

    def __exit__(self, *exc_info: object) -> bool:
        return False


class SendClassificationTests(unittest.TestCase):
    def setUp(self) -> None:
        FakeSMTP.instances = []
        FakeSMTP.mode = "ok"
        self.config = EmailConfig(
            host="smtp.example.com",
            port=587,
            security="starttls",
            sender="from@example.com",
            recipient="to@example.com",
            username="from@example.com",
        )
        patcher = mock.patch("learnradar.notify.smtplib.SMTP", FakeSMTP)
        patcher.start()
        self.addCleanup(patcher.stop)
        env = mock.patch.dict("os.environ", {"LEARNRADAR_SMTP_PASSWORD": "secret"})
        env.start()
        self.addCleanup(env.stop)

    def test_success_uses_starttls_with_verification(self) -> None:
        status, detail = send_email(self.config, subject="s", body="b")
        self.assertEqual((status, detail), ("sent", ""))
        instance = FakeSMTP.instances[0]
        self.assertEqual(instance.logged_in, ("from@example.com", "secret"))
        self.assertEqual(instance.tls_context.verify_mode, ssl.CERT_REQUIRED)
        self.assertEqual(len(instance.sent), 1)

    def test_connect_error_is_failed_not_unknown(self) -> None:
        FakeSMTP.mode = "connect_error"
        status, detail = send_email(self.config, subject="s", body="b")
        self.assertEqual(status, "failed")
        self.assertIn("连接失败", detail)

    def test_auth_error_is_failed(self) -> None:
        FakeSMTP.mode = "auth_error"
        status, _detail = send_email(self.config, subject="s", body="b")
        self.assertEqual(status, "failed")

    def test_disconnect_during_send_is_unknown(self) -> None:
        FakeSMTP.mode = "disconnect"
        status, detail = send_email(self.config, subject="s", body="b")
        self.assertEqual(status, "unknown")
        self.assertIn("SMTPServerDisconnected", detail)

    def test_rejected_data_is_failed(self) -> None:
        FakeSMTP.mode = "data_error"
        status, _detail = send_email(self.config, subject="s", body="b")
        self.assertEqual(status, "failed")


class SMTPDiagnosisTests(unittest.TestCase):
    def setUp(self):
        self.config = EmailConfig(host="smtp.example.com", port=465, security="ssl",
            sender="fixture@example.com", recipient="fixture@example.com", username="fixture@example.com")

    def diagnose(self, replies, *, dns_error=None, tcp_error=None, tls_error=None):
        sock = mock.Mock()
        sock.makefile.return_value = io.BytesIO(replies)
        context = ssl.create_default_context()
        with mock.patch.object(context, "wrap_socket", return_value=sock, side_effect=tls_error), \
             mock.patch.object(notify.ssl, "create_default_context", return_value=context), \
             mock.patch.object(socket, "getfqdn", return_value="fixture.example.com"), \
             mock.patch.object(socket, "getaddrinfo", return_value=[], side_effect=dns_error), \
             mock.patch.object(smtplib.SMTP, "_get_socket", return_value=sock, side_effect=tcp_error):
            report = notify.diagnose_smtp(self.config, password="FixturePassword")
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)
        self.assertNotIn("FixturePassword", json.dumps(report))
        self.assertNotIn(self.config.username, json.dumps(report))
        commands = b"".join(call.args[0] for call in sock.sendall.call_args_list)
        for prohibited in (b"MAIL ", b"RCPT ", b"DATA"):
            self.assertNotIn(prohibited, commands.upper())
        return report

    def test_connection_authentication_and_quit_without_mail(self):
        report = self.diagnose(b"220 ready\r\n250-fixture\r\n250 AUTH PLAIN\r\n235 authenticated\r\n221 bye\r\n")
        self.assertTrue(report["ok"], report)
        self.assertTrue(report["authenticated"])
        phases = {r["stage"] for r in report["stages"]}
        self.assertTrue({"dns", "tcp", "tls", "welcome", "ehlo", "authentication", "quit"} <= phases)

    def test_dns_failure_is_not_authentication_failure(self):
        report = self.diagnose(b"", dns_error=socket.gaierror("fixture"))
        self.assertFalse(report["ok"])
        self.assertEqual(report["failure_stage"], "dns")

    def test_tcp_failure_stage(self):
        report = self.diagnose(b"", tcp_error=ConnectionRefusedError("fixture"))
        self.assertEqual(report["failure_stage"], "tcp")

    def test_certificate_failure_stage(self):
        report = self.diagnose(b"", tls_error=ssl.SSLCertVerificationError("fixture"))
        self.assertEqual(report["failure_stage"], "tls")
        self.assertFalse(report["authenticated"])

    def test_welcome_disconnect_stage(self):
        report = self.diagnose(b"")
        self.assertEqual(report["failure_stage"], "welcome")

    def test_ehlo_failure_records_only_response_code(self):
        report = self.diagnose(b"220 ready\r\n500 FixturePassword\r\n")
        self.assertEqual(report["failure_stage"], "ehlo")
        self.assertEqual(report["code"], 500)

    def test_authentication_disconnect_stage(self):
        report = self.diagnose(b"220 ready\r\n250-fixture\r\n250 AUTH PLAIN\r\n")
        self.assertEqual(report["failure_stage"], "authentication")
        self.assertFalse(report["authenticated"])

    def test_quit_failure_is_separate_from_authenticated(self):
        report = self.diagnose(b"220 ready\r\n250-fixture\r\n250 AUTH PLAIN\r\n235 authenticated\r\n")
        self.assertEqual(report["failure_stage"], "quit")
        self.assertTrue(report["authenticated"])
        self.assertFalse(report["ok"])


class ConfigTests(unittest.TestCase):
    def test_load_email_config_reads_env_path_and_requires_password(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "email.toml"
            path.write_text(
                "[email]\nhost = \"smtp.example.com\"\nsender = \"a@b.c\"\nrecipient = \"d@e.f\"\n",
                encoding="utf-8",
            )
            with mock.patch.dict("os.environ", {"LEARNRADAR_EMAIL_CONFIG": str(path)}, clear=False):
                config = notify.load_email_config(_settings_stub(Path(tmp)), require_secret=False)
                self.assertEqual(config.recipient, "d@e.f")
                with mock.patch.dict("os.environ", {"LEARNRADAR_SMTP_PASSWORD": ""}, clear=False):
                    with self.assertRaises(Exception) as ctx:
                        notify.load_email_config(_settings_stub(Path(tmp)), require_secret=True)
                    self.assertIn("SMTP 密码", str(ctx.exception))

    def test_missing_config_file_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict("os.environ", {"LEARNRADAR_EMAIL_CONFIG": str(Path(tmp) / "none.toml")}, clear=False):
                with self.assertRaises(Exception) as ctx:
                    notify.load_email_config(_settings_stub(Path(tmp)), require_secret=False)
                self.assertIn("email.example.toml", str(ctx.exception))


def _settings_stub(root: Path):
    class _Settings:
        pass

    stub = _Settings()
    stub.root = root
    return stub


if __name__ == "__main__":
    unittest.main()
