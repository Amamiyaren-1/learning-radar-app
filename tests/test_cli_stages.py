"""CLI 阶段编排测试（离线）：初筛阶段记录用量与失败原因，不换模型。"""

from __future__ import annotations

import dataclasses
import json
import tempfile
import unittest
from unittest import mock

from learnradar import cli
from learnradar.config import CandidateModel, load_settings
from learnradar.models import ModelError, ModelResponse, Usage

CANDIDATES = [
    {
        "full_name": "a/one",
        "description": "说明",
        "topics": ["agent"],
        "language": "Python",
        "stargazers_count": 10,
        "pushed_at": "2026-09-01T00:00:00Z",
        "archived": False,
        "fork": False,
    }
]


class StubAdapter:
    def __init__(self, text: str | None, *, error: str = "") -> None:
        self.text = text
        self.error = error
        self.calls = 0

    def complete(self, *, system: str, user: str, max_output_tokens: int | None = None) -> ModelResponse:
        self.calls += 1
        if self.error:
            raise ModelError(self.error)
        return ModelResponse(
            text=self.text or "{}",
            usage=Usage(input_tokens=7, output_tokens=3, reasoning_tokens=0, raw={}),
            model_id="stub-flash",
            provider="chat_completions",
            latency_ms=9,
            endpoint="stub://",
            raw={},
        )


class ScreeningStageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = load_settings()
        self.settings = dataclasses.replace(base, app={**base.app, "data_dir": self.tmp.name})
        self.model = CandidateModel(
            name="flash",
            label="V4.1 Flash（初筛）",
            provider="chat_completions",
            model_id="deepseek-flash",
            base_url="https://api.deepseek.com/v1",
            api_key_env="LEARNRADAR_UNUSED_KEY",
        )

    def run_stage(self, adapter):
        usage: list[dict] = []
        with mock.patch.object(cli, "build_adapter", return_value=adapter):
            outcome = cli._run_screening_stage(
                self.settings,
                audit=[],
                profile={"profile_id": "test"},
                profile_confirmed=False,
                candidates=CANDIDATES,
                topic={"id": "tool-calling", "label": "工具调用", "github_query": "q"},
                model=self.model,
                usage_entries=usage,
            )
        return outcome, usage

    def test_success_records_stage_model_tokens_latency(self) -> None:
        text = json.dumps(
            {"candidates": [{"repo": "a/one", "decision": "keep", "reason": "相关"}]},
            ensure_ascii=False,
        )
        outcome, usage = self.run_stage(StubAdapter(text))
        self.assertFalse(outcome.failed)
        self.assertEqual(len(usage), 1)
        entry = usage[0]
        self.assertEqual(entry["stage"], "screening")
        self.assertEqual(entry["role"], "screening")
        self.assertEqual(entry["model_id"], "deepseek-flash")
        self.assertEqual(entry["reported_model_id"], "stub-flash")
        self.assertTrue(entry["model_mismatch"], "接口回报的模型与配置不一致时必须可见")
        self.assertEqual(entry["input_tokens"], 7)
        self.assertEqual(entry["latency_ms"], 9)
        self.assertTrue(entry["ok"])

    def test_failure_records_reason_and_keeps_candidates(self) -> None:
        outcome, usage = self.run_stage(StubAdapter(None, error="模拟 500 错误"))
        self.assertTrue(outcome.failed)
        self.assertEqual(outcome.retained_names, ["a/one"])
        failed = [item for item in usage if not item["ok"]]
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0]["stage"], "screening")
        self.assertIn("模拟 500 错误", failed[0]["error"])
        self.assertEqual(failed[0]["calls"], 1, "失败也消耗一次模型请求预算")

    def test_stage_usage_rows_group_and_summarise(self) -> None:
        entries = [
            {"stage": "screening", "model_id": "flash", "calls": 1, "ok": True, "input_tokens": 10, "output_tokens": 2, "reasoning_tokens": 0, "latency_ms": 100},
            {"stage": "screening", "model_id": "flash", "calls": 0, "ok": False, "error": "boom"},
            {"stage": "analysis", "model_id": "pro", "calls": 1, "ok": True, "input_tokens": 20, "output_tokens": 5, "reasoning_tokens": 4, "latency_ms": 200},
        ]
        rows = cli._stage_usage_rows(entries)
        self.assertEqual([row["stage"] for row in rows], ["analysis", "screening"])
        screening = next(row for row in rows if row["stage"] == "screening")
        self.assertEqual(screening["calls"], 1)
        self.assertEqual(screening["failed"], 1)
        self.assertEqual(screening["input_tokens"], 10)
        self.assertEqual(screening["error"], "boom")
        analysis = next(row for row in rows if row["stage"] == "analysis")
        self.assertEqual(analysis["reasoning_tokens"], 4)

    def test_failure_entry_is_explicit(self) -> None:
        entry = cli._failure_entry("analysis", self.model, "超时", role="analysis")
        self.assertFalse(entry["ok"])
        self.assertEqual(entry["calls"], 0)
        self.assertEqual(entry["error"], "超时")
        self.assertEqual(entry["model_id"], "deepseek-flash")


    def test_budget_counts_failures_and_blocks_when_exhausted(self) -> None:
        budget = cli.ModelBudget(1)
        inner = StubAdapter(None, error="boom")
        wrapped = cli.BudgetedAdapter(inner, budget)
        with self.assertRaises(cli.ModelError):
            wrapped.complete(system="s", user="u")
        self.assertEqual(budget.used, 1)
        with self.assertRaises(cli.ModelBudgetExceeded):
            wrapped.complete(system="s", user="u")
        self.assertEqual(inner.calls, 1, "预算用尽后不得再发出请求")

    def test_budget_is_shared_and_reports_remaining(self) -> None:
        budget = cli.ModelBudget(2)
        budget.consume()
        self.assertEqual(budget.remaining, 1)
        budget.consume()
        self.assertEqual(budget.remaining, 0)
        with self.assertRaises(cli.ModelBudgetExceeded):
            budget.consume()
        self.assertEqual(budget.describe(), "2/2")

    def test_screening_stage_records_request_count(self) -> None:
        text = json.dumps(
            {"candidates": [{"repo": "a/one", "decision": "keep", "reason": "相关"}]},
            ensure_ascii=False,
        )
        budget = cli.ModelBudget(4)
        usage: list[dict] = []
        adapter = StubAdapter(text)
        with mock.patch.object(cli, "build_adapter", return_value=adapter):
            outcome = cli._run_screening_stage(
                self.settings,
                audit=[],
                profile={"profile_id": "t"},
                profile_confirmed=False,
                candidates=CANDIDATES,
                topic={"id": "t", "label": "l", "github_query": "q"},
                model=self.model,
                usage_entries=usage,
                budget=budget,
            )
        self.assertEqual(outcome.requests, 1)
        self.assertEqual(budget.used, 1)
        self.assertEqual(usage[0]["stage"], "screening")

    def test_failure_rows_keep_earlier_stage_records(self) -> None:
        prior = [
            {
                "stage": "screening",
                "role": "screening",
                "model_id": "deepseek-flash",
                "calls": 1,
                "ok": True,
                "input_tokens": 100,
                "output_tokens": 20,
                "reasoning_tokens": 0,
                "latency_ms": 500,
            }
        ]
        response = StubAdapter('{}').complete(system="s", user="u")
        rows = cli._failure_usage_rows(
            prior,
            [response],
            self.model,
            "输出被截断",
            unrecorded_calls=1,
        )
        stages = [row["stage"] for row in rows]
        self.assertEqual(stages, ["screening", "analysis", "analysis"])
        self.assertEqual(rows[0]["model_id"], "deepseek-flash")
        self.assertEqual(rows[-1]["calls"], 1)
        self.assertFalse(rows[-1]["ok"])
        self.assertEqual(rows[-1]["error"], "输出被截断")
        totals = cli._aggregate_usage(rows)
        self.assertEqual(totals["input_tokens"], 107, "初筛与分析 token 都要保留")

if __name__ == "__main__":
    unittest.main()
