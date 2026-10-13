"""email-send：给已保存的运行手动发信；不分析、不占周期；已发送不重发。"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import unittest
from pathlib import Path
from unittest import mock

from learnradar import cli, cloud
from tests.test_notify import FakeSMTP
from tests.test_weekly import WeeklyTestCase

RUN_ID = "2026-W39-agent-memory"


class EmailSendTests(WeeklyTestCase):
    def _send(self, run_id: str = RUN_ID, *, force: bool = False) -> tuple[int, str]:
        buffer = io.StringIO()
        guard = mock.Mock(side_effect=AssertionError("email-send 不得调用模型"))
        with mock.patch.object(cli, "build_adapter", guard), contextlib.redirect_stdout(buffer):
            code = cloud.cmd_email_send(
                self.settings, argparse.Namespace(run_id=run_id, force=force)
            )
        guard.assert_not_called()
        return code, buffer.getvalue()

    def _seed_run(
        self,
        run_id: str,
        *,
        status: str,
        payload: dict | None = None,
        validation: dict | None = None,
        with_report: bool = True,
    ) -> None:
        run_dir = self.data_dir / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "run-context.json").write_text(
            json.dumps({"target": "a/one", "comparators": ["a/two"]}, ensure_ascii=False),
            encoding="utf-8",
        )
        self.store.start_run(
            run_id, kind="increment-a-analysis", started_at="2026-09-27T21:52:05+00:00", snapshot={}
        )
        if with_report:
            md_path = self.data_dir / "reports" / f"{run_id}.md"
            md_path.parent.mkdir(parents=True, exist_ok=True)
            md_path.write_text("# 未通过结构检查（未发布）\n\n诊断\n", encoding="utf-8")
            self.store.save_report(
                run_id,
                report_id=run_id,
                created_at="2026-09-27 21:52:05",
                status=status,
                title="t",
                markdown_path=str(md_path),
                html_path="",
                payload=payload or {},
                validation=validation or {"ok": False, "errors": ["x"], "warnings": [], "rules_version": "qa-5"},
            )
        self.store.finish_run(
            run_id, status=status, finished_at="2026-09-27T21:52:05+00:00", notes=""
        )

    # --- 已发布：发推荐邮件（复用现有渲染与隐私过滤）
    def test_published_run_gets_the_recommendation_email(self) -> None:
        FakeSMTP.mode = "auth_error"          # weekly 那次发信失败，留下 failed 邮件行
        code, _output, _adapter = self.run_weekly(["a/one", "a/two", "a/three"])
        self.assertNotEqual(code, 0)
        before = self.store.latest_email_for_run(RUN_ID)
        self.assertEqual(before["status"], "failed")
        cycle_before = dict(self.store.get_cycle("2026-W39"))

        FakeSMTP.mode = "ok"
        code2, output2 = self._send()
        self.assertEqual(code2, 0, output2)
        email = self.store.latest_email_for_run(RUN_ID)
        self.assertEqual(email["status"], "sent")
        self.assertEqual(email["kind"], "report")
        self.assertEqual(email["cycle_id"], "", "手动发送不挂到 weekly 周期上")
        self.assertEqual(email["attempts"], 2)
        self.assertEqual(email["body"], before["body"], "复用同一份已渲染内容")
        self.assertIn("【一个具体亮点】", email["body"])
        attachment = Path(email["attachment_path"])
        self.assertTrue(attachment.exists())
        self.assertEqual(attachment.name, "email-report.md", "附件是对外版本，不是原报告文件")
        text = attachment.read_text(encoding="utf-8")
        self.assertNotIn("附录 B", text)
        self.assertNotIn("附录 E", text)
        self.assertEqual(
            dict(self.store.get_cycle("2026-W39")), cycle_before, "email-send 不改周期记录"
        )

    def test_already_sent_run_is_not_resent(self) -> None:
        code, _output, _adapter = self.run_weekly(["a/one", "a/two", "a/three"])
        self.assertEqual(code, 0)
        connections = len(FakeSMTP.instances)
        code2, output2 = self._send()
        self.assertEqual(code2, 0, output2)
        self.assertIn("不重复发送", output2)
        self.assertEqual(len(FakeSMTP.instances), connections, "已发送的运行不再建立连接")
        self.assertEqual(self.store.latest_email_for_run(RUN_ID)["status"], "sent")

    def test_unknown_result_needs_force(self) -> None:
        FakeSMTP.mode = "disconnect"
        self.run_weekly(["a/one", "a/two", "a/three"])
        self.assertEqual(self.store.latest_email_for_run(RUN_ID)["status"], "unknown")
        code, output = self._send()
        self.assertNotEqual(code, 0)
        self.assertIn("默认不重发", output)
        self.assertEqual(self.store.latest_email_for_run(RUN_ID)["status"], "unknown")

        FakeSMTP.mode = "ok"
        code2, output2 = self._send(force=True)
        self.assertEqual(code2, 0, output2)
        self.assertEqual(self.store.latest_email_for_run(RUN_ID)["status"], "sent")

    # --- 被拒 / 没有报告：只发不含草稿的失败通知
    def test_rejected_run_sends_failure_notice_without_draft(self) -> None:
        draft = "草稿里的亮点：持久执行（不应出现在邮件里）"
        payload = {
            "target": {"repo": "a/one"},
            "highlight": {"items": [{"name": "x", "plain": draft}]},
            "recommendation": {"decision": "watch"},
        }
        self._seed_run(
            "rejected-run",
            status="rejected",
            payload=payload,
            validation={
                "ok": False,
                "errors": ["claims[1].quote 无法在所引证据中匹配"],
                "warnings": [],
                "rules_version": "qa-5",
            },
        )
        FakeSMTP.mode = "ok"
        code, output = self._send(run_id="rejected-run")
        self.assertEqual(code, 0, output)
        email = self.store.latest_email_for_run("rejected-run")
        self.assertEqual(email["kind"], "failure")
        self.assertIn("结构检查未通过", email["subject"])
        self.assertIn("claims[1].quote", email["body"])
        self.assertNotIn(draft, email["body"], "被拒报告只能发失败通知，不带草稿内容")
        self.assertEqual(email["attachment_path"], "")
        self.assertEqual(email["cycle_id"], "")
        self.assertFalse(
            (self.data_dir / "runs" / "rejected-run" / "email-report.md").exists(),
            "失败通知不生成附件",
        )

    def test_run_without_report_sends_failure_notice(self) -> None:
        self._seed_run("model-failed-run", status="model-failed", with_report=False)
        FakeSMTP.mode = "ok"
        code, output = self._send(run_id="model-failed-run")
        self.assertEqual(code, 0, output)
        email = self.store.latest_email_for_run("model-failed-run")
        self.assertEqual(email["kind"], "failure")
        self.assertIn("模型生成失败", email["subject"])
        self.assertEqual(email["attachment_path"], "")

    def test_unknown_run_id_is_refused(self) -> None:
        code, output = self._send(run_id="not-a-run")
        self.assertNotEqual(code, 0)
        self.assertIn("找不到该运行的记录", output)
        self.assertEqual(self.emails(), [], "找不到运行时不留任何邮件记录")


if __name__ == "__main__":
    unittest.main()
