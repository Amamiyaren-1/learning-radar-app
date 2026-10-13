"""离线夹具回归；用户提供的原样字段不代表已读过云端产物。"""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from learnradar.cli import _generated_verification
from learnradar.evidence import Evidence, matchable
from learnradar.validate import validate_report
from scripts import offline_recheck
from tests.test_validate import COLLECTED, TARGET, SAVED_LEARNING_ACTION, make_evidence, valid_report


QUOTE = "Empower your AI with persistent, graph-like structured memory across any model, session, or tool."
NOVELTY_QUOTE = "agent-memory is the runtime that fixes that"


def materials():
    report = valid_report()
    evidence = make_evidence()
    evidence.append(Evidence(
        evidence_id="E-repo-fde715463a", kind="github_repo",
        url="https://example.invalid/fixture", title="fixture 仓库元数据",
        retrieved_at="fixture", owner=TARGET, source_type="metadata",
        excerpt="仓库自述：" + QUOTE, extra={"description": QUOTE},
    ))
    report["claims"][0] = {
        "type": "vendor_claim", "text": "项目方宣称能跨模型与会话保留结构化记忆。",
        "evidence_ids": ["E-repo-fde715463a"], "quote": QUOTE,
    }
    report["verification"] = _generated_verification(evidence)
    return report, evidence


class OfflineRecheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.run = Path(self.tmp.name) / "runs" / "fixture"
        self.run.mkdir(parents=True)
        self.report, self.evidence = materials()
        self.write("report.json", self.report)
        self.write("evidence.json", [e.to_dict() for e in self.evidence])
        self.write("validation.json", {"ok": False, "errors": ["fixture 原校验"]})
        self.write("status.json", {"status": "rejected", "fixture": True})

    def write(self, name, value):
        (self.run / name).write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def invoke(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            code = offline_recheck.main(["fixture", "--data-dir", self.tmp.name])
        return code, output.getvalue()

    def test_verbatim_repo_quote_passes_full_validator_and_script(self):
        self.assertIn(matchable(QUOTE), matchable(self.evidence[-1].excerpt))
        result = validate_report(
            self.report, evidence=self.evidence, target_repo=TARGET,
            collected_repos=COLLECTED, require_quotes=True,
            expected_verification=_generated_verification(self.evidence),
        )
        self.assertTrue(result.ok, result.errors)
        self.write("run-context.json", {
            "target": TARGET, "comparators": COLLECTED[1:],
            "learning_entry": {"verified": False},
        })
        before = {p.name: p.read_bytes() for p in self.run.iterdir()}
        code, output = self.invoke()
        self.assertEqual(code, 0, output)
        payload = json.loads((self.run / "offline-recheck.json").read_text(encoding="utf-8"))
        trace = next(row for row in payload["quote_diagnostics"] if row["path"] == "claims[1]")
        self.assertEqual(trace["normalized_quote"], matchable(QUOTE))
        self.assertEqual(trace["json_pointer"], "/claims/0")
        self.assertEqual(trace["path_index_base"], 1)
        selected = trace["selected_evidence"][0]
        self.assertEqual(selected["evidence_id"], "E-repo-fde715463a")
        self.assertEqual(selected["text_field"], "excerpt")
        self.assertTrue(selected["substring_match"])
        for name, content in before.items():
            self.assertEqual((self.run / name).read_bytes(), content)

    def test_wrong_novelty_evidence_is_rejected_even_when_readme_contains_quote(self):
        self.evidence.extend([
            Evidence(evidence_id="E-repo-598e79d3d7", kind="github_repo",
                     url="https://example.invalid/repo", title="fixture", retrieved_at="fixture",
                     owner=TARGET, excerpt="仓库元数据：fixture。"),
            Evidence(evidence_id="E-readme-94765e1351", kind="github_readme",
                     url="https://example.invalid/readme", title="fixture", retrieved_at="fixture",
                     owner=TARGET, excerpt=NOVELTY_QUOTE),
        ])
        self.report["novelty"].update(quote=NOVELTY_QUOTE, evidence_ids=["E-repo-598e79d3d7"])
        result = validate_report(self.report, evidence=self.evidence, target_repo=TARGET,
                                 collected_repos=COLLECTED, require_quotes=True)
        self.assertFalse(result.ok)
        self.assertTrue(any("novelty.quote 无法" in error for error in result.errors), result.errors)
        self.write("report.json", self.report)
        self.write("evidence.json", [e.to_dict() for e in self.evidence])
        self.write("run-context.json", {"target": TARGET, "comparators": COLLECTED[1:],
                                        "learning_entry": {"verified": False}})
        code, output = self.invoke()
        self.assertEqual(code, 1, output)
        self.assertIn("novelty.quote 无法", output)

    def test_missing_context_is_explicit_and_does_not_write_or_guess(self):
        before = {p.name: p.read_bytes() for p in self.run.iterdir()}
        code, output = self.invoke()
        self.assertEqual(code, 2)
        self.assertIn("run-context.json", output)
        self.assertIn("learning_entry", output)
        self.assertEqual({p.name: p.read_bytes() for p in self.run.iterdir()}, before)

    def test_saved_credential_gap_output_passes_recheck_without_changing_originals(self):
        self.report["learning_action"] = dict(SAVED_LEARNING_ACTION)
        self.write("report.json", self.report)
        previous_error = "learning_action 里提到了用户没有的提供方凭据，却没有写明缺口：不得把未具备的密钥当成已满足（档案只有已配置的通道）"
        self.write("validation.json", {"ok": False, "rules_version": "qa-6", "errors": [previous_error]})
        self.write("run-context.json", {"target": TARGET, "comparators": COLLECTED[1:],
                                        "learning_entry": {"verified": False}})
        before = {p.name: p.read_bytes() for p in self.run.iterdir()}
        code, output = self.invoke()
        self.assertEqual(code, 0, output)
        result = json.loads((self.run / "offline-recheck.json").read_text(encoding="utf-8"))
        self.assertTrue(result["recheck"]["ok"])
        self.assertEqual(result["changed"]["errors_removed"], [previous_error])
        for name, content in before.items():
            self.assertEqual((self.run / name).read_bytes(), content)

    def test_incomplete_context_cannot_silently_disable_entry_checks(self):
        for entry in (None, {}, {"verified": True}, {"verified": "false"}):
            with self.subTest(entry=entry):
                self.write("run-context.json", {"target": TARGET, "comparators": COLLECTED[1:],
                                                "learning_entry": entry})
                code, output = self.invoke()
                self.assertEqual(code, 2, output)
                self.assertFalse((self.run / "offline-recheck.json").exists())

    def test_description_is_not_used_in_place_of_excerpt(self):
        self.evidence[-1].excerpt = "仓库元数据：fixture。"
        result = validate_report(self.report, evidence=self.evidence, target_repo=TARGET,
                                 collected_repos=COLLECTED, require_quotes=True)
        self.assertTrue(any("claims[1].quote 无法" in error for error in result.errors), result.errors)

    def test_first_claim_failure_is_not_attributed_to_second_matching_quote(self):
        """通用 fixture：直接匹配第二项为 True，但完整校验正确拒绝第一项拼接引文。"""
        self.report["claims"].insert(0, {
            "type": "fact", "text": "材料称语言为 Python，许可为 BSD。",
            "quote": "语言=Python；许可证=BSD",
            "evidence_ids": ["E-repo-fixture-reordered"],
        })
        self.evidence.append(Evidence(
            evidence_id="E-repo-fixture-reordered", kind="github_repo",
            url="https://example.invalid/fixture-reordered", title="fixture",
            retrieved_at="fixture", owner=TARGET,
            excerpt="许可证=BSD；语言=Python",
            extra={"license": "BSD", "language": "Python"},
        ))
        self.assertIn(matchable(self.report["claims"][1]["quote"]),
                      matchable(self.evidence[-2].excerpt))
        self.write("report.json", self.report)
        self.write("evidence.json", [e.to_dict() for e in self.evidence])
        self.write("run-context.json", {"target": TARGET, "comparators": COLLECTED[1:],
                                        "learning_entry": {"verified": False}})
        code, output = self.invoke()
        self.assertEqual(code, 1, output)
        payload = json.loads((self.run / "offline-recheck.json").read_text(encoding="utf-8"))
        claims = {row["path"]: row for row in payload["quote_diagnostics"]}
        self.assertEqual(claims["claims[1]"]["json_pointer"], "/claims/0")
        self.assertTrue(claims["claims[1]"]["validation_errors"])
        self.assertFalse(claims["claims[1]"]["selected_evidence"][0]["substring_match"])
        self.assertEqual(claims["claims[2]"]["json_pointer"], "/claims/1")
        self.assertFalse(claims["claims[2]"]["validation_errors"])
        self.assertTrue(claims["claims[2]"]["selected_evidence"][0]["substring_match"])
        self.assertIn('"quote_error_details"', output)

    def test_trace_shows_last_object_when_evidence_ids_are_duplicated(self):
        duplicate = Evidence(**{**self.evidence[-1].to_dict(), "excerpt": "fixture 不同摘录"})
        self.evidence.append(duplicate)
        result = validate_report(self.report, evidence=self.evidence, target_repo=TARGET,
                                 collected_repos=COLLECTED, require_quotes=True)
        self.assertTrue(any("claims[1].quote 无法" in error for error in result.errors))
        trace = next(row for row in offline_recheck._quote_diagnostics(self.report, self.evidence)
                     if row["path"] == "claims[1]")
        selected = trace["selected_evidence"][0]
        self.assertEqual(selected["same_id_count"], 2)
        self.assertEqual(selected["array_index_zero_based"], len(self.evidence) - 1)
        self.assertFalse(selected["substring_match"])


if __name__ == "__main__":
    unittest.main()
