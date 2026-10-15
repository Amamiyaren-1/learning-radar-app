"""Offline weekly deduplication/concurrency/recovery/mail-state/separate-retry tests."""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import io
import json
import os
import re
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from learnradar import cli, cloud, web
from learnradar.cycle import (
    collect_outcome,
    cycle_id_for,
    schedule_config,
    scheduled_instant,
)
from learnradar.history import target_history
from learnradar.report import render_markdown
from learnradar.validate import ValidationResult
from learnradar.config import load_settings
from learnradar.evidence import normalize_repo_name
from learnradar.models import ModelError, ModelResponse, Usage
from learnradar.net import FetchError, FetchResult, now_iso
from learnradar.screening import ScreeningOutcome
from learnradar.store import Store
from tests.test_notify import FakeSMTP

README = (
    "# demo\n\n## Quickstart\n\n"
    "```bash\nnpm install demo\n```\n\n"
    "A tool-calling helper for agents that keeps every tool definition in one place "
    "and reuses the same calling convention everywhere.\n"
)

BLOCK_RE = re.compile(r"^\[(E-[A-Za-z0-9\-]+)\][^\n]*\n(.*?)(?=^\[E-|\Z)", re.S | re.M)


def parse_evidence(prompt: str) -> list[dict]:
    """Read evidence IDs, owners, and excerpts from prompts so fake responses follow supplied evidence."""
    items: list[dict] = []
    for match in BLOCK_RE.finditer(prompt):
        block = match.group(2)
        owner = ""
        owner_match = re.search(r"所属仓库: (\S+)", block)
        if owner_match:
            owner = normalize_repo_name(owner_match.group(1))
        excerpt = ""
        excerpt_match = re.search(r"\n  摘录:\n(.*)", block, re.S)
        if excerpt_match:
            raw = excerpt_match.group(1)
            lines = [line[4:] if line.startswith("    ") else line for line in raw.split("\n")]
            excerpt = "\n".join(lines).strip()
        items.append({"id": match.group(1), "owner": owner, "excerpt": excerpt})
    return items


def pick_line(excerpt: str) -> str:
    for line in excerpt.splitlines():
        text = line.strip()
        if len(text) >= 12:
            return text
    return excerpt.strip()[:40]


def build_report(prompt: str, target: str, comparators: list[str], decision: str = "watch") -> dict:
    evidence = parse_evidence(prompt)

    def pick(owner: str) -> dict | None:
        for item in evidence:
            if item["owner"] == owner and len(item["excerpt"]) >= 12:
                return item
        return None

    main = pick(target) or (evidence[0] if evidence else None)
    quote = pick_line(main["excerpt"]) if main else "证据不足"
    comparisons = []
    for name in comparators:
        item = pick(name)
        if not item:
            continue
        comparisons.append(
            {
                "comparator": name,
                "why_this_comparator": "同为工具集，便于比较。",
                "problem": "解决同类问题。",
                "key_difference": "实现方式不同。",
                "fit_conditions": "需要更广覆盖时。",
                "limitations": "上手成本更高。",
                "evidence_ids": [item["id"]],
            }
        )
    return {
        "title": f"{target} 分析报告",
        "target": {
            "repo": target,
            "version": "未标注",
            "category": ["Agent", "工具调用"],
            "summary_one_line": "把工具定义与调用约定收敛到一处。",
        },
        "highlight": {
            "items": [
                {
                    "name": "工具定义收敛",
                    "plain": "把工具定义与调用约定放在一处。",
                    "scenario": "假设场景：没有它就要在每个项目里重复适配。",
                    "how": "README 声称收敛到一处，尚未实际验证。",
                    "difference": "证据不足以比较优劣。",
                    "value": "能少写重复适配代码。",
                    "evidence_ids": [main["id"]],
                    "quote": quote,
                }
            ]
        },
        "novelty": {
            "what": "证据不足",
            "when": "不确定",
            "kind": "unclear",
            "evidence_ids": [main["id"]],
            "quote": quote,
        },
        "claims": [
            {
                "type": "fact",
                "text": "README 里写了这段内容。",
                "evidence_ids": [main["id"]],
                "quote": quote,
            }
        ],
        "comparisons": comparisons,
        "value_for_user": {
            "learn": "补齐工具调用的基本概念。",
            "demo": "做一个命令行小演示。",
            "artifact": "尝试换成自己的工具。",
        },
        "starting_conditions": {
            "have": ["会用 C 与 Python 做基础算法题"],
            "gaps": ["没有核验依赖是否齐全"],
        },
        "difficulty": {
            "level": "run_example",
            "basis": "README 提供安装与示例命令。",
            "conditions": [
                {"condition": "runtime", "value": "未知（未在任何已取材料中找到）", "status": "unknown"},
                {"condition": "os_support", "value": "未知（未在任何已取材料中找到）", "status": "unknown"},
                {"condition": "gpu", "value": "不适用（只调用远程 API）", "status": "not_applicable"},
                {
                    "condition": "external_cost",
                    "value": "需要模型 API",
                    "status": "inference",
                    "basis": "示例需要调用模型",
                },
            ],
        },
        "learning_action": {
            "executable": False,
            "action": "本次未核验到可执行路径。",
            "done_criteria": "",
            "entry": "",
        },
        "mini_experiment": {
            "suggested_change": "给示例加一个自定义工具。",
            "observable": "输出里出现新增工具名。",
            "boundary": "不修改库本身。",
        },
        "explore_further": {
            "repo": f"https://github.com/{target}",
            "docs": "未核验到",
            "example_entry": "未核验到",
            "known_limits": ["本次只读了 README"],
        },
        "recommendation": {
            "decision": decision,
            "reason": "夹具判定：先观察。",
            "evidence_gaps": ["缺少运行条件证据"],
            "uncertainties": ["依赖是否齐全未知"],
        },
    }


