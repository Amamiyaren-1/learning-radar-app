"""Test analysis failure classification: compact empty/truncated output; repair only invalid JSON."""

from __future__ import annotations

import json
import unittest

from learnradar.analysis import AnalysisError, run_analysis
from learnradar.config import load_settings
from learnradar.models import ModelResponse, Usage

REPORT = {
    "title": "t",
    "target": {"repo": "owner/target", "version": "v1", "category": ["a"], "summary_one_line": "s"},
    "novelty": {"what": "w", "when": "2026-01", "kind": "unclear", "evidence_ids": ["E1"], "quote": "q"},
    "claims": [{"type": "fact", "text": "t", "evidence_ids": ["E1"], "quote": "q"}],
    "comparisons": [],
    "value_for_user": {"learn": "l", "demo": "d", "artifact": "a"},
    "difficulty": {"level": "run_example", "basis": "b", "environment": "e", "hardware": "h", "external_cost": "c"},
    "learning_action": {"executable": True, "action": "a", "done_criteria": "d", "entry": "https://example.com"},
    "recommendation": {"decision": "watch", "reason": "r"},
}


class StubAdapter:
    """Script text/finish_reason responses and retain received prompts."""

    def __init__(self, script: list[tuple[str, str]]) -> None:
        self.script = list(script)
        self.prompts: list[str] = []

    def complete(self, *, system: str, user: str, max_output_tokens: int | None = None) -> ModelResponse:
        self.prompts.append(user)
        index = min(len(self.prompts) - 1, len(self.script) - 1)
        text, finish_reason = self.script[index]
        return ModelResponse(
            text=text,
            usage=Usage(input_tokens=10, output_tokens=5, reasoning_tokens=5, raw={}),
            model_id="stub",
            provider="chat_completions",
            latency_ms=1,
            endpoint="stub://",
            raw={},
            finish_reason=finish_reason,
        )


class FailurePolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = load_settings()

    def run_it(self, script: list[tuple[str, str]], *, max_calls: int = 2):
        adapter = StubAdapter(script)
        try:
            outcome = run_analysis(
                adapter,
                self.settings,
                profile={"profile_id": "t"},
                profile_confirmed=False,
                target={"repo": "owner/target"},
                comparators=[],
                evidence=[],
                max_calls=max_calls,
            )
            return adapter, outcome, None
        except AnalysisError as exc:
            return adapter, None, exc

    def test_empty_output_switches_strategy_instead_of_resending_same_prompt(self) -> None:
        adapter, outcome, error = self.run_it(
            [("", "length"), (json.dumps(REPORT, ensure_ascii=False), "stop")]
        )
        self.assertIsNone(error)
        self.assertEqual(outcome.retry_kind, "empty_output")
        self.assertEqual(len(adapter.prompts), 2)
        self.assertNotEqual(adapter.prompts[0], adapter.prompts[1])
        self.assertIn("输出策略调整", adapter.prompts[1])
        self.assertIn("empty_output", adapter.prompts[1])

    def test_truncated_output_counted_as_truncated_kind(self) -> None:
        adapter, outcome, error = self.run_it(
            [("{\"title\": \"x", "length"), (json.dumps(REPORT, ensure_ascii=False), "stop")]
        )
        self.assertIsNone(error)
        self.assertEqual(outcome.retry_kind, "truncated")

    def test_second_empty_output_stops_and_reports_kind(self) -> None:
        adapter, outcome, error = self.run_it([("", "length"), ("", "length")])
        self.assertIsNone(outcome)
        self.assertEqual(error.kind, "empty_output")
        self.assertEqual(error.attempts, 2)
        self.assertEqual(len(adapter.prompts), 2, "不允许多次无意义重发")

    def test_single_call_budget_does_not_retry_empty_output(self) -> None:
        adapter, outcome, error = self.run_it([("", "length")], max_calls=1)
        self.assertEqual(error.kind, "empty_output")
        self.assertEqual(error.attempts, 1)
        self.assertEqual(len(adapter.prompts), 1)

    def test_invalid_json_uses_the_repair_prompt(self) -> None:
        adapter, outcome, error = self.run_it(
            [("这不是 JSON", "stop"), (json.dumps(REPORT, ensure_ascii=False), "stop")]
        )
        self.assertIsNone(error)
        self.assertEqual(outcome.retry_kind, "invalid_json")
        self.assertIn("上一次输出的问题", adapter.prompts[1])
        self.assertNotIn("输出策略调整", adapter.prompts[1])

    def test_two_invalid_json_outputs_stop_with_kind(self) -> None:
        _, outcome, error = self.run_it([("不是 JSON", "stop"), ("还不是 JSON", "stop")])
        self.assertEqual(error.kind, "invalid_json")
        self.assertEqual(error.attempts, 2)


if __name__ == "__main__":
    unittest.main()
