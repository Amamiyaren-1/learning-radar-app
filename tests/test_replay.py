"""Restore replay inputs from saved evidence without network access."""

from __future__ import annotations

import argparse
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from learnradar import cli
from learnradar.config import load_settings
from learnradar.evidence import Evidence
from learnradar.store import Store

FIXTURES = Path(__file__).parent / "fixtures"


def fixtures_evidence() -> list[Evidence]:
    raw = json.loads((FIXTURES / "offline_evidence.json").read_text(encoding="utf-8"))
    return [Evidence(**item) for item in raw]


class ReplayInputTests(unittest.TestCase):
    def setUp(self) -> None:
        self.evidence = fixtures_evidence()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = load_settings()
        self.settings = type(base)(**{**base.__dict__, "app": {**base.app, "data_dir": self.tmp.name}})

    def test_repos_in_evidence_lists_unique_owners(self) -> None:
        repos = cli._repos_in_evidence(self.evidence)
        self.assertEqual(
            repos,
            ["example-offline/target", "example-offline/baseline-a", "example-offline/baseline-b"],
        )

    def test_repo_facts_are_taken_from_saved_repo_evidence(self) -> None:
        facts = cli._repo_facts_from_evidence(self.evidence, "example-offline/target")
        self.assertEqual(facts.get("stars"), 1234)
        self.assertIn("full_name", facts)

    def test_learning_entry_reconstruction_flags_example_signal(self) -> None:
        evidence = list(self.evidence)
        evidence.append(
            Evidence(
                evidence_id="E-page-x",
                kind="web_page",
                url="https://example.com/docs/quickstart",
                title="示例页",
                retrieved_at="2026-09-22T00:00:00+00:00",
                owner="example-offline/target",
                notes="这是本次学习动作实际读取的入口页面；页面里检测到示例信号（本次未实际运行示例）。",
            )
        )
        entry = cli._learning_entry_from_evidence(evidence, "example-offline/target")
        self.assertTrue(entry["verified"])
        self.assertTrue(entry["example_signal"])
        self.assertEqual(entry["url"], "https://example.com/docs/quickstart")

    def test_missing_learning_entry_is_reported_not_guessed(self) -> None:
        entry = cli._learning_entry_from_evidence(self.evidence, "example-offline/target")
        self.assertFalse(entry["verified"])
        self.assertIn("没有保存的学习入口证据", entry["error"])

    def test_replay_without_saved_run_reports_usage_error(self) -> None:
        args = type("Args", (), {"run": "not-a-run", "target": None, "compare": None, "model": None, "run_id": None})()
        self.assertEqual(cli.cmd_replay_analysis(self.settings, args), cli.EXIT_USAGE)

    def test_replay_builds_payload_without_network(self) -> None:
        """Recover target/comparators from evidence.json in an isolated fixture run directory."""
        run_dir = Path(self.tmp.name) / "runs" / "fake-run"
        run_dir.mkdir(parents=True)
        (run_dir / "evidence.json").write_text(
            json.dumps([item.to_dict() for item in self.evidence], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        loaded = [Evidence(**item) for item in json.loads((run_dir / "evidence.json").read_text(encoding="utf-8"))]
        self.assertEqual(len(loaded), len(self.evidence))
        self.assertEqual(cli._repos_in_evidence(loaded)[0], "example-offline/target")


    def test_load_candidates_from_run(self) -> None:
        with Store(Path(self.tmp.name) / "learning-radar.sqlite") as store:
            store.start_run("run-x", kind="test", started_at="t", snapshot={})
            store.save_candidates(
                "run-x",
                [{"id": 1, "full_name": "a/one", "html_url": "https://github.com/a/one",
                  "stargazers_count": 5, "pushed_at": "2026-09-01T00:00:00Z", "description": "d"}],
                role="discovered",
            )
        items = cli._load_candidates_from_run(self.settings, "run-x")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["full_name"], "a/one")

    def test_load_screening_from_run_round_trip(self) -> None:
        run_dir = Path(self.tmp.name) / "runs" / "run-y"
        run_dir.mkdir(parents=True)
        (run_dir / "run-context.json").write_text(
            json.dumps(
                {
                    "screening": {
                        "entries": [{"repo": "a/one", "decision": "keep", "fit": "high", "reason": "r", "gate": "g"}],
                        "failed": False,
                        "error": "",
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        outcome = cli._load_screening_from_run(self.settings, "run-y")
        self.assertFalse(outcome.failed)
        self.assertEqual(outcome.retained_names, ["a/one"])

    def test_load_screening_from_missing_run_raises(self) -> None:
        from learnradar.config import ConfigError

        with self.assertRaises(ConfigError):
            cli._load_screening_from_run(self.settings, "missing-run")


class ReplayEndToEndTests(unittest.TestCase):
    """Fake-model offline replay regression for undefined runs_dir/model_budget failures."""

    def setUp(self) -> None:
        # Only fake-adapter replay tests bypass the credential-presence precheck.
        credentials = mock.patch('learnradar.config.CandidateModel.api_key',
                                 return_value='offline-test-placeholder')
        credentials.start()
        self.addCleanup(credentials.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = load_settings()
        self.settings = type(base)(**{**base.__dict__, "app": {**base.app, "data_dir": self.tmp.name}})
        self.data_dir = Path(self.tmp.name)
        self.source_run = "20260927T215205-agent-memory"
        run_dir = self.data_dir / "runs" / self.source_run
        run_dir.mkdir(parents=True)
        (run_dir / "evidence.json").write_text(
            (FIXTURES / "offline_evidence.json").read_text(encoding="utf-8"), encoding="utf-8"
        )
        (run_dir / "run-context.json").write_text(
            json.dumps(
                {
                    "target": "example-offline/target",
                    "comparators": ["example-offline/baseline-a", "example-offline/baseline-b"],
                    "topic": "agent-memory",
                    "query": "fixture",
                    "screening": {"entries": [], "failed": False, "error": "", "note": "夹具"},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def _source_files(self) -> dict[str, bytes]:
        run_dir = self.data_dir / "runs" / self.source_run
        return {path.name: path.read_bytes() for path in sorted(run_dir.iterdir())}

    def test_replay_publishes_writes_usage_and_leaves_source_untouched(self) -> None:
        from tests.test_weekly import FakeAdapter

        class FixtureAdapter(FakeAdapter):
            def complete(self, **kwargs):
                response = super().complete(**kwargs)
                response.fixture = True
                return response

        before = self._source_files()
        adapter = FixtureAdapter(
            "example-offline/target",
            ["example-offline/baseline-a", "example-offline/baseline-b"],
        )
        args = argparse.Namespace(
            run=self.source_run, target=None, compare=None, model=None, run_id="replay-regression-1"
        )
        with mock.patch.object(cli, "build_adapter", return_value=adapter), mock.patch(
            "socket.create_connection", side_effect=AssertionError("离线测试禁止网络")
        ), mock.patch("socket.socket.connect", side_effect=AssertionError("离线测试禁止网络")):
            code = cli.cmd_replay_analysis(self.settings, args)

        self.assertEqual(code, 0, "重放应当能正常发布（不再因未定义名崩溃）")
        self.assertEqual(adapter.calls, 1, "只调用一次分析模型，且是假模型")
        new_dir = self.data_dir / "runs" / "replay-regression-1"
        usage = json.loads((new_dir / "usage.json").read_text(encoding="utf-8"))
        self.assertEqual(usage["totals"]["calls"], 1, "分析用量要落在新运行目录里")
        validation = json.loads((new_dir / "validation.json").read_text(encoding="utf-8"))
        self.assertTrue(validation["ok"], validation)
        self.assertTrue((new_dir / "report.json").exists())
        context = json.loads((new_dir / "run-context.json").read_text(encoding="utf-8"))
        self.assertEqual(context["replay_of"], self.source_run)
        self.assertEqual(context["target"], "example-offline/target")
        self.assertEqual(context["comparators"], ["example-offline/baseline-a", "example-offline/baseline-b"])
        self.assertIn("learning_entry", context)
        from scripts.offline_recheck import main as recheck
        import contextlib
        import io
        protected = {p.name: p.read_bytes() for p in new_dir.iterdir()}
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(recheck(["replay-regression-1", "--data-dir", str(self.data_dir)]), 0)
        for name, content in protected.items():
            self.assertEqual((new_dir / name).read_bytes(), content)
        self.assertEqual(
            self._source_files(), before, "重放不得改写原运行的 evidence/report/validation"
        )

    def test_replay_saves_effective_overrides_before_fake_model_failure(self) -> None:
        from learnradar.models import ModelError

        before = self._source_files()
        args = argparse.Namespace(
            run=self.source_run, target="example-offline/baseline-a",
            compare=["example-offline/target", "example-offline/baseline-b"],
            model=None, run_id="fixture-replay-failed",
        )

        def unavailable(*args, **kwargs):
            context = json.loads((self.data_dir / "runs" / "fixture-replay-failed" /
                                  "run-context.json").read_text(encoding="utf-8"))
            self.assertEqual(context["target"], "example-offline/baseline-a")
            self.assertEqual(context["comparators"], args_namespace.compare)
            self.assertEqual(context["replay_of"], self.source_run)
            raise ModelError("fixture 假模型不可用，未调用真实模型")

        args_namespace = args
        with mock.patch.object(cli, "build_adapter", side_effect=unavailable):
            code = cli.cmd_replay_analysis(self.settings, args)
        self.assertEqual(code, cli.EXIT_MODEL_UNAVAILABLE)
        self.assertEqual(self._source_files(), before)


if __name__ == "__main__":
    unittest.main()