class FakeAdapter:
    """Fake model answers from supplied prompt evidence and can deliberately damage structure."""

    def __init__(self, target: str, comparators: list[str], *, decision: str = "watch", broken: bool = False) -> None:
        self.target = target
        self.comparators = comparators
        self.decision = decision
        self.broken = broken
        self.calls = 0

    def complete(self, *, system: str, user: str, max_output_tokens: int | None = None) -> ModelResponse:
        self.calls += 1
        report = build_report(user, self.target, self.comparators, self.decision)
        if self.broken:
            report.pop("comparisons", None)
        return ModelResponse(
            text=json.dumps(report, ensure_ascii=False),
            usage=Usage(input_tokens=100, output_tokens=200, reasoning_tokens=0, raw={}),
            model_id="deepseek-v4-pro",
            provider="chat_completions",
            latency_ms=5,
            endpoint="stub://",
            raw={},
            finish_reason="stop",
        )


class WeeklyTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.data_dir = Path(self._tmp.name)
        base = load_settings()
        self.settings = dataclasses.replace(
            base,
            app={**base.app, "data_dir": self._tmp.name},
            schedule={
                "timezone": "Asia/Shanghai",
                "weekday": "Sun",
                "at": "20:17",
                "topic": "agent-memory",
                "task_timeout_seconds": 60,
                "attach_markdown": True,
            },
        )
        email_path = self.data_dir / "email.toml"
        email_path.write_text(
            "[email]\n"
            'host = "smtp.example.com"\n'
            "port = 587\n"
            'security = "starttls"\n'
            'sender = "radar@example.com"\n'
            'recipient = "me@example.com"\n'
            'username = "radar@example.com"\n',
            encoding="utf-8",
        )
        self.email_path = email_path
        env = mock.patch.dict(
            "os.environ",
            {
                "LEARNRADAR_EMAIL_CONFIG": str(email_path),
                "LEARNRADAR_SMTP_PASSWORD": "secret",
            },
        )
        env.start()
        self.addCleanup(env.stop)
        FakeSMTP.instances = []
        FakeSMTP.mode = "ok"
        smtp_patch = mock.patch("learnradar.notify.smtplib.SMTP", FakeSMTP)
        smtp_patch.start()
        self.addCleanup(smtp_patch.stop)
        self.store = Store(self.data_dir / "learning-radar.sqlite")
        self.addCleanup(self.store.close)
        self.screening_calls: list[list[str]] = []
        self.adapters: list[FakeAdapter] = []

    # -- helpers
    def items(self, names: list[str]) -> list[dict]:
        out = []
        for name in names:
            out.append(
                {
                    "full_name": name,
                    "description": "工具调用框架",
                    "language": "Python",
                    "stargazers_count": 100,
                    "pushed_at": "2026-09-01T00:00:00Z",
                    "archived": False,
                    "fork": False,
                    "default_branch": "main",
                    "html_url": f"https://github.com/{name}",
                }
            )
        return out

    def fake_client(self, items: list[dict]):
        client = mock.Mock()
        client.search_repositories.return_value = items
        client.get_repository.side_effect = lambda name: next(
            (entry for entry in items if entry["full_name"] == name), items[0]
        )
        client.latest_commits.return_value = [
            {"sha": "a" * 40, "commit": {"committer": {"date": "2026-09-01T00:00:00Z"}, "message": "init"}}
        ]
        client.get_readme.return_value = (
            README,
            {"path": "README.md", "html_url": "https://github.com/a/one#readme"},
        )
        client.latest_release.return_value = None
        client.notes = []
        client.audit = []
        client.mode = "api"
        client.user_agent = "ua"
        client.timeout = 5
        client.max_bytes = 100000
        return client

    @staticmethod
    def fake_fetch(url: str, **kwargs):
        if "missing" in url:
            raise FetchError("HTTP 404")
        return FetchResult(
            url=url,
            final_url=url,
            status=200,
            headers={"Content-Type": "text/html"},
            body=b"<pre>npm install demo</pre>",
            retrieved_at=now_iso(),
            sha256="deadbeef",
            content_type="text/html",
            attempts=1,
        )

    def run_weekly(
        self,
        names: list[str],
        *,
        decision: str = "watch",
        broken: bool = False,
        cycle: str | None = "2026-W39",
        force_redo: bool = False,
        target: str = "a/one",
        comparators: list[str] | None = None,
        adapter_error: bool = False,
    ) -> tuple[int, str, FakeAdapter]:
        comparators = comparators if comparators is not None else names[1:3]
        screening = ScreeningOutcome(
            entries=[{"repo": target, "decision": "keep", "fit": "high", "reason": "相关", "gate": ""}]
        )

        def screening_stage(*args: object, **kwargs: object):
            passed = kwargs.get("candidates") or []
            self.screening_calls.append([str(item.get("full_name")) for item in passed])
            return screening

        adapter = FakeAdapter(target, comparators, decision=decision, broken=broken)
        self.adapters.append(adapter)
        builder = (
            mock.Mock(side_effect=ModelError("测试用：无真实模型"))
            if adapter_error
            else mock.Mock(return_value=adapter)
        )
        buffer = io.StringIO()
        with mock.patch.object(cli, "_client", return_value=self.fake_client(self.items(names))), \
             mock.patch.object(cli, "_run_screening_stage", side_effect=screening_stage), \
             mock.patch.object(cli, "build_adapter", builder), \
             mock.patch("learnradar.collector.fetch", side_effect=self.fake_fetch), \
             contextlib.redirect_stdout(buffer):
            code = cloud.cmd_weekly(
                self.settings,
                argparse.Namespace(topic=None, cycle=cycle, force_redo=force_redo),
            )
        return code, buffer.getvalue(), adapter

    def seed_published_run(self, run_id: str, repo: str, *, days: float) -> None:
        finished = (datetime.now(timezone.utc) - timedelta(days=days)).replace(microsecond=0).isoformat()
        self.store.start_run(run_id, kind="increment-a-analysis", started_at=finished, snapshot={})
        run_dir = self.data_dir / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "run-context.json").write_text(
            json.dumps({"target": repo}, ensure_ascii=False), encoding="utf-8"
        )
        self.store.save_report(
            run_id,
            report_id=run_id,
            created_at=finished,
            status="published",
            title="旧的已发布报告",
            markdown_path="",
            html_path="",
            payload={"target": {"repo": repo}},
            validation={"ok": True, "rules_version": "qa-5"},
        )
        self.store.finish_run(run_id, status="published", finished_at=finished, notes="")

    def emails(self) -> list[dict]:
        return self.store.conn.execute("SELECT * FROM emails ORDER BY rowid").fetchall() and [
            dict(row) for row in self.store.conn.execute("SELECT * FROM emails ORDER BY rowid")
        ]


