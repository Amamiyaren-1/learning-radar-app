"""Read-only cooldown regression with default success/failure windows and no preference changes."""

from __future__ import annotations

import argparse
import dataclasses
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from learnradar import cli
from learnradar.config import load_settings
from learnradar.history import (
    CooldownConfig,
    cooldown_config,
    filter_items,
    manual_notice,
    summary_line,
    target_history,
)
from learnradar.models import ModelError
from learnradar.net import now_iso
from learnradar.screening import ScreeningOutcome
from learnradar.store import Store

NOW = "2026-09-24T12:00:00+00:00"
CONFIG = CooldownConfig(success_days=30, failure_days=7)

README = "x [Docs](https://docs.example.dev/get-started) [Examples](https://github.com/a/one-examples)\n\n"


def days_ago(days: float) -> str:
    moment = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc) - timedelta(days=days)
    return moment.replace(microsecond=0).isoformat()


def make_item(name: str) -> dict:
    return {
        "full_name": name,
        "description": "说明",
        "language": "Python",
        "stargazers_count": 10,
        "pushed_at": "2026-09-01T00:00:00Z",
        "archived": False,
        "fork": False,
        "default_branch": "main",
        "html_url": f"https://github.com/{name}",
    }


class HistoryTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.data_dir = Path(self._tmp.name)
        self.store = Store(self.data_dir / "learning-radar.sqlite")
        self.addCleanup(self.store.close)

    def seed(
        self,
        run_id: str,
        *,
        target: str = "",
        run_status: str = "published",
        report_status: str | None = None,
        finished: str = "",
        topic: str = "tool-calling",
        record_target: bool = True,
        payload_target: str | None = None,
        no_timestamp: bool = False,
    ) -> None:
        stamp = "" if no_timestamp else (finished or NOW)
        self.store.start_run(run_id, kind="increment-a-analysis", started_at=stamp, snapshot={})
        run_dir = self.data_dir / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        if record_target:
            (run_dir / "run-context.json").write_text(
                json.dumps({"target": target, "topic": topic}, ensure_ascii=False),
                encoding="utf-8",
            )
        if report_status:
            repo = payload_target if payload_target is not None else target
            self.store.save_report(
                run_id,
                report_id=run_id,
                created_at=stamp,
                status=report_status,
                title="t",
                markdown_path="",
                html_path="",
                payload={"target": {"repo": repo}},
                validation={"ok": report_status == "published"},
            )
        self.store.finish_run(run_id, status=run_status, finished_at=stamp, notes="")

    def seed_report_only(self, run_id: str, repo: str, *, status: str, created_at: str) -> None:
        """Successful replay finalization can have a reports row without a runs row."""
        self.store.save_report(
            run_id,
            report_id=run_id,
            created_at=created_at,
            status=status,
            title="t",
            markdown_path="",
            html_path="",
            payload={"target": {"repo": repo}},
            validation={"ok": status == "published"},
        )

    def decisions(self):
        history = target_history(self.store, runs_dir=self.data_dir / "runs")
        items = [make_item("owner/proj")]
        return filter_items(items, history, config=CONFIG, now=NOW), history

    # --- Success: 30 days; failure: 7 days
    def test_published_target_skips_for_30_days(self) -> None:
        self.seed("run-a", target="owner/proj", report_status="published", finished=days_ago(10))
        decisions, _history = self.decisions()
        self.assertEqual(decisions[0].state, "skipped")
        self.assertEqual(decisions[0].days, 30)
        self.assertEqual(decisions[0].until, days_ago(-20))

    def test_published_target_recovers_after_30_days(self) -> None:
        self.seed("run-a", target="owner/proj", report_status="published", finished=days_ago(31))
        decisions, _history = self.decisions()
        self.assertEqual(decisions[0].state, "allowed")

    def test_rejected_target_pauses_for_7_days(self) -> None:
        self.seed("run-a", target="owner/proj", run_status="rejected", report_status="rejected", finished=days_ago(3))
        decisions, _history = self.decisions()
        self.assertEqual(decisions[0].state, "skipped")
        self.assertEqual(decisions[0].days, 7)
        self.assertEqual(decisions[0].until, days_ago(-4))
        self.assertIn("结构检查未通过", decisions[0].reason)

    def test_rejected_target_recovers_after_7_days(self) -> None:
        self.seed("run-a", target="owner/proj", run_status="rejected", report_status="rejected", finished=days_ago(8))
        decisions, _history = self.decisions()
        self.assertEqual(decisions[0].state, "allowed")

    def test_render_failed_pauses_for_7_days(self) -> None:
        self.seed("run-a", target="owner/proj", run_status="render-failed", report_status="render-failed", finished=days_ago(1))
        decisions, _history = self.decisions()
        self.assertEqual(decisions[0].state, "skipped")
        self.assertIn("渲染失败", decisions[0].reason)

    def test_model_failed_attempt_pauses_for_7_days(self) -> None:
        self.seed("run-a", target="owner/proj", run_status="model-failed", finished=days_ago(2))
        decisions, _history = self.decisions()
        self.assertEqual(decisions[0].state, "skipped")
        self.assertIn("生成失败", decisions[0].reason)

    # --- Offline rechecks do not change historical state
    def test_offline_recheck_pass_does_not_publish_rejected_run(self) -> None:
        self.seed("run-a", target="owner/proj", run_status="rejected", report_status="rejected", finished=days_ago(3))
        recheck = self.data_dir / "runs" / "run-a" / "offline-recheck.json"
        recheck.write_text(
            json.dumps({"ok": True, "structure_check": "结构检查通过"}, ensure_ascii=False),
            encoding="utf-8",
        )
        decisions, _history = self.decisions()
        self.assertEqual(decisions[0].state, "skipped", "离线复检通过不代表原报告已发布")
        self.assertEqual(decisions[0].days, 7)
        self.assertNotEqual(decisions[0].until, days_ago(-27))

    # --- Failure cannot shorten successful cooldown
    def test_later_failure_does_not_shorten_success_cooldown(self) -> None:
        self.seed("run-a", target="owner/proj", report_status="published", finished=days_ago(10))
        self.seed("run-b", target="owner/proj", run_status="rejected", report_status="rejected", finished=days_ago(1))
        decisions, _history = self.decisions()
        self.assertEqual(decisions[0].state, "skipped")
        self.assertEqual(decisions[0].days, 30)
        self.assertEqual(decisions[0].until, days_ago(-20))

    # --- Cross-topic and case-insensitive identity
    def test_same_repo_across_topics_and_case_is_one_history(self) -> None:
        self.seed("run-a", target="Owner/Proj", report_status="published", finished=days_ago(5), topic="tool-calling")
        self.seed("run-b", target="owner/proj", run_status="rejected", report_status="rejected", finished=days_ago(2), topic="agent-memory")
        decisions, history = self.decisions()
        self.assertEqual(list(history), ["owner/proj"])
        self.assertEqual(len(history["owner/proj"]), 2)
        self.assertEqual(decisions[0].state, "skipped")

    # --- Comparator/search/screening-only appearances do not count
    def test_comparator_only_and_screened_only_are_not_counted(self) -> None:
        self.seed(
            "run-a",
            target="other/lib",
            report_status="published",
            finished=days_ago(1),
            payload_target="other/lib",
        )
        (self.data_dir / "runs" / "run-a" / "run-context.json").write_text(
            json.dumps({"target": "other/lib", "comparators": ["owner/proj"]}, ensure_ascii=False),
            encoding="utf-8",
        )
        self.seed("run-b", target="", run_status="screening-ok", finished=days_ago(1), report_status=None)
        decisions, history = self.decisions()
        self.assertNotIn("owner/proj", history)
        self.assertEqual(decisions[0].state, "allowed")

    def test_discovered_only_run_is_not_counted(self) -> None:
        self.seed("run-a", target="", run_status="failed", finished=days_ago(1), record_target=False)
        decisions, history = self.decisions()
        self.assertEqual(history, {})
        self.assertEqual(decisions[0].state, "allowed")

    # --- Missing fields and unknown states
    def test_unknown_status_is_recorded_but_allowed(self) -> None:
        self.seed("run-a", target="owner/proj", run_status="something-new", finished=days_ago(1))
        decisions, _history = self.decisions()
        self.assertEqual(decisions[0].state, "unknown")
        self.assertIn("无法判定", decisions[0].reason)
        self.assertFalse(decisions[0].blocked)

    def test_missing_timestamp_is_not_guessed(self) -> None:
        self.seed(
            "run-a", target="owner/proj", report_status="published", no_timestamp=True
        )
        history = target_history(self.store, runs_dir=self.data_dir / "runs")
        decisions = filter_items([make_item("owner/proj")], history, config=CONFIG, now=NOW)
        self.assertEqual(decisions[0].state, "allowed")

    def test_published_replay_without_runs_row_starts_success_cooldown(self) -> None:
        self.seed_report_only(
            "replay-run",
            "owner/proj",
            status="published",
            created_at="2026-09-23 14:07:58 中国标准时间",
        )
        decisions, history = self.decisions()
        self.assertEqual(len(history["owner/proj"]), 1)
        self.assertEqual(decisions[0].state, "skipped")
        self.assertEqual(decisions[0].days, 30)
        self.assertEqual(decisions[0].until, "2026-10-23T06:07:58+00:00")

    def test_unparsable_timestamp_is_not_guessed(self) -> None:
        self.seed_report_only("run-x", "owner/proj", status="published", created_at="时间不详")
        decisions, _history = self.decisions()
        self.assertEqual(decisions[0].state, "allowed")

    # --- Feedback has no effect
    def test_feedback_does_not_affect_cooldown(self) -> None:
        self.store.save_feedback(
            "run-a", "owner/proj", {"want_more": "no"}, "", now=days_ago(1)
        )
        decisions, _history = self.decisions()
        self.assertEqual(decisions[0].state, "allowed", "“不想进一步了解”不参与冷却判断")
        self.seed("run-b", target="owner/proj", report_status="published", finished=days_ago(2))
        decisions, _history = self.decisions()
        self.assertEqual(decisions[0].state, "skipped", "有冷却历史时仍然跳过，与反馈无关")

    # --- Hints and summaries
    def test_manual_notice_explains_history_without_blocking(self) -> None:
        self.seed("run-a", target="owner/proj", report_status="published", finished=days_ago(2))
        history = target_history(self.store, runs_dir=self.data_dir / "runs")
        notice = manual_notice(
            "owner/proj", history["owner/proj"], config=CONFIG, now=NOW
        )
        self.assertIn("owner/proj", notice)
        self.assertIn("run-a", notice)
        self.assertIn("不受限制", notice)

    def test_summary_line_counts_skips_and_unknown(self) -> None:
        self.seed("run-a", target="owner/proj", report_status="published", finished=days_ago(2))
        self.seed("run-b", target="other/lib", run_status="something-new", finished=days_ago(2))
        history = target_history(self.store, runs_dir=self.data_dir / "runs")
        decisions = filter_items(
            [make_item("owner/proj"), make_item("other/lib"), make_item("third/lib")],
            history,
            config=CONFIG,
            now=NOW,
        )
        line = summary_line(decisions)
        self.assertIn("本次取得 3 个候选", line)
        self.assertIn("跳过 1 个", line)
        self.assertIn("剩余 2 个进入初筛", line)
        self.assertIn("无法判定", line)


