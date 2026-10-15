"""Test safe CLI finalization, persist-before-render ordering, and explicit terminal states.
Malformed structure must not lose usage or terminal state."""

from __future__ import annotations

import dataclasses
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from learnradar import cli
from learnradar.config import CandidateModel, load_settings
from learnradar.evidence import Evidence
from learnradar.store import Store

FIXTURES = Path(__file__).parent / "fixtures"
TARGET = "example-offline/target"
COMPARATORS = ["example-offline/baseline-a", "example-offline/baseline-b"]


def load_evidence() -> list[Evidence]:
    raw = json.loads((FIXTURES / "offline_evidence.json").read_text(encoding="utf-8"))
    return [Evidence(**item) for item in raw]


def load_report() -> dict:
    return json.loads((FIXTURES / "offline_analysis.json").read_text(encoding="utf-8"))


class FinalizeRunTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = load_settings()
        self.settings = dataclasses.replace(base, app={**base.app, "data_dir": self.tmp.name})
        self.evidence = load_evidence()
        self.candidate = CandidateModel(
            name="offline", label="离线夹具", provider="offline_fixture", model_id="offline-fixture-v1"
        )
        self.usage = [
            {
                "ts": "2026-09-21T00:00:00+00:00",
                "stage": "analysis",
                "calls": 1,
                "provider": "offline_fixture",
                "model_id": "offline-fixture-v1",
                "input_tokens": None,
                "output_tokens": None,
                "reasoning_tokens": None,
                "latency_ms": 0,
                "cost_value": None,
                "cost_currency": None,
                "pricing_known": False,
            }
        ]
        self.audit = [
            {"ts": "2026-09-21T00:00:00+00:00", "method": "GET", "url": "https://example.com", "status": 200, "ms": 5, "bytes": 10}
        ]

    def _finalize(self, report, **overrides):
        kwargs = dict(
            run_id=overrides.pop("run_id", "test-run"),
            report=report,
            evidence=self.evidence,
            target_name=TARGET,
            comparator_names=COMPARATORS,
            candidate=self.candidate,
            profile={"profile_id": "test"},
            profile_confirmed=False,
            topic={"id": "offline"},
            query="fixture",
            selection_note="夹具",
            discovered_count=0,
            client_mode="public",
            routes_text="fixture",
            usage_entries=self.usage,
            audit=self.audit,
            fixture=True,
        )
        kwargs.update(overrides)
        return cli.finalize_run(self.settings, **kwargs)

    def test_valid_report_is_published(self) -> None:
        status, validation, md_path, meta = self._finalize(load_report())
        self.assertEqual(status, "published")
        self.assertTrue(validation.ok, msg=validation.errors)
        self.assertTrue(md_path.exists())
        self.assertIn("**与相关项目的对比（含为什么选这些对照）**", md_path.read_text(encoding="utf-8"))
        self.assertIn("数据来源标记", " ".join(meta.keys()))

    def test_bad_structure_is_rejected_and_state_is_persisted(self) -> None:
        report = load_report()
        report["target"] = ["not", "a", "dict"]
        status, validation, md_path, meta = self._finalize(report, run_id="bad-structure")
        self.assertEqual(status, "rejected")
        self.assertFalse(validation.ok)

        runs_dir = Path(self.tmp.name) / "runs" / "bad-structure"
        for name in ("usage.json", "audit.json", "evidence.json", "validation.json", "report.json"):
            self.assertTrue((runs_dir / name).exists(), f"{name} 必须落盘")
        self.assertTrue(md_path.exists())
        markdown = md_path.read_text(encoding="utf-8")
        self.assertIn("未通过结构检查", markdown)
        self.assertNotIn("**与相关项目的对比（含为什么选这些对照）**", markdown, "坏结构不得进入正常报告渲染")

        with Store(Path(self.tmp.name) / "learning-radar.sqlite") as store:
            self.assertEqual(store.get_report("bad-structure")["status"], "rejected")
        usage = json.loads((runs_dir / "usage.json").read_text(encoding="utf-8"))
        self.assertEqual(usage["totals"]["calls"], 1)

    def test_render_failure_keeps_usage_and_terminal_state(self) -> None:
        with mock.patch.object(cli, "render_markdown", side_effect=RuntimeError("boom")):
            status, _, md_path, _ = self._finalize(load_report(), run_id="render-boom")
        self.assertEqual(status, "render-failed")
        self.assertEqual(md_path, Path(""))
        runs_dir = Path(self.tmp.name) / "runs" / "render-boom"
        self.assertTrue((runs_dir / "usage.json").exists())
        self.assertTrue((runs_dir / "render-error.txt").exists())
        self.assertIn("boom", (runs_dir / "render-error.txt").read_text(encoding="utf-8"))
        with Store(Path(self.tmp.name) / "learning-radar.sqlite") as store:
            self.assertEqual(store.get_report("render-boom")["status"], "render-failed")

    def test_model_claimed_verification_is_overridden_and_flagged(self) -> None:
        report = load_report()
        report["verification"] = {
            "docs_reviewed": False,
            "source_reviewed": True,
            "actually_ran": True,
            "note": "模型自报读过源码并运行过",
        }
        status, validation, _, _ = self._finalize(report, run_id="claim-check")
        self.assertEqual(status, "published")
        self.assertTrue(any("模型自报的查阅状态" in item for item in validation.warnings), msg=validation.warnings)
        stored = json.loads(
            (Path(self.tmp.name) / "runs" / "claim-check" / "report.json").read_text(encoding="utf-8")
        )
        self.assertFalse(stored["verification"]["actually_ran"])
        self.assertFalse(stored["verification"]["source_reviewed"])
        self.assertTrue(stored["model_claimed_verification"]["actually_ran"])


if __name__ == "__main__":
    unittest.main()