class WeeklyFlowTests(WeeklyTestCase):
    def test_published_cycle_sends_report_and_never_repeats(self) -> None:
        code, output, adapter = self.run_weekly(["a/one", "a/two", "a/three"])
        self.assertEqual(code, 0, output)
        self.assertEqual(adapter.calls, 1, "一次周期只调用一次分析模型")
        cycle = self.store.get_cycle("2026-W39")
        self.assertEqual(cycle["status"], "completed")
        self.assertEqual(cycle["run_id"], "2026-W39-agent-memory")
        email = self.store.latest_email_for_run("2026-W39-agent-memory")
        self.assertEqual(email["status"], "sent")
        self.assertEqual(email["kind"], "report")
        self.assertIn("a/one", email["subject"])
        self.assertIn("【一个具体亮点】", email["body"])
        self.assertEqual(len(FakeSMTP.instances[0].sent), 1)
        message = FakeSMTP.instances[0].sent[0]
        self.assertTrue(message.is_multipart(), "正常推荐邮件应带 Markdown 附件")
        usage = json.loads(
            (self.data_dir / "runs" / "2026-W39-agent-memory" / "usage.json").read_text(encoding="utf-8")
        )
        self.assertLessEqual(usage["budget"]["used"], 4, "模型预算仍然生效")

        # Repeating the same cycle does not analyze again or create new mail.
        code2, output2, adapter2 = self.run_weekly(["a/one", "a/two", "a/three"])
        self.assertEqual(code2, 0, output2)
        self.assertIn("该周期已经处理过", output2)
        self.assertEqual(adapter2.calls, 0, "重复触发不得再调用模型")
        self.assertEqual(len(self.emails()), 1)
        self.assertEqual(len(FakeSMTP.instances), 1)

    def test_all_candidates_in_cooldown_sends_status_with_zero_model_calls(self) -> None:
        self.seed_published_run("run-old", "a/one", days=1)
        code, output, adapter = self.run_weekly(["a/one"])
        self.assertEqual(code, 0, output)
        self.assertEqual(adapter.calls, 0, "全部在冷却期内时不得调用模型")
        self.assertEqual(self.screening_calls, [], "连初筛都不调用")
        cycle = self.store.get_cycle("2026-W39")
        self.assertEqual(cycle["status"], "completed")
        email = self.store.latest_email_for_run("2026-W39-agent-memory")
        self.assertEqual(email["kind"], "status")
        self.assertEqual(email["status"], "sent")
        self.assertIn("冷却", email["body"])

    def test_rejected_report_sends_failure_notice_without_draft(self) -> None:
        code, output, _adapter = self.run_weekly(["a/one", "a/two", "a/three"], broken=True)
        cycle = self.store.get_cycle("2026-W39")
        self.assertEqual(cycle["status"], "completed")
        email = self.store.latest_email_for_run("2026-W39-agent-memory")
        self.assertEqual(email["kind"], "failure")
        self.assertIn("结构检查未通过", email["subject"])
        self.assertNotIn("工具定义收敛", email["body"], "未通过的草稿不能当推荐发送")
        self.assertNotEqual(code, 0, "没有可发布报告时进程应报需要处理")

    def test_model_failure_marks_cycle_failed_and_notifies(self) -> None:
        code, output, _adapter = self.run_weekly(
            ["a/one", "a/two", "a/three"], adapter_error=True
        )
        self.assertNotEqual(code, 0, output)
        cycle = self.store.get_cycle("2026-W39")
        self.assertEqual(cycle["status"], "failed")
        email = self.store.latest_email_for_run("2026-W39-agent-memory")
        self.assertEqual(email["kind"], "failure")
        self.assertEqual(email["status"], "sent")

    def test_concurrent_trigger_exits_without_analysis(self) -> None:
        lock = self.data_dir / "locks" / "weekly.lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text("pid=999\n", encoding="utf-8")
        code, output, adapter = self.run_weekly(["a/one", "a/two", "a/three"])
        self.assertNotEqual(code, 0, output)
        self.assertIn("旧 weekly 锁仍存在", output)
        self.assertEqual(adapter.calls, 0)
        self.assertIsNone(self.store.get_cycle("2026-W39"))

    def test_interrupted_cycle_is_recorded_and_needs_manual_redo(self) -> None:
        started = (datetime.now(timezone.utc) - timedelta(hours=3)).replace(microsecond=0).isoformat()
        self.store.save_cycle("2026-W39", status="running", topic="agent-memory", started_at=started)
        code, output, adapter = self.run_weekly(["a/one", "a/two", "a/three"])
        self.assertNotEqual(code, 0, output)
        self.assertEqual(adapter.calls, 0, "中断的周期不自动重跑")
        self.assertEqual(self.store.get_cycle("2026-W39")["status"], "running", "时间不能证明旧执行者已停止")
        self.assertIn("--force-redo", output)

        # Manual recovery checks and backs up before authorizing new analysis.
        self.assertEqual(cloud.cmd_cycle_recover(self.settings,argparse.Namespace(cycle="2026-W39",backup_out=None)),0)

        code2, output2, adapter2 = self.run_weekly(
            ["a/one", "a/two", "a/three"], force_redo=True
        )
        self.assertEqual(code2, 0, output2)
        self.assertEqual(adapter2.calls, 1)
        self.assertEqual(self.store.get_cycle("2026-W39")["status"], "completed")

    def test_completed_cycle_refuses_force_redo(self) -> None:
        self.run_weekly(["a/one", "a/two", "a/three"])
        code, output, adapter = self.run_weekly(
            ["a/one", "a/two", "a/three"], force_redo=True
        )
        self.assertEqual(code, cli.EXIT_USAGE)
        self.assertIn("拒绝强制重跑", output)
        self.assertEqual(adapter.calls, 0)


