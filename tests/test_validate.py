"""Test inspectable structure, comparator ownership/deduplication, numeric binding, scores, and durations."""

from __future__ import annotations

import copy
import unittest

from learnradar.evidence import Evidence
from learnradar.validate import validate_report

TARGET = "owner/target"
COLLECTED = [TARGET, "other/mature", "other/second"]

# Regression action fields are supplied verbatim; all other evidence/report data is generic fixture data.
SAVED_LEARNING_ACTION = {
    "executable": False,
    "action": "本次没有可执行动作：已核验学习入口（MCP Quickstart）需要 OpenAI API 密钥和 Docker，而用户只有 DeepSeek API，兼容路径未核验；建议先按 README 安装 cognee[gliner] 并运行 cognee-cli demo，但该路径未作为已核验入口。",
    "done_criteria": "",
    "entry": "https://docs.cognee.ai/cognee-mcp/mcp-quickstart",
}


def make_evidence() -> list[Evidence]:
    return [
        Evidence(
            evidence_id="E-repo-aaa",
            kind="github_repo",
            url="https://github.com/owner/target",
            title="owner/target 仓库元数据",
            retrieved_at="2026-09-21T00:00:00Z",
            source_type="metadata",
            owner="owner/target",
            excerpt="仓库元数据：owner/target；Stars=1234。",
            extra={"stars": 1234},
        ),
        Evidence(
            evidence_id="E-readme-bbb",
            kind="github_readme",
            url="https://github.com/owner/target#readme",
            title="owner/target README",
            retrieved_at="2026-09-21T00:00:00Z",
            source_type="official",
            owner="owner/target",
            commit_sha="abc123",
            excerpt="README 摘录：这是一个把工具定义与调用约定收敛到一处的工具。要求 Python 3.11 以上。",
        ),
        Evidence(
            evidence_id="E-repo-ccc",
            kind="github_repo",
            url="https://github.com/other/mature",
            title="other/mature 仓库元数据",
            retrieved_at="2026-09-21T00:00:00Z",
            source_type="metadata",
            owner="other/mature",
            excerpt="仓库元数据：other/mature；Stars=5678。",
            extra={"stars": 5678},
        ),
        Evidence(
            evidence_id="E-repo-ddd",
            kind="github_repo",
            url="https://github.com/other/second",
            title="other/second 仓库元数据",
            retrieved_at="2026-09-21T00:00:00Z",
            source_type="metadata",
            owner="other/second",
            excerpt="仓库元数据：other/second；Stars=900。",
            extra={"stars": 900},
        ),
    ]