class CooldownCliTests(unittest.TestCase):
    """Fake search/models: all cooled targets consume zero calls; skipped targets may be comparators."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.data_dir = Path(self._tmp.name)
        base = load_settings()
        self.settings = dataclasses.replace(base, app={**base.app, "data_dir": self._tmp.name})
        self.store = Store(self.data_dir / "learning-radar.sqlite")
        self.addCleanup(self.store.close)

    def seed_published(self, run_id: str, repo: str, *, days: float) -> None:
        finished = (
            datetime.now(timezone.utc) - timedelta(days=days)
        ).replace(microsecond=0).isoformat()
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

    def run_args(self, **overrides) -> argparse.Namespace:
        data = {
            "topic": None,
            "candidate": None,
            "compare": None,
            "model": None,
            "per_page": 5,
            "limit": 5,
            "candidates_from": None,
            "screening_only": False,
            "screening_from": None,
        }
        data.update(overrides)
        return argparse.Namespace(**data)

    def fake_client(self, items):
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

    def only_run_id(self) -> str:
        runs = [path.name for path in (self.data_dir / "runs").iterdir() if path.is_dir()]
        fresh = [name for name in runs if not name.startswith("run-old")]
        self.assertEqual(len(fresh), 1, fresh)
        return fresh[0]

    def test_all_candidates_in_cooldown_make_zero_model_calls(self) -> None:
        self.seed_published("run-old", "a/one", days=1)
        items = [make_item("a/one"), make_item("A/One")]
        screening_call = mock.Mock()
        with mock.patch.object(cli, "_client", return_value=self.fake_client(items)), \
             mock.patch.object(cli, "_run_screening_stage", screening_call), \
             mock.patch.object(cli, "build_adapter", mock.Mock(side_effect=AssertionError("不得调用模型"))):
            # Explicit direction retains the legacy single-query/raw-row semantics;
            # default broad identity deduplication is covered by discovery tests.
            code = cli.cmd_run(self.settings, self.run_args(topic='agent-memory'))

        self.assertEqual(code, cli.EXIT_GATE_FAILED)
        self.assertFalse(screening_call.called, "全部候选在冷却期内时不得调用初筛模型")
        run_id = self.only_run_id()
        context = json.loads(
            (self.data_dir / "runs" / run_id / "run-context.json").read_text(encoding="utf-8")
        )
        self.assertEqual(context["stage"], "cooldown-skipped")
        self.assertEqual(context["target"], "")
        self.assertEqual(len(context["cooldown"]["skipped"]), 2, "大小写不同的同一项目都要记下来")
        self.assertEqual(context["cooldown"]["config"], {"success_days": 30, "failure_days": 7})
        self.assertIn("跳过", context["cooldown"]["summary"])
        usage = json.loads(
            (self.data_dir / "runs" / run_id / "usage.json").read_text(encoding="utf-8")
        )
        self.assertEqual(usage["status"], "all-candidates-in-cooldown")
        self.assertEqual(usage["budget"]["used"], 0)
        row = self.store.conn.execute(
            "SELECT status, notes FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        self.assertEqual(row["status"], "all-candidates-in-cooldown")
        self.assertIn("模型调用 0 次", row["notes"])
        skipped_rows = self.store.conn.execute(
            "SELECT full_name FROM candidates WHERE run_id = ? AND role = 'skipped-cooldown'",
            (run_id,),
        ).fetchall()
        self.assertEqual(len(skipped_rows), 2)

    def test_partial_skip_filters_screening_but_keeps_comparator_pool(self) -> None:
        self.seed_published("run-old", "a/two", days=2)
        items = [make_item("a/one"), make_item("a/two"), make_item("a/three")]
        screening = ScreeningOutcome(
            entries=[{"repo": "a/one", "decision": "keep", "fit": "high", "reason": "相关", "gate": ""}]
        )
        screening_call = mock.Mock(return_value=screening)
        comparator_pool: list[list[dict]] = []

        def fake_comparators(pool, target, count):
            comparator_pool.append(list(pool))
            return [item for item in pool if item["full_name"] == "a/two"]

        def fake_fetch(url, **kwargs):
            from learnradar.net import FetchError, FetchResult

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

        with mock.patch.object(cli, "_client", return_value=self.fake_client(items)), \
             mock.patch.object(cli, "_run_screening_stage", screening_call), \
             mock.patch.object(cli, "_auto_comparators", side_effect=fake_comparators), \
             mock.patch("learnradar.collector.fetch", side_effect=fake_fetch), \
             mock.patch.object(cli, "build_adapter", side_effect=ModelError("测试用：无真实模型")):
            code = cli.cmd_run(self.settings, self.run_args())

        self.assertNotEqual(code, 0, "没有真实模型时按规则停住，不伪造报告")
        passed = [item["full_name"] for item in screening_call.call_args.kwargs["candidates"]]
        self.assertEqual(passed, ["a/one", "a/three"], "冷却期内的候选不进初筛")
        self.assertTrue(comparator_pool, "对照选择必须被调用")
        self.assertIn("a/two", [item["full_name"] for item in comparator_pool[0]], "被跳过的项目仍可作为对照")
        run_id = self.only_run_id()
        skipped_rows = self.store.conn.execute(
            "SELECT full_name FROM candidates WHERE run_id = ? AND role = 'skipped-cooldown'",
            (run_id,),
        ).fetchall()
        self.assertEqual([row["full_name"] for row in skipped_rows], ["a/two"])


if __name__ == "__main__":
    unittest.main()