class EmailStateTests(WeeklyTestCase):
    def test_send_failure_keeps_reason_and_retry_sends_saved_content_only(self) -> None:
        FakeSMTP.mode = "auth_error"
        code, output, _adapter = self.run_weekly(["a/one", "a/two", "a/three"])
        self.assertNotEqual(code, 0, output)
        email = self.store.latest_email_for_run("2026-W39-agent-memory")
        self.assertEqual(email["status"], "failed")
        self.assertIn("SMTPAuthenticationError", email["error"])

        FakeSMTP.mode = "ok"
        buffer = io.StringIO()
        with mock.patch.object(cli, "build_adapter", mock.Mock(side_effect=AssertionError("重发不得调用模型"))), \
             contextlib.redirect_stdout(buffer):
            code2 = cloud.cmd_email_retry(
                self.settings, argparse.Namespace(email_id=None, run_id="2026-W39-agent-memory", cycle=None, force=False)
            )
        self.assertEqual(code2, 0, buffer.getvalue())
        email2 = self.store.latest_email_for_run("2026-W39-agent-memory")
        self.assertEqual(email2["status"], "sent")
        self.assertEqual(email2["attempts"], 2)
        self.assertEqual(email2["body"], email["body"], "重发的是保存下来的内容")

    def test_unknown_result_is_not_auto_resent(self) -> None:
        FakeSMTP.mode = "disconnect"
        self.run_weekly(["a/one", "a/two", "a/three"])
        email = self.store.latest_email_for_run("2026-W39-agent-memory")
        self.assertEqual(email["status"], "unknown")

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = cloud.cmd_email_retry(
                self.settings, argparse.Namespace(email_id=None, run_id="2026-W39-agent-memory", cycle=None, force=False)
            )
        self.assertNotEqual(code, 0)
        self.assertIn("默认不重发", buffer.getvalue())
        self.assertEqual(self.store.latest_email_for_run("2026-W39-agent-memory")["status"], "unknown")
        self.assertEqual(len(FakeSMTP.instances[0].sent), 0)

        FakeSMTP.mode = "ok"
        with contextlib.redirect_stdout(io.StringIO()):
            code2 = cloud.cmd_email_retry(
                self.settings, argparse.Namespace(email_id=None, run_id="2026-W39-agent-memory", cycle=None, force=True)
            )
        self.assertEqual(code2, 0)
        self.assertEqual(self.store.latest_email_for_run("2026-W39-agent-memory")["status"], "sent")

    def test_sent_email_is_not_resent(self) -> None:
        self.run_weekly(["a/one", "a/two", "a/three"])
        sent_before = len(FakeSMTP.instances)
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = cloud.cmd_email_retry(
                self.settings, argparse.Namespace(email_id=None, run_id="2026-W39-agent-memory", cycle=None, force=True)
            )
        self.assertEqual(code, 0)
        self.assertIn("不重复发送", buffer.getvalue())
        self.assertEqual(len(FakeSMTP.instances), sent_before, "已发送的邮件不再建立新连接")

    def test_preview_reads_saved_results_without_model_or_send(self) -> None:
        self.run_weekly(["a/one", "a/two", "a/three"])
        before = self.emails()
        buffer = io.StringIO()
        with mock.patch.object(cli, "build_adapter", mock.Mock(side_effect=AssertionError("预览不得调用模型"))), \
             contextlib.redirect_stdout(buffer):
            code = cloud.cmd_email_preview(
                self.settings, argparse.Namespace(run_id="2026-W39-agent-memory", cycle=None)
            )
        self.assertEqual(code, 0, buffer.getvalue())
        text = buffer.getvalue()
        self.assertIn("未分析、未发送、未写库", text)
        self.assertIn("【一个具体亮点】", text)
        self.assertIn("敏感内容检查：未发现", text)
        self.assertEqual(self.emails(), before, "预览不写库")
        self.assertEqual(len(FakeSMTP.instances), 1, "预览不发信")

    def test_preview_of_unknown_run_is_safe(self) -> None:
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = cloud.cmd_email_preview(
                self.settings, argparse.Namespace(run_id="not-a-run", cycle=None)
            )
        self.assertNotEqual(code, 0, buffer.getvalue())

    def test_cycles_and_backup_commands_work(self) -> None:
        self.run_weekly(["a/one", "a/two", "a/three"])
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = cloud.cmd_cycles(self.settings, argparse.Namespace(limit=5))
        self.assertEqual(code, 0)
        self.assertIn("2026-W39", buffer.getvalue())

        out = self.data_dir / "backup.sqlite"
        with contextlib.redirect_stdout(io.StringIO()):
            code2 = cloud.cmd_backup(self.settings, argparse.Namespace(out=str(out)))
        self.assertEqual(code2, 0)
        self.assertTrue(out.exists())
        restored = Store(out)
        self.addCleanup(restored.close)
        self.assertEqual(restored.get_cycle("2026-W39")["status"], "completed")


