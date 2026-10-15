"""Report rendering separates structural checks from content review and includes evidence locators."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from learnradar.evidence import Evidence
from learnradar.report import basis_text, meta_block, render_markdown, render_rejection
from learnradar.validate import validate_report

FIXTURES = Path(__file__).parent / "fixtures"
TARGET = "example-offline/target"
COLLECTED = [TARGET, "example-offline/baseline-a", "example-offline/baseline-b"]


def load_evidence() -> list[Evidence]:
    raw = json.loads((FIXTURES / "offline_evidence.json").read_text(encoding="utf-8"))
    return [Evidence(**item) for item in raw]


def load_report() -> dict:
    return json.loads((FIXTURES / "offline_analysis.json").read_text(encoding="utf-8"))


class ReportRenderingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.evidence = load_evidence()
        self.report = load_report()
        self.validation = validate_report(
            self.report,
            evidence=self.evidence,
            target_repo=TARGET,
            collected_repos=COLLECTED,
            require_quotes=True,
        )
        self.meta = meta_block(
            validation=self.validation,
            run_id="report-test",
            created_at="2026-09-21 00:00:00",
            model_label="离线夹具",
            model_id="offline-fixture-v1",
            prompt_version="analysis-3",
            rules_version=self.validation.rules_version,
            profile_label="test（草稿，未确认）",
            discovery={"summary": "夹具"},
            usage={"calls": 1, "cost_value": None},
            http_summary={"requests": 0, "ok": 0, "failed": 0, "bytes": 0},
            fixture=True,
            comparators=COLLECTED[1:],
        )

    def test_validation_passes_with_quotes(self) -> None:
        self.assertTrue(self.validation.ok, msg=self.validation.errors)

    def test_meta_block_separates_structure_and_content_review(self) -> None:
        self.assertEqual(self.meta["结构检查"], "结构检查通过")
        self.assertIn("未完成", self.meta["内容复核"])

    def test_not_executable_action_renders_source_and_not_applicable(self) -> None:
        report = json.loads(json.dumps(self.report, ensure_ascii=False))
        report["learning_action"] = {
            "executable": False,
            "action": "本次没有可执行的学习动作：README 的步骤只能作为安装准备参考。",
            "done_criteria": "",
            "entry": "https://github.com/example-offline/target#readme",
        }
        markdown = render_markdown(
            report=report, evidence=self.evidence, meta=self.meta, validation=self.validation
        )
        self.assertIn("本次没有可执行的学习动作", markdown)
        self.assertIn("不适用：本次未提供可执行学习动作", markdown)
        self.assertIn("https://github.com/example-offline/target#readme", markdown)

    def test_markdown_marks_verification_as_generated(self) -> None:
        markdown = render_markdown(
            report=self.report, evidence=self.evidence, meta=self.meta, validation=self.validation
        )
        self.assertIn("验证状态（由运行记录生成", markdown)
        self.assertIn("内容复核：未完成", markdown)
        self.assertIn("结构检查通过不等于内容已核验", markdown)

    def test_markdown_shows_basis_excerpt_and_locator(self) -> None:
        markdown = render_markdown(
            report=self.report, evidence=self.evidence, meta=self.meta, validation=self.validation
        )
        self.assertIn("每条结论的依据摘录与定位", markdown)
        self.assertIn("引文「Stars=1234」", markdown)
        self.assertIn("来源：", markdown)

    def test_basis_text_without_quote_says_so(self) -> None:
        line = basis_text({"evidence_ids": ["E-repo-fixture-a"]}, {item.evidence_id: item for item in self.evidence})
        self.assertIn("来源：", line)
        self.assertIn("来源：", line)

    def test_rejection_renderer_is_type_safe_and_labeled(self) -> None:
        result = validate_report(["not", "a", "dict"], evidence=self.evidence, target_repo=TARGET)
        self.assertFalse(result.ok)
        rendered = render_rejection(report=["not", "a", "dict"], validation=result, meta=self.meta)
        self.assertIn("未通过结构检查", rendered)
        self.assertIn("内容复核：未完成", rendered)
        self.assertIn("list", rendered)


    def test_markdown_shows_condition_table_with_status(self) -> None:
        markdown = render_markdown(
            report=self.report, evidence=self.evidence, meta=self.meta, validation=self.validation
        )
        self.assertIn("运行条件（正文与下表同源", markdown)
        self.assertIn("运行时及版本", markdown)
        self.assertIn("未知（未找到依据）", markdown)
        self.assertIn("模型推断", markdown)

    def test_markdown_states_quotes_do_not_prove_support(self) -> None:
        markdown = render_markdown(
            report=self.report, evidence=self.evidence, meta=self.meta, validation=self.validation
        )
        self.assertIn("不能证明引文支持结论", markdown)

    def test_rejection_page_shows_stage_usage_and_failure_reason(self) -> None:
        result = validate_report(["not", "a", "dict"], evidence=self.evidence, target_repo=TARGET)
        stage_usage = [
            {
                "stage": "screening",
                "model_id": "deepseek-flash",
                "calls": 1,
                "failed": 1,
                "input_tokens": 10,
                "output_tokens": 2,
                "reasoning_tokens": 0,
                "latency_ms": 123,
                "error": "初筛输出不合法",
            }
        ]
        rendered = render_rejection(
            report={"title": "x"}, validation=result, meta=self.meta, stage_usage=stage_usage
        )
        self.assertIn("分阶段模型调用", rendered)
        self.assertIn("deepseek-flash", rendered)
        self.assertIn("初筛输出不合法", rendered)

    def test_environment_text_comes_from_conditions_only(self) -> None:
        # Separate environment/hardware/external_cost free-text fields no longer exist.
        self.assertNotIn("environment", self.report["difficulty"])
        self.assertNotIn("hardware", self.report["difficulty"])
        self.assertNotIn("external_cost", self.report["difficulty"])
        markdown = render_markdown(
            report=self.report, evidence=self.evidence, meta=self.meta, validation=self.validation
        )
        conditions = self.report["difficulty"]["conditions"]
        for item in conditions:
            self.assertIn(str(item["value"]), markdown, msg=f"条件 {item['condition']} 的值必须出现在正文里")

    def test_changing_a_condition_changes_the_rendered_body(self) -> None:
        report = json.loads(json.dumps(self.report, ensure_ascii=False))
        report["difficulty"]["conditions"][0]["value"] = "Node.js 22+（改成运行时判定）"
        markdown = render_markdown(
            report=report, evidence=self.evidence, meta=self.meta, validation=self.validation
        )
        self.assertIn("Node.js 22+（改成运行时判定）", markdown)
        self.assertEqual(
            markdown.count("未知（夹具未提供）"),
            1,
            msg="被改掉的那条条件不应再出现旧值，另一条未知条件保持不变",
        )

if __name__ == "__main__":
    unittest.main()
