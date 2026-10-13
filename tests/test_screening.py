"""初筛阶段测试：保留不确定候选、排除必须有理由、失败不换模型。"""

from __future__ import annotations

import json
import unittest

from learnradar.models import ModelError, ModelResponse, Usage
from learnradar.screening import KEEP_DECISIONS, run_screening

PROFILE = {"profile_id": "test", "goals": {"current": "学习 Agent 工程"}}
TOPIC = {"id": "tool-calling", "label": "工具调用", "github_query": "agent tool calling"}


def candidate(repo: str) -> dict:
    return {
        "full_name": repo,
        "description": f"{repo} 的说明",
        "topics": ["agent"],
        "language": "Python",
        "stargazers_count": 100,
        "pushed_at": "2026-09-01T00:00:00Z",
        "archived": False,
        "fork": False,
    }


class StubAdapter:
    """按脚本返回文本；记录被调用次数，用于证明没有发生模型切换。"""

    def __init__(self, texts: list[str], *, model_id: str = "stub-screening-model") -> None:
        self.texts = list(texts)
        self.model_id = model_id
        self.calls = 0

    def complete(self, *, system: str, user: str, max_output_tokens: int | None = None) -> ModelResponse:
        self.calls += 1
        if self.calls > len(self.texts):
            raise AssertionError("调用次数超出预期脚本")
        text = self.texts[self.calls - 1]
        if text == "__RAISE__":
            raise ModelError("模拟模型失败：连接被拒绝")
        return ModelResponse(
            text=text,
            usage=Usage(input_tokens=10, output_tokens=5, reasoning_tokens=0, raw={}),
            model_id=self.model_id,
            provider="chat_completions",
            latency_ms=12,
            endpoint="stub://",
            raw={},
        )


def payload(entries: list[dict]) -> str:
    return json.dumps({"candidates": entries, "notes": ""}, ensure_ascii=False)


class ScreeningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.candidates = [candidate("a/one"), candidate("b/two"), candidate("c/three")]

    def screen(self, texts: list[str], **kwargs):
        adapter = StubAdapter(texts)
        outcome = run_screening(
            adapter,
            profile=PROFILE,
            profile_confirmed=False,
            candidates=self.candidates,
            topic=TOPIC,
            max_keep=kwargs.pop("max_keep", 8),
            max_calls=kwargs.pop("max_calls", 2),
            **kwargs,
        )
        return adapter, outcome

    def test_keep_uncertain_and_exclude_with_reason(self) -> None:
        adapter, outcome = self.screen(
            [
                payload(
                    [
                        {"repo": "a/one", "decision": "keep", "reason": "与目标直接相关"},
                        {"repo": "b/two", "decision": "uncertain", "reason": "看起来相关但说明太少"},
                        {"repo": "c/three", "decision": "exclude", "reason": "纯教程仓库，不是工具"},
                    ]
                )
            ]
        )
        self.assertFalse(outcome.failed)
        self.assertEqual(outcome.retained_names, ["a/one", "b/two"])
        self.assertEqual([item["repo"] for item in outcome.excluded], ["c/three"])
        self.assertIn("纯教程仓库", outcome.excluded[0]["reason"])
        self.assertEqual(adapter.calls, 1, "一次调用就够，不应重试或换模型")

    def test_missing_candidate_is_retained_as_uncertain(self) -> None:
        _, outcome = self.screen(
            [payload([{"repo": "a/one", "decision": "keep", "reason": "相关"}])]
        )
        self.assertIn("b/two", outcome.retained_names)
        self.assertIn("c/three", outcome.retained_names)
        self.assertTrue(any("缺失" in item for item in outcome.warnings))

    def test_exclusion_without_reason_is_downgraded_to_uncertain(self) -> None:
        _, outcome = self.screen(
            [payload([{"repo": "a/one", "decision": "exclude", "reason": ""}])]
        )
        self.assertIn("a/one", outcome.retained_names)
        self.assertTrue(any("没有写理由" in item for item in outcome.warnings))

    def test_invalid_decision_is_downgraded_to_uncertain(self) -> None:
        _, outcome = self.screen([payload([{"repo": "a/one", "decision": "maybe", "reason": "?"}])])
        self.assertIn("a/one", outcome.retained_names)
        self.assertTrue(any("不是合法取值" in item for item in outcome.warnings))

    def test_unknown_repo_is_ignored_with_warning(self) -> None:
        _, outcome = self.screen(
            [
                payload(
                    [
                        {"repo": "a/one", "decision": "keep", "reason": "相关"},
                        {"repo": "ghost/never-seen", "decision": "keep", "reason": "幻觉仓库"},
                    ]
                )
            ]
        )
        self.assertEqual([item["repo"] for item in outcome.entries], ["a/one", "b/two", "c/three"])
        self.assertTrue(any("本次候选之外" in item for item in outcome.warnings))

    def test_model_failure_keeps_all_candidates_and_does_not_switch_model(self) -> None:
        adapter, outcome = self.screen(["__RAISE__"])
        self.assertTrue(outcome.failed)
        self.assertEqual(len(outcome.entries), 3)
        self.assertTrue(all(item["decision"] == "uncertain" for item in outcome.entries))
        self.assertIn("连接被拒绝", outcome.error)
        self.assertEqual(adapter.calls, 1, "失败后不得换别的模型重试")
        self.assertIn("初筛失败", outcome.summary())

    def test_unparseable_output_triggers_one_repair_call(self) -> None:
        adapter, outcome = self.screen(
            [
                "这不是 JSON",
                payload([{"repo": "a/one", "decision": "keep", "reason": "相关"}]),
            ]
        )
        self.assertFalse(outcome.failed)
        self.assertEqual(outcome.attempts, 2)
        self.assertEqual(adapter.calls, 2)

    def test_two_unparseable_outputs_end_as_failed_keep_all(self) -> None:
        adapter, outcome = self.screen(["不是 JSON", "还不是 JSON"])
        self.assertTrue(outcome.failed)
        self.assertEqual(adapter.calls, 2)
        self.assertEqual(len(outcome.retained_names), 3)

    def test_prompt_note_states_it_is_not_evidence(self) -> None:
        _, outcome = self.screen([payload([])])
        note = outcome.prompt_note()
        self.assertIn("不是证据", note)

    def test_keep_decisions_constant(self) -> None:
        self.assertEqual(set(KEEP_DECISIONS), {"keep", "uncertain"})


    def test_parseable_but_wrong_shape_output_keeps_all_candidates(self) -> None:
        # 两次都返回合法 JSON、但没有 candidates 数组：必须安全失败，不能抛异常
        adapter, outcome = self.screen(['{"foo": 1}', '{"bar": 2}'])
        self.assertTrue(outcome.failed)
        self.assertEqual(adapter.calls, 2)
        self.assertEqual(len(outcome.retained_names), 3)
        self.assertIn("candidates", outcome.error)

    def test_gate_is_kept_in_the_record(self) -> None:
        payload = json.dumps(
            {"candidates": [{"repo": "a/one", "decision": "keep", "fit": "high",
                             "reason": "领域相关且可先跑示例", "gate": "需先确认 Node 运行时"}]},
            ensure_ascii=False,
        )
        _, outcome = self.screen([payload])
        self.assertEqual(outcome.entries[0]["gate"], "需先确认 Node 运行时")

    def test_keep_without_reason_is_downgraded(self) -> None:
        payload = json.dumps(
            {"candidates": [{"repo": "a/one", "decision": "keep", "fit": "high", "reason": ""}]},
            ensure_ascii=False,
        )
        _, outcome = self.screen([payload])
        self.assertEqual(outcome.entries[0]["decision"], "uncertain")
        self.assertTrue(any("没有写理由" in item for item in outcome.warnings))

    def test_ranking_orders_keep_then_fit(self) -> None:
        payload = json.dumps(
            {"candidates": [
                {"repo": "a/one", "decision": "uncertain", "fit": "high", "reason": "r"},
                {"repo": "b/two", "decision": "keep", "fit": "low", "reason": "r"},
                {"repo": "c/three", "decision": "keep", "fit": "high", "reason": "r"},
            ]},
            ensure_ascii=False,
        )
        _, outcome = self.screen([payload])
        self.assertEqual([row["repo"] for row in outcome.ranking()], ["c/three", "b/two", "a/one"])

if __name__ == "__main__":
    unittest.main()