class ScheduleAnchorTests(unittest.TestCase):
    """Catch-up uses the latest scheduled instant without replaying multiple weeks."""

    def setUp(self) -> None:
        self.schedule = schedule_config(
            dataclasses.replace(
                load_settings(),
                schedule={
                    "timezone": "Asia/Shanghai",
                    "weekday": "Sun",
                    "at": "20:17",
                    "topic": "agent-memory",
                    "task_timeout_seconds": 60,
                    "attach_markdown": True,
                },
            )
        )

    def test_planned_instant_is_used_for_the_cycle(self) -> None:
        # Normal Sunday 20:17 trigger.
        sunday = datetime(2026, 9, 27, 20, 17, tzinfo=timezone(timedelta(hours=8)))
        anchor = scheduled_instant(self.schedule, sunday)
        self.assertEqual(anchor, sunday)
        self.assertEqual(cycle_id_for(anchor), "2026-W39")

    def test_catch_up_after_downtime_belongs_to_the_missed_week(self) -> None:
        # Monday 09:00 catch-up belongs to the missed Sunday cycle, not a new week.
        monday = datetime(2026, 9, 28, 9, 0, tzinfo=timezone(timedelta(hours=8)))
        anchor = scheduled_instant(self.schedule, monday)
        self.assertEqual(anchor, datetime(2026, 9, 27, 20, 17, tzinfo=timezone(timedelta(hours=8))))
        self.assertEqual(cycle_id_for(anchor), "2026-W39")

    def test_long_downtime_only_catches_up_one_cycle(self) -> None:
        # After three weeks offline, select only the latest scheduled instant without backlog replay.
        later = datetime(2026, 10, 12, 9, 0, tzinfo=timezone(timedelta(hours=8)))
        anchor = scheduled_instant(self.schedule, later)
        self.assertEqual(anchor, datetime(2026, 10, 11, 20, 17, tzinfo=timezone(timedelta(hours=8))))
        self.assertEqual(cycle_id_for(anchor), "2026-W41")

    def test_same_instant_never_maps_to_two_cycles(self) -> None:
        for hours in range(0, 24):
            moment = datetime(2026, 9, 28, hours, 0, tzinfo=timezone(timedelta(hours=8)))
            self.assertEqual(cycle_id_for(scheduled_instant(self.schedule, moment)), "2026-W39")


