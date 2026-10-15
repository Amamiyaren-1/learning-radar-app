"""Offline analysis, publication gate, and rendering without real model credentials.
All evidence is synthetic and marked as fixtures; never present it as real evidence."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from learnradar.analysis import run_analysis
from learnradar.config import CandidateModel, load_settings
from learnradar.evidence import Evidence
from learnradar.models import OfflineFixtureAdapter
from learnradar.report import meta_block, render_markdown
from learnradar.validate import validate_report
from learnradar.web import markdown_to_html

FIXTURES = Path(__file__).parent / "fixtures"
TARGET_REPO = "example-offline/target"


def load_fixture_evidence() -> list[Evidence]:
    raw = json.loads((FIXTURES / "offline_evidence.json").read_text(encoding="utf-8"))
    return [Evidence(**item) for item in raw]


class OfflinePipelineTests(unittest.TestCase):
    def test_full_offline_path(self) -> None:
        settings = load_settings()
        evidence = load_fixture_evidence()
        candidate = CandidateModel(
            name="offline",
            label="离线夹具（测试用）",
            provider="offline_fixture",
            model_id="offline-fixture-v1",
        )
        adapter = OfflineFixtureAdapter(candidate, fixture_path=FIXTURES / "offline_analysis.json")

        outcome = run_analysis(
            adapter,
            settings,
            profile={"profile_id": "test"},
            profile_confirmed=False,
            target={"repo": TARGET_REPO, "role": "target", "facts": {}},
            comparators=[{"repo": "example-offline/baseline-a"}, {"repo": "example-offline/baseline-b"}],
            evidence=evidence,
            max_calls=1,
        )
        self.assertTrue(outcome.fixture, "夹具输出必须被标记为 fixture")
        self.assertEqual(outcome.attempts, 1)

        validation = validate_report(
            outcome.report,
            evidence=evidence,
            target_repo=TARGET_REPO,
            collected_repos=[TARGET_REPO, "example-offline/baseline-a", "example-offline/baseline-b"],
            require_quotes=True,
        )
        self.assertTrue(validation.ok, msg=validation.errors)

        meta = meta_block(
            run_id="offline-test",
            created_at="2026-09-21 00:00:00",
            model_label=candidate.label,
            model_id=candidate.model_id,
            prompt_version="analysis-1",
            rules_version=validation.rules_version,
            profile_label="test（未确认）",
            discovery={"summary": "离线夹具，没有真实搜索"},
            usage={"calls": 1, "input_tokens": None, "output_tokens": None, "cost_value": None, "latency_ms": 0},
            http_summary={"requests": 0, "ok": 0, "failed": 0, "bytes": 0},
            fixture=True,
            comparators=["example-offline/baseline-a", "example-offline/baseline-b"],
        )
        self.assertIn("离线夹具", meta["数据来源标记"])

        markdown = render_markdown(
            report=outcome.report, evidence=evidence, meta=meta, validation=validation
        )
        self.assertIn("**与相关项目的对比（含为什么选这些对照）**", markdown)
        self.assertIn("example-offline/baseline-a", markdown)
        self.assertIn("待观察", markdown)
        html = markdown_to_html(markdown)
        self.assertIn("<table>", html)

    def test_fixture_report_is_rejected_when_star_count_is_faked(self) -> None:
        settings = load_settings()
        evidence = load_fixture_evidence()
        candidate = CandidateModel(
            name="offline", label="离线夹具", provider="offline_fixture", model_id="offline-fixture-v1"
        )
        adapter = OfflineFixtureAdapter(candidate, fixture_path=FIXTURES / "offline_analysis.json")
        outcome = run_analysis(
            adapter,
            settings,
            profile={"profile_id": "test"},
            profile_confirmed=False,
            target={"repo": TARGET_REPO},
            comparators=[],
            evidence=evidence,
            max_calls=1,
        )
        outcome.report["claims"][0]["text"] = "夹具记录为 88888 星（测试数据）。"
        validation = validate_report(
            outcome.report,
            evidence=evidence,
            target_repo=TARGET_REPO,
            collected_repos=[TARGET_REPO, "example-offline/baseline-a", "example-offline/baseline-b"],
            require_quotes=True,
        )
        self.assertFalse(validation.ok)


if __name__ == "__main__":
    unittest.main()