def valid_report() -> dict:
    return {
        "title": "owner/target 对比报告",
        "target": {
            "repo": "owner/target",
            "version": "v1.0.0",
            "category": ["Agent", "工具调用"],
            "summary_one_line": "让 Agent 以统一方式调用外部工具。",
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
                    "evidence_ids": ["E-readme-bbb"],
                    "quote": "把工具定义与调用约定收敛到一处",
                }
            ]
        },
        "novelty": {
            "what": "把工具定义与调用约定收敛到一处。",
            "when": "2026-08-01",
            "kind": "major_version",
            "evidence_ids": ["E-readme-bbb"],
            "quote": "把工具定义与调用约定收敛到一处",
        },
        "claims": [
            {"type": "fact", "text": "仓库获得 1234 星。", "evidence_ids": ["E-repo-aaa"], "quote": "Stars=1234"},
            {
                "type": "vendor_claim",
                "text": "README 声称把工具定义与调用约定收敛到一处。",
                "evidence_ids": ["E-readme-bbb"],
                "quote": "把工具定义与调用约定收敛到一处",
            },
            {"type": "inference", "text": "对新手来说直接跑示例更划算。", "evidence_ids": ["E-readme-bbb"]},
        ],
        "comparisons": [
            {
                "comparator": "other/mature",
                "why_this_comparator": "同为工具集合的成熟基线。",
                "problem": "成熟的同类工具集。",
                "key_difference": "覆盖更广但更重。",
                "fit_conditions": "需要大量内置集成时。",
                "limitations": "上手成本更高。",
                "evidence_ids": ["E-repo-ccc"],
            },
            {
                "comparator": "other/second",
                "why_this_comparator": "轻量做法的对照。",
                "problem": "较轻量的另一种做法。",
                "key_difference": "抽象更少。",
                "fit_conditions": "只想要最小实现时。",
                "limitations": "生态较小。",
                "evidence_ids": ["E-repo-ddd"],
            },
        ],
        "value_for_user": {
            "learn": "能补齐 Agent 工具调用的基本概念。",
            "demo": "可以做一个命令行小演示。",
            "artifact": "可以尝试替换成自己的工具。",
        },
        "starting_conditions": {
            "have": ["会用 C 与 Python 做基础算法题"],
            "gaps": ["没有核验到 Windows 支持说明"],
        },
        "difficulty": {
            "level": "run_example",
            "basis": "README 提供最小示例。",
            "conditions": [
                {"condition": "runtime", "value": "3.11+", "status": "evidence",
                 "evidence_ids": ["E-readme-bbb"], "quote": "要求 Python 3.11 以上"},
                {"condition": "os_support", "value": "未知（未在任何已取材料中找到）",
                 "status": "unknown"},
                {"condition": "gpu", "value": "不适用（只调用远程 API）", "status": "not_applicable"},
                {"condition": "external_cost", "value": "无", "status": "inference",
                 "basis": "README 没有提到付费服务"},
            ],
        },
        "learning_action": {
            "executable": True,
            "action": "跑通仓库里的最小示例并读一遍工具注册那段代码。",
            "done_criteria": "示例能打印一次成功的工具调用记录。",
            "entry": "https://github.com/owner/target#readme",
        },
        "mini_experiment": {
            "suggested_change": "给示例加一个自定义工具并观察注册结果。",
            "observable": "示例输出里出现新增工具名。",
            "boundary": "不修改库本身，只改示例。",
        },
        "explore_further": {
            "repo": "https://github.com/owner/target",
            "docs": "https://github.com/owner/target#readme",
            "example_entry": "https://github.com/owner/target#readme",
            "known_limits": ["README 未说明 Windows 支持"],
        },
        "verification": {
            "docs_reviewed": True,
            "source_reviewed": False,
            "actually_ran": False,
            "note": "只查阅 README，没有运行代码。",
        },
        "recommendation": {
            "decision": "recommend",
            "reason": "概念清晰、示例可执行，适合当前目标。",
            "evidence_gaps": ["没有独立第三方评测"],
            "uncertainties": ["长期维护情况未知"],
        },
    }


class ValidateReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.evidence = make_evidence()
        self.report = valid_report()

    def check(self, report: dict, **kwargs):
        params = {"collected_repos": COLLECTED}
        params.update(kwargs)
        return validate_report(report, evidence=self.evidence, target_repo=TARGET, **params)

    def test_valid_report_passes(self) -> None:
        result = self.check(self.report)
        self.assertTrue(result.ok, msg=result.errors)

    def test_unknown_evidence_id_is_rejected(self) -> None:
        report = copy.deepcopy(self.report)
        report["claims"][0]["evidence_ids"] = ["E-不存在-999"]
        result = self.check(report)
        self.assertFalse(result.ok)
        self.assertTrue(any("不存在的证据编号" in item for item in result.errors))

    def test_missing_citation_is_rejected(self) -> None:
        report = copy.deepcopy(self.report)
        report["claims"][0]["evidence_ids"] = []
        self.assertFalse(self.check(report).ok)

    def test_too_few_comparators_is_rejected(self) -> None:
        report = copy.deepcopy(self.report)
        report["comparisons"] = report["comparisons"][:1]
        result = self.check(report)
        self.assertFalse(result.ok)
        self.assertTrue(any("对照项目不足" in item for item in result.errors))

    def test_duplicate_comparators_are_rejected(self) -> None:
        report = copy.deepcopy(self.report)
        report["comparisons"][1] = copy.deepcopy(report["comparisons"][0])
        result = self.check(report)
        self.assertFalse(result.ok)
        self.assertTrue(any("对照项目重复" in item for item in result.errors), msg=result.errors)

    def test_ghost_comparator_is_rejected(self) -> None:
        report = copy.deepcopy(self.report)
        report["comparisons"][1]["comparator"] = "ghost/never-collected"
        result = self.check(report)
        self.assertFalse(result.ok)
        self.assertTrue(any("不在本次实际采集范围内" in item for item in result.errors), msg=result.errors)

    def test_comparison_must_cite_its_own_evidence(self) -> None:
        report = copy.deepcopy(self.report)
        report["comparisons"][1]["evidence_ids"] = ["E-repo-ccc"]
        result = self.check(report)
        self.assertFalse(result.ok)
        self.assertTrue(any("没有引用属于 other/second 的证据" in item for item in result.errors), msg=result.errors)

    def test_fabricated_star_count_is_rejected(self) -> None:
        report = copy.deepcopy(self.report)
        report["claims"][0]["text"] = "仓库获得 99999 星。"
        self.assertFalse(self.check(report).ok)

    def test_borrowed_star_count_from_another_project_is_rejected(self) -> None:
        report = copy.deepcopy(self.report)
        report["claims"][0]["text"] = "仓库获得 5678 星。"
        result = self.check(report)
        self.assertFalse(result.ok)
        self.assertTrue(any("张冠李戴" in item or "找不到" in item for item in result.errors), msg=result.errors)

    def test_same_number_on_its_own_evidence_is_allowed(self) -> None:
        report = copy.deepcopy(self.report)
        report["claims"][0]["text"] = "对照项目 other/mature 有 5678 星。"
        report["claims"][0]["evidence_ids"] = ["E-repo-ccc"]
        self.assertTrue(self.check(report).ok, msg=self.check(report).errors)

    def test_decorative_score_is_rejected(self) -> None:
        report = copy.deepcopy(self.report)
        report["recommendation"]["reason"] = "综合评分 92/100，值得学。"
        self.assertFalse(self.check(report).ok)

    def test_precise_duration_is_rejected(self) -> None:
        report = copy.deepcopy(self.report)
        report["difficulty"]["basis"] = "大约 3 小时可以掌握。"
        self.assertFalse(self.check(report).ok)

    def test_actually_ran_must_be_false(self) -> None:
        report = copy.deepcopy(self.report)
        report["verification"]["actually_ran"] = True
        self.assertFalse(self.check(report).ok)

    def test_verification_must_match_run_records(self) -> None:
        report = copy.deepcopy(self.report)
        report["verification"]["source_reviewed"] = True
        expected = {"docs_reviewed": True, "source_reviewed": False, "actually_ran": False}
        result = self.check(report, expected_verification=expected)
        self.assertFalse(result.ok)
        self.assertTrue(any("与运行记录" in item for item in result.errors), msg=result.errors)

    def test_quote_must_match_cited_evidence(self) -> None:
        report = copy.deepcopy(self.report)
        report["claims"][0]["evidence_ids"] = ["E-readme-bbb"]
        report["claims"][0]["quote"] = "这个项目已经在 Windows 上通过完整兼容性测试。"
        result = self.check(report, require_quotes=True)
        self.assertFalse(result.ok)
        self.assertTrue(any("无法在所引证据中匹配" in item for item in result.errors), msg=result.errors)

    def test_quote_present_in_evidence_passes(self) -> None:
        report = copy.deepcopy(self.report)
        report["claims"][0]["quote"] = "Stars=1234"
        report["claims"][1]["quote"] = "把工具定义与调用约定收敛到一处"
        report["novelty"]["quote"] = "把工具定义与调用约定收敛到一处"
        result = self.check(report, require_quotes=True)
        self.assertTrue(result.ok, msg=result.errors)

    def test_unverified_learning_entry_is_rejected_when_strict(self) -> None:
        report = copy.deepcopy(self.report)
        report["learning_action"]["entry"] = "https://example.com/guessed-entry"
        self.assertFalse(self.check(report, require_verified_entry=True).ok)
        soft = self.check(report)
        self.assertTrue(soft.ok, msg=soft.errors)
        self.assertTrue(any("learning_action.entry" in item for item in soft.warnings))

    def test_recommend_requires_gaps_and_uncertainties(self) -> None:
        report = copy.deepcopy(self.report)
        report["recommendation"]["evidence_gaps"] = []
        self.assertFalse(self.check(report).ok)

    def test_watch_decision_without_gaps_is_allowed(self) -> None:
        report = copy.deepcopy(self.report)
        report["recommendation"]["decision"] = "watch"
        report["recommendation"]["evidence_gaps"] = []
        report["recommendation"]["uncertainties"] = []
        self.assertTrue(self.check(report).ok, msg=self.check(report).errors)

    def test_target_repo_mismatch_is_rejected(self) -> None:
        report = copy.deepcopy(self.report)
        report["target"]["repo"] = "someone/else"
        self.assertFalse(self.check(report).ok)

    def test_structure_label_and_content_review_are_separated(self) -> None:
        result = self.check(self.report)
        summary = result.summary()
        self.assertEqual(summary["structure_check"], "结构检查通过")
        self.assertEqual(summary["content_review"], "not_performed")


    def test_condition_evidence_requires_matching_quote(self) -> None:
        report = copy.deepcopy(self.report)
        report["difficulty"]["conditions"][0]["quote"] = "已经在 Windows 上通过完整兼容性测试"
        result = self.check(report)
        self.assertFalse(result.ok)
        self.assertTrue(any("无法在所引证据中匹配" in item for item in result.errors), msg=result.errors)

    def test_condition_marked_unknown_with_evidence_is_rejected(self) -> None:
        report = copy.deepcopy(self.report)
        report["difficulty"]["conditions"][1]["evidence_ids"] = ["E-readme-bbb"]
        result = self.check(report)
        self.assertFalse(result.ok)
        self.assertTrue(any("标为未知却声称有证据" in item for item in result.errors), msg=result.errors)

    def test_condition_unknown_must_say_unknown(self) -> None:
        report = copy.deepcopy(self.report)
        report["difficulty"]["conditions"][1]["value"] = "支持 Windows"
        self.assertFalse(self.check(report).ok)

    def test_condition_inference_requires_basis(self) -> None:
        report = copy.deepcopy(self.report)
        report["difficulty"]["conditions"][3].pop("basis")
        result = self.check(report)
        self.assertFalse(result.ok)
        self.assertTrue(any("必须写明 basis" in item for item in result.errors), msg=result.errors)

    def test_missing_required_condition_is_rejected(self) -> None:
        report = copy.deepcopy(self.report)
        report["difficulty"]["conditions"] = report["difficulty"]["conditions"][:2]
        result = self.check(report)
        self.assertFalse(result.ok)
        self.assertTrue(any("缺少关键条件" in item for item in result.errors), msg=result.errors)

    def test_matching_quote_does_not_prove_the_conclusion(self) -> None:
        # Matching a quote proves what the excerpt says, not that the conclusion follows.
        report = copy.deepcopy(self.report)
        report["difficulty"]["conditions"][1] = {
            "condition": "os_support",
            "value": "支持 Windows",
            "status": "evidence",
            "evidence_ids": ["E-readme-bbb"],
            "quote": "要求 Python 3.11 以上",
        }
        result = self.check(report)
        self.assertTrue(result.ok, msg=result.errors)
        self.assertEqual(result.content_review, "not_performed")

    def test_executable_action_must_use_the_verified_entry(self) -> None:
        report = copy.deepcopy(self.report)
        result = self.check(report, verified_entry={"url": "https://example.com/other", "example_signal": True})
        self.assertFalse(result.ok)
        self.assertTrue(any("必须是本次核验过的入口" in item for item in result.errors), msg=result.errors)

    def test_claiming_executable_on_entry_without_example_signal_warns(self) -> None:
        report = copy.deepcopy(self.report)
        report["learning_action"]["entry"] = "https://example.com/overview"
        entry = {"url": "https://example.com/overview", "verified": True, "example_signal": False}
        result = self.check(report, verified_entry=entry)
        self.assertTrue(result.ok, msg=result.errors)
        self.assertTrue(any("没有检测到示例信号" in item for item in result.warnings), msg=result.warnings)

    def test_not_executable_keeps_readme_link_and_empty_criteria(self) -> None:
        """A non-executable action with a valid README URL and empty completion criteria may be an observation report."""
        report = copy.deepcopy(self.report)
        report["learning_action"] = {
            "executable": False,
            "action": "本次没有可执行的学习动作：README 的 Quick Start 只能作为安装准备参考。",
            "done_criteria": "",
            "entry": "https://github.com/owner/target#readme",
        }
        result = self.check(report)
        self.assertTrue(result.ok, msg=result.errors)

    def test_executable_state_must_be_explicit(self) -> None:
        report = copy.deepcopy(self.report)
        del report["learning_action"]["executable"]
        result = self.check(report)
        self.assertFalse(result.ok)
        self.assertTrue(any("executable" in item for item in result.errors), msg=result.errors)

    def test_conditions_use_generic_runtime_category(self) -> None:
        report = copy.deepcopy(self.report)
        report["difficulty"]["conditions"] = [
            {"condition": "python_version", "value": "3.11+", "status": "unknown"},
            {"condition": "os_support", "value": "未知（未找到）", "status": "unknown"},
            {"condition": "gpu", "value": "不适用（只调远程 API）", "status": "not_applicable"},
            {"condition": "external_cost", "value": "无", "status": "inference", "basis": "b"},
        ]
        result = self.check(report)
        self.assertFalse(result.ok)
        self.assertTrue(any("缺少关键条件 runtime" in item for item in result.errors), msg=result.errors)

    def test_not_applicable_needs_wording_and_no_evidence(self) -> None:
        report = copy.deepcopy(self.report)
        report["difficulty"]["conditions"][2] = {
            "condition": "gpu",
            "value": "不需要",
            "status": "not_applicable",
        }
        self.assertFalse(self.check(report).ok)

    def test_mini_experiment_must_not_carry_evidence_ids(self) -> None:
        report = copy.deepcopy(self.report)
        report["mini_experiment"]["evidence_ids"] = ["E-readme-bbb"]
        result = self.check(report)
        self.assertFalse(result.ok)
        self.assertTrue(any("不得写成已验证能力" in item for item in result.errors), msg=result.errors)

    def test_other_provider_credentials_cannot_be_claimed_as_available(self) -> None:
        report = copy.deepcopy(self.report)
        report["starting_conditions"]["have"] = ["已配置 DeepSeek API", "已有 Anthropic API 密钥"]
        result = self.check(report)
        self.assertFalse(result.ok)
        self.assertTrue(any("没有的提供方凭据" in item for item in result.errors), msg=result.errors)

    def test_saved_unavailable_action_explains_provider_credential_gap(self) -> None:
        report = copy.deepcopy(self.report)
        report["learning_action"] = copy.deepcopy(SAVED_LEARNING_ACTION)
        result = self.check(report)
        self.assertTrue(result.ok, result.errors)

    def test_unavailable_flag_without_provider_gap_is_still_rejected(self) -> None:
        for action in (
            "安装并运行 OpenAI 示例。",
            "已有 OpenAI API 密钥，其他功能兼容路径未核验。",
            "需要 OpenAI API 密钥，而用户只有 OpenAI API。",
        ):
            with self.subTest(action=action):
                report = copy.deepcopy(self.report)
                report["learning_action"] = {**SAVED_LEARNING_ACTION, "action": action}
                result = self.check(report)
                self.assertFalse(result.ok)
                self.assertTrue(any("没有的提供方凭据" in item for item in result.errors), result.errors)

    def test_gap_explanation_does_not_make_unavailable_action_executable(self) -> None:
        report = copy.deepcopy(self.report)
        report["learning_action"] = {**SAVED_LEARNING_ACTION, "executable": True, "done_criteria": "看到输出"}
        result = self.check(report)
        self.assertFalse(result.ok)
        self.assertTrue(any("没有的提供方凭据" in item for item in result.errors), result.errors)

    def test_other_provider_in_gaps_or_suggestions_is_allowed(self) -> None:
        report = copy.deepcopy(self.report)
        report["starting_conditions"]["gaps"] = ["示例需 Anthropic API 密钥，用户没有"]
        report["mini_experiment"]["suggested_change"] = "把示例里的 Anthropic 模型换成 DeepSeek 兼容调用"
        result = self.check(report)
        self.assertTrue(result.ok, msg=result.errors)

    def test_other_provider_credentials_with_gap_marker_are_allowed(self) -> None:
        report = copy.deepcopy(self.report)
        report["mini_experiment"]["suggested_change"] = (
            "示例默认用 Anthropic；用户没有该凭据，属于缺口，需要先核验兼容路径。"
        )
        result = self.check(report)
        self.assertTrue(result.ok, msg=result.errors)

    def test_highlight_items_are_required(self) -> None:
        report = copy.deepcopy(self.report)
        report["highlight"]["items"][0]["plain"] = ""
        result = self.check(report)
        self.assertFalse(result.ok)
        self.assertTrue(any("highlight.items[1].plain" in item for item in result.errors))

    def test_explore_example_entry_may_say_unverified_when_nothing_verified(self) -> None:
        report = copy.deepcopy(self.report)
        report["explore_further"]["example_entry"] = "未核验到"
        result = self.check(report)
        self.assertTrue(result.ok, msg=result.errors)

    def test_explore_example_entry_must_be_url_when_entry_verified(self) -> None:
        report = copy.deepcopy(self.report)
        report["explore_further"]["example_entry"] = "未核验到"
        result = self.check(
            report,
            verified_entry={"url": "https://example.com/entry", "verified": True, "example_signal": True},
        )
        self.assertFalse(result.ok)
        self.assertTrue(any("explore_further.example_entry" in item for item in result.errors))

    def test_learning_action_may_say_not_executable_without_example_signal(self) -> None:
        report = copy.deepcopy(self.report)
        report["learning_action"]["executable"] = False
        report["learning_action"]["entry"] = "https://example.com/overview"
        result = self.check(
            report,
            verified_entry={"url": "https://example.com/overview", "verified": True, "example_signal": False},
        )
        self.assertTrue(result.ok, msg=result.errors)

    def test_not_executable_action_may_leave_done_criteria_empty(self) -> None:
        report = copy.deepcopy(self.report)
        report["learning_action"] = {
            "executable": False,
            "action": "本次没有可执行的学习动作：未核验到可跟随的示例入口。",
            "done_criteria": "",
            "entry": "",
        }
        result = self.check(
            report, verified_entry={"url": "", "verified": False, "example_signal": False}
        )
        self.assertTrue(result.ok, msg=result.errors)

    def test_executable_action_still_requires_done_criteria(self) -> None:
        report = copy.deepcopy(self.report)
        report["learning_action"]["done_criteria"] = ""
        result = self.check(report)
        self.assertFalse(result.ok)
        self.assertTrue(any("done_criteria" in item for item in result.errors), msg=result.errors)

    def test_metadata_claim_with_wrong_version_is_rejected(self) -> None:
        report = copy.deepcopy(self.report)
        report["claims"].append({
            "type": "fact",
            "text": "最新发布版本为 9.9.9。",
            "evidence_ids": ["E-repo-aaa"],
        })
        result = self.check(report, require_quotes=True)
        self.assertFalse(result.ok)
        self.assertTrue(
            any(("元数据值" in item) or ("quote 缺失" in item) for item in result.errors),
            msg=result.errors,
        )

    def test_metadata_claim_with_matching_version_passes(self) -> None:
        report = copy.deepcopy(self.report)
        report["claims"].append({
            "type": "fact",
            "text": "最新发布版本为 1234。",
            "evidence_ids": ["E-repo-aaa"],
        })
        result = self.check(report, require_quotes=True)
        self.assertTrue(result.ok, msg=result.errors)

    def test_metadata_without_numbers_cannot_carry_judgement(self) -> None:
        report = copy.deepcopy(self.report)
        report["claims"].append({
            "type": "fact",
            "text": "该项目非常适合初学者，并且兼容 DeepSeek。",
            "evidence_ids": ["E-repo-aaa"],
        })
        result = self.check(report, require_quotes=True)
        self.assertFalse(result.ok)
        self.assertTrue(any("quote 缺失" in item for item in result.errors), msg=result.errors)

    def test_repo_metadata_sentence_binds_each_value_to_its_field(self) -> None:
        """Metadata whitelist fixture accepts natural expressions regardless of quote field order."""
        self.evidence[0].extra.update(language="Python", license="BSD", pushed_at="2026-01-02T03:04:05Z")
        self.evidence[0].excerpt = "最后推送 2026-01-02T03:04:05Z；语言=Python；许可证=BSD。"
        report = copy.deepcopy(self.report)
        report["claims"][0].update(
            text="仓库元数据显示语言为 Python，许可证为 BSD，最近推送在 2026-01-02。",
            quote="语言=Python；许可证=BSD；最后推送 2026-01-02T03:04:05Z",
        )
        result = self.check(report, require_quotes=True)
        self.assertTrue(result.ok, result.errors)
        # Field order/date precision do not change claims; mismatched values or extra judgments are rejected.
        for text in (
            "仓库元数据显示最近推送在 2026-01-02，许可证为 BSD，语言为 Python。",
            "仓库元数据显示语言为 Python，许可证为 BSD，最后推送在 2026-01-02T03:04:05Z。",
        ):
            with self.subTest(text=text):
                report["claims"][0]["text"] = text
                result = self.check(report, require_quotes=True)
                self.assertTrue(result.ok, result.errors)
        for text in (
            "仓库元数据显示语言为 BSD，许可证为 Python，最近推送在 2026-01-02。",
            "仓库元数据显示语言为 Java，许可证为 BSD，最近推送在 2026-01-02。",
            "仓库元数据显示语言为 Python，许可证为 BSD，最近推送在 2026-01-03。",
            "仓库元数据显示语言为 Python，许可证为 BSD，最近推送在 2026-01。",
            "仓库元数据显示语言为 Python，许可证为 BSD，星数为 9999。",
            "仓库元数据显示语言为 Python，许可证为 BSD，支持持久记忆。",
            "仓库元数据显示语言为 Python，许可证为 BSD，支持 Python。",
            "仓库元数据显示语言为 Python，许可证为 BSD，适合初学者。",
        ):
            with self.subTest(text=text):
                report["claims"][0]["text"] = text
                result = self.check(report, require_quotes=True)
                self.assertFalse(result.ok, result.errors)
                self.assertTrue(any("claims[1].quote 无法" in error for error in result.errors), result.errors)

        report["claims"][0]["text"] = "仓库元数据显示语言为 Python，许可证为 BSD。"
        self.evidence[0].extra.pop("language")
        result = self.check(report, require_quotes=True)
        self.assertFalse(result.ok, result.errors)
        self.assertTrue(any("claims[1].quote 无法" in error for error in result.errors), result.errors)

    def test_repo_metadata_sentence_cannot_combine_values_from_different_projects(self) -> None:
        self.evidence[0].extra.update(language="Python", license="BSD")
        self.evidence[2].extra.update(pushed_at="2026-01-02T03:04:05Z")
        report = copy.deepcopy(self.report)
        report["claims"][0].update(
            text="仓库元数据显示语言为 Python，许可证为 BSD，最近推送在 2026-01-02。",
            evidence_ids=["E-repo-aaa", "E-repo-ccc"], quote="fixture 非连续引文",
        )
        result = self.check(report, require_quotes=True)
        self.assertFalse(result.ok)
        self.assertTrue(any("claims[1].quote 无法" in error for error in result.errors), result.errors)

if __name__ == "__main__":
    unittest.main()