class AttachmentPrivacyTests(WeeklyTestCase):
    """Outbound attachments use allowed fields only, excluding feedback, profiles, prompts, and run metadata."""

    def test_email_attachment_is_the_external_version(self) -> None:
        self.run_weekly(["a/one", "a/two", "a/three"])
        email = self.store.latest_email_for_run("2026-W39-agent-memory")
        path = Path(email["attachment_path"])
        self.assertTrue(path.exists())
        self.assertEqual(path.name, "email-report.md", "附件是对外版本，不是服务器上的报告文件")
        text = path.read_text(encoding="utf-8")
        self.assertIn("附录 A", text)
        self.assertIn("附录 C", text)
        self.assertNotIn("附录 B", text, "运行与消耗信息不进对外附件")
        self.assertNotIn("附录 E", text, "用户反馈不进对外附件")
        self.assertNotIn("me@example.com", text)

    def test_sanitized_markdown_drops_feedback_even_if_report_has_it(self) -> None:
        self.run_weekly(["a/one", "a/two", "a/three"])
        outcome = collect_outcome(self.store, self.settings, "2026-W39-agent-memory")
        feedback_rows = [
            {
                "repo": "a/one",
                "run_id": "2026-W39-agent-memory",
                "updated_at": "2026-09-24T12:00:00+00:00",
                "understood_what": "yes",
                "understood_highlight": "unsure",
                "want_more": "no",
                "note": "这是用户的私人备注",
            }
        ]
        raw = render_markdown(
            report=outcome["payload"],
            evidence=outcome["evidence"],
            meta={"运行 ID": "2026-W39-agent-memory", "模型": "deepseek-v4-pro"},
            validation=ValidationResult(ok=True),
            stage_usage=[],
            feedback_rows=feedback_rows,
        )
        self.assertIn("附录 E", raw, "服务器上的报告确实可能带反馈附录")
        self.assertIn("这是用户的私人备注", raw)

        external = cloud.sanitized_markdown(outcome)
        self.assertNotIn("附录 E", external)
        self.assertNotIn("这是用户的私人备注", external)
        self.assertNotIn("附录 B", external)
        self.assertIn("结构检查通", external)


