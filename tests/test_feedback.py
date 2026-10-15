"""Feedback binds project/run, updates existing rows, reuses only relevant text, and does not affect selection."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from learnradar import cli
from learnradar.analysis import build_user_prompt, run_analysis
from learnradar.config import load_settings
from learnradar.feedback import EMPTY_TEXT, clean_answers, prompt_text_for_repos
from learnradar.evidence import Evidence
from learnradar.models import ModelResponse, Usage
from learnradar.report import render_markdown
from learnradar.store import Store
from learnradar.validate import validate_report


class FeedbackStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = Store(Path(self._tmp.name) / "learning-radar.sqlite")
        self.addCleanup(self.store.close)

    def test_repeat_submit_updates_single_record(self) -> None:
        first = self.store.save_feedback(
            "run-a",
            "owner/proj",
            {"understood_what": "yes", "understood_highlight": "yes", "want_more": "no"},
            "第一次备注",
            now="2026-09-23T10:00:00Z",
        )
        self.assertEqual(first["answered_at"], "2026-09-23T10:00:00Z")
        second = self.store.save_feedback(
            "run-a",
            "Owner/Proj",
            {"understood_what": "no", "understood_highlight": "unsure", "want_more": "unsure"},
            "第二次备注",
            now="2026-09-23T11:00:00Z",
        )
        count = self.store.conn.execute("SELECT COUNT(*) AS c FROM feedback").fetchone()["c"]
        self.assertEqual(count, 1, "重复提交必须覆盖同一条记录")
        self.assertEqual(second["answered_at"], "2026-09-23T10:00:00Z")
        self.assertEqual(second["updated_at"], "2026-09-23T11:00:00Z")
        self.assertEqual(second["understood_what"], "no")
        self.assertEqual(second["understood_highlight"], "unsure")
        self.assertEqual(second["note"], "第二次备注")

    def test_unknown_choice_is_stored_as_blank(self) -> None:
        answers = clean_answers({"understood_what": "maybe", "want_more": "no"})
        self.assertEqual(answers["understood_what"], "")
        self.assertEqual(answers["understood_highlight"], "")
        self.assertEqual(answers["want_more"], "no")

    def test_blank_note_is_allowed(self) -> None:
        row = self.store.save_feedback("run-a", "owner/proj", {"want_more": "no"}, "", now="t1")
        self.assertEqual(row["note"], "")
        self.assertEqual(row["want_more"], "no")

    def test_feedback_binds_to_project_across_runs(self) -> None:
        self.store.save_feedback(
            "run-a", "owner/proj", {"want_more": "no"}, "旧", now="2026-09-23T10:00:00Z"
        )
        self.store.save_feedback(
            "run-b",
            "https://github.com/owner/proj",
            {"want_more": "yes"},
            "新",
            now="2026-09-24T10:00:00Z",
        )
        rows = self.store.feedback_for_repos(["owner/proj"])
        self.assertEqual(len(rows), 1, "同一项目只取最近一次")
        self.assertEqual(rows[0]["run_id"], "run-b")
        self.assertEqual(rows[0]["note"], "新")
        self.assertEqual(rows[0]["history"], 2)

    def test_feedback_is_not_stored_as_evidence(self) -> None:
        self.store.save_feedback("run-a", "owner/proj", {"want_more": "no"}, "", now="t1")
        self.assertEqual(self.store.get_evidence("run-a"), [])


class FeedbackPromptTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = Store(Path(self._tmp.name) / "learning-radar.sqlite")
        self.addCleanup(self.store.close)
        self.store.save_feedback(
            "run-a",
            "owner/proj",
            {"understood_what": "yes", "understood_highlight": "unsure", "want_more": "no"},
            "备注原话",
            now="2026-09-23T11:00:00Z",
        )
        self.store.save_feedback(
            "run-c", "other/lib", {"want_more": "yes"}, "", now="2026-09-24T11:00:00Z"
        )

    def test_without_feedback_says_none(self) -> None:
        self.assertEqual(prompt_text_for_repos(self.store, ["nobody/here"]), EMPTY_TEXT)

    def test_prompt_text_keeps_user_words_and_prohibitions(self) -> None:
        text = prompt_text_for_repos(self.store, ["owner/proj"])
        self.assertIn("用户原话", text)
        self.assertIn("owner/proj", text)
        self.assertIn("是否想进一步了解：否", text)
        self.assertIn("不构成排除名单", text)
        self.assertIn("不代表用户认可项目", text)
        self.assertIn("备注原话", text)
        self.assertIn("2026-09-23T11:00:00Z", text)
        self.assertIn("run-a", text)

    def test_only_requested_project_is_included(self) -> None:
        text = prompt_text_for_repos(self.store, ["owner/proj"])
        self.assertNotIn("other/lib", text)

    def test_build_user_prompt_includes_feedback_block(self) -> None:
        settings = load_settings()
        text = prompt_text_for_repos(self.store, ["owner/proj"])
        prompt = build_user_prompt(
            settings,
            profile={"profile_id": "test"},
            profile_confirmed=False,
            target={"repo": "owner/proj"},
            comparators=[],
            evidence=[],
            feedback_text=text,
        )
        self.assertNotIn("{{FEEDBACK}}", prompt, "占位符必须被替换")
        self.assertIn("owner/proj", prompt)
        self.assertIn("不构成排除名单", prompt)

    def test_build_user_prompt_without_feedback_says_none(self) -> None:
        settings = load_settings()
        prompt = build_user_prompt(
            settings,
            profile={"profile_id": "test"},
            profile_confirmed=False,
            target={"repo": "owner/proj"},
            comparators=[],
            evidence=[],
        )
        self.assertIn(EMPTY_TEXT, prompt)


class FeedbackIsNotAPreferenceTests(unittest.TestCase):
    def test_want_more_no_does_not_exclude_the_project(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = Store(Path(tmp.name) / "learning-radar.sqlite")
        self.addCleanup(store.close)
        store.save_feedback("run-a", "owner/proj", {"want_more": "no"}, "", now="2026-09-23T10:00:00Z")
        items = [
            {"full_name": "owner/proj", "description": "d", "pushed_at": "2026-09-20T00:00:00Z"},
            {"full_name": "other/lib", "description": "d", "pushed_at": "2026-09-01T00:00:00Z"},
        ]
        chosen, _note = cli._select_auto_target(
            items,
            decisions={"owner/proj": "keep", "other/lib": "keep"},
            fits={"owner/proj": "high", "other/lib": "high"},
            reasons={},
            selection={"tiebreak_recent": True},
        )
        self.assertIsNotNone(chosen)
        self.assertEqual(chosen["full_name"], "owner/proj", "反馈不参与选点，不构成排除名单")
        self.assertIn("不构成排除名单", prompt_text_for_repos(store, ["owner/proj"]))


class FeedbackInAnalysisPromptTests(unittest.TestCase):
    """Fake model verifies relevant projects' verbatim feedback is included in analysis prompts."""

    def test_prompt_carries_only_requested_projects(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = Store(Path(tmp.name) / "learning-radar.sqlite")
        self.addCleanup(store.close)
        store.save_feedback(
            "run-a",
            "owner/proj",
            {"understood_what": "yes", "understood_highlight": "yes", "want_more": "no"},
            "备注原话",
            now="2026-09-23T11:00:00Z",
        )
        store.save_feedback("run-c", "other/lib", {"want_more": "yes"}, "", now="2026-09-24T11:00:00Z")

        settings = load_settings()
        seen: list[str] = []

        class StubAdapter:
            def complete(self, *, system, user, max_output_tokens=None):
                seen.append(user)
                return ModelResponse(
                    text=json.dumps({"target": {"repo": "owner/proj"}}, ensure_ascii=False),
                    usage=Usage(input_tokens=1, output_tokens=1, reasoning_tokens=0, raw={}),
                    model_id="stub",
                    provider="chat_completions",
                    latency_ms=1,
                    endpoint="stub://local",
                    raw={},
                )

        run_analysis(
            StubAdapter(),
            settings,
            profile={"profile_id": "test"},
            profile_confirmed=False,
            target={"repo": "owner/proj"},
            comparators=[],
            evidence=[],
            max_calls=1,
            feedback_text=prompt_text_for_repos(store, ["owner/proj"]),
        )
        self.assertEqual(len(seen), 1)
        self.assertIn("是否想进一步了解：否", seen[0])
        self.assertIn("不构成排除名单", seen[0])
        self.assertNotIn("other/lib", seen[0])


FIXTURES = Path(__file__).parent / "fixtures"
TARGET = "example-offline/target"


class FeedbackReportAppendixTests(unittest.TestCase):
    """Feedback appears only when present and is marked as verbatim user speech."""

    def setUp(self) -> None:
        self.report = json.loads((FIXTURES / "offline_analysis.json").read_text(encoding="utf-8"))
        self.evidence = [
            Evidence(**item)
            for item in json.loads((FIXTURES / "offline_evidence.json").read_text(encoding="utf-8"))
        ]
        self.validation = validate_report(
            self.report,
            evidence=self.evidence,
            target_repo=TARGET,
            collected_repos=[TARGET, "example-offline/baseline-a", "example-offline/baseline-b"],
            require_quotes=True,
        )

    def _render(self, rows: list[dict]) -> str:
        return render_markdown(
            report=self.report,
            evidence=self.evidence,
            meta={},
            validation=self.validation,
            stage_usage=[],
            feedback_rows=rows,
        )

    def test_appendix_marks_feedback_as_user_words(self) -> None:
        markdown = self._render(
            [
                {
                    "repo": TARGET,
                    "run_id": "run-a",
                    "updated_at": "2026-09-23T11:00:00Z",
                    "understood_what": "yes",
                    "understood_highlight": "unsure",
                    "want_more": "no",
                    "note": "备注原话",
                }
            ]
        )
        self.assertIn("用户对相关项目的既有反馈（用户原话，不是模型判断）", markdown)
        self.assertIn("是否想进一步了解", markdown)
        self.assertIn("| example-offline/target | 是 | 不确定 | 否 | 备注原话 |", markdown)
        self.assertIn("备注原话", markdown)
        self.assertIn("不用于跨项目偏好推断", markdown)

    def test_no_appendix_without_feedback(self) -> None:
        self.assertNotIn("附录 E", self._render([]))


if __name__ == "__main__":
    unittest.main()