class EmailTestCommandTests(WeeklyTestCase):
    """Mail tests do not call models, modify report states, or consume production cycles."""

    def test_smtp_test_sends_without_touching_cycles_or_reports(self) -> None:
        reports_before = self.store.conn.execute("SELECT COUNT(*) AS c FROM reports").fetchone()["c"]
        buffer = io.StringIO()
        with mock.patch.object(cli, "build_adapter", mock.Mock(side_effect=AssertionError("测试邮件不得调用模型"))), \
             contextlib.redirect_stdout(buffer):
            code = cloud.cmd_email_test(self.settings, argparse.Namespace())
        self.assertEqual(code, 0, buffer.getvalue())
        self.assertIn("服务器已接受", buffer.getvalue())
        self.assertEqual(len(FakeSMTP.instances[0].sent), 1)
        message = FakeSMTP.instances[0].sent[0]
        self.assertEqual(message["To"], "me@example.com")
        rows = self.emails()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["kind"], "test")
        self.assertEqual(rows[0]["cycle_id"], "")
        self.assertEqual(self.store.list_cycles(), [], "测试邮件不占周期")
        self.assertIsNone(self.store.get_cycle("2026-W39"))
        reports_after = self.store.conn.execute("SELECT COUNT(*) AS c FROM reports").fetchone()["c"]
        self.assertEqual(reports_before, reports_after, "测试邮件不改动报告状态")

    def test_smtp_test_failure_is_reported(self) -> None:
        FakeSMTP.mode = "connect_error"
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = cloud.cmd_email_test(self.settings, argparse.Namespace())
        self.assertNotEqual(code, 0)
        self.assertIn("failed", buffer.getvalue())
        self.assertEqual(self.emails()[0]["status"], "failed")


class BackupRestoreTests(WeeklyTestCase):
    """Restored database/files support history, feedback, viewing, and saved-mail readback."""

    def test_bundle_backup_restores_readable_data(self) -> None:
        self.run_weekly(["a/one", "a/two", "a/three"])
        run_id = "2026-W39-agent-memory"
        self.store.save_feedback(
            run_id,
            "a/one",
            {"understood_what": "yes", "understood_highlight": "yes", "want_more": "no"},
            "恢复后应当读得到",
            now="2026-09-24T12:00:00+00:00",
        )
        backup_db = self.data_dir / "backup" / "learning-radar.sqlite"
        bundle = self.data_dir / "backup" / "files"
        with contextlib.redirect_stdout(io.StringIO()):
            code = cloud.cmd_backup(
                self.settings, argparse.Namespace(out=str(backup_db), bundle_dir=str(bundle))
            )
        self.assertEqual(code, 0)
        manifest = json.loads((bundle / "MANIFEST.json").read_text(encoding="utf-8"))
        self.assertIn("reports", manifest["included"])
        self.assertTrue(manifest["excluded"], "密钥与档案必须显式排除")
        self.assertFalse((bundle / "profile.toml").exists())
        self.assertFalse((bundle / "secret.env").exists())

        # Restore to another isolated data directory, simulating a new server.
        restored_dir = self.data_dir / "restored"
        (restored_dir / "reports").mkdir(parents=True)
        shutil.copy(backup_db, restored_dir / "learning-radar.sqlite")
        shutil.copytree(bundle / "reports", restored_dir / "reports", dirs_exist_ok=True)
        shutil.copytree(bundle / "runs", restored_dir / "runs", dirs_exist_ok=True)

        store = Store(restored_dir / "learning-radar.sqlite")
        self.addCleanup(store.close)
        history = target_history(store, runs_dir=restored_dir / "runs")
        self.assertIn("a/one", history, "冷却判定要能在恢复后读到历史")
        self.assertEqual(len(store.feedback_for_repos(["a/one"])), 1, "反馈要在恢复后可读")
        self.assertEqual(
            store.feedback_for_repos(["a/one"])[0]["note"], "恢复后应当读得到"
        )
        self.assertEqual(store.get_cycle("2026-W39")["status"], "completed", "周期记录可读")
        email = store.latest_email_for_run(run_id)
        self.assertEqual(email["status"], "sent", "邮件状态可读")
        self.assertTrue(Path(email["attachment_path"]).exists(), "重发邮件要能找到附件")
        view = web.build_report_view(store, run_id)
        self.assertIsNotNone(view)
        self.assertEqual(view["document_kind"], "published")
        self.assertIn("a/one", view["document"], "报告正文要在恢复后可打开")
        self.assertTrue(view["can_feedback"], "反馈表单仍可用")


if __name__ == "__main__":
    unittest.main()
