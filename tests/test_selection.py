"""选点测试：语言不参与排序；初筛结论与理由决定选点；无合适项目就不推荐。"""

from __future__ import annotations

import unittest

from learnradar.cli import _decision_map, _fit_map, _reason_map, _select_auto_target
from learnradar.screening import ScreeningOutcome

SELECTION = {"tiebreak_recent": True}


def repo(name: str, language: str, pushed: str, stars: int = 100, **overrides) -> dict:
    item = {
        "full_name": name,
        "description": "说明",
        "language": language,
        "pushed_at": pushed,
        "stargazers_count": stars,
        "fork": False,
        "archived": False,
    }
    item.update(overrides)
    return item


def screening(*rows) -> ScreeningOutcome:
    return ScreeningOutcome(
        entries=[
            {"repo": name, "decision": decision, "fit": fit, "reason": reason}
            for name, decision, fit, reason in rows
        ]
    )


class SelectionTests(unittest.TestCase):
    def pick(self, items, screen_like):
        return _select_auto_target(
            items,
            decisions=_decision_map(screen_like),
            fits=_fit_map(screen_like),
            reasons=_reason_map(screen_like),
            selection=SELECTION,
        )

    def test_language_does_not_decide_when_fit_differs(self) -> None:
        """陌生语言（TypeScript/Java）只要初筛判定更适配，就应该胜过 Python 候选。"""
        items = [
            repo("a/py", "Python", "2026-09-21T00:00:00Z"),
            repo("b/ts", "TypeScript", "2026-09-20T00:00:00Z"),
            repo("c/java", "Java", "2026-09-19T00:00:00Z"),
        ]
        screen = screening(
            ("a/py", "keep", "low", "元数据不足"),
            ("b/ts", "keep", "high", "领域相关、有示例、门槛清楚"),
            ("c/java", "uncertain", "high", "领域相关但不确认"),
        )
        picked, note = self.pick(items, screen)
        self.assertEqual(picked["full_name"], "b/ts")
        self.assertIn("语言（TypeScript）仅作环境信息", note)

    def test_fit_level_from_screening_reason_drives_selection(self) -> None:
        items = [
            repo("a/one", "Rust", "2026-09-21T00:00:00Z"),
            repo("b/two", "Rust", "2026-09-01T00:00:00Z"),
        ]
        screen = screening(
            ("a/one", "keep", "medium", "学习收益一般"),
            ("b/two", "keep", "high", "展示潜力大、门槛清楚"),
        )
        picked, note = self.pick(items, screen)
        self.assertEqual(picked["full_name"], "b/two", "fit=high 应胜过更新的 fit=medium")
        self.assertIn("fit=high", note)

    def test_screening_reason_is_echoed_in_selection_note(self) -> None:
        items = [repo("a/one", "Java", "2026-09-21T00:00:00Z")]
        screen = screening(("a/one", "keep", "high", "领域相关且有可体验演示"))
        _, note = self.pick(items, screen)
        self.assertIn("领域相关且有可体验演示", note)

    def test_keep_beats_uncertain_regardless_of_fit(self) -> None:
        items = [
            repo("a/keep", "Go", "2026-09-01T00:00:00Z"),
            repo("b/uncertain", "Python", "2026-09-21T00:00:00Z"),
        ]
        screen = screening(
            ("a/keep", "keep", "low", "信息不足但领域相关"),
            ("b/uncertain", "uncertain", "high", "看起来相关但不确定"),
        )
        picked, _ = self.pick(items, screen)
        self.assertEqual(picked["full_name"], "a/keep")

    def test_recent_push_only_breaks_ties(self) -> None:
        items = [
            repo("a/older", "Rust", "2026-08-01T00:00:00Z"),
            repo("b/newer", "Rust", "2026-09-21T00:00:00Z"),
        ]
        tie = screening(
            ("a/older", "keep", "medium", "理由"),
            ("b/newer", "keep", "medium", "理由"),
        )
        picked, _ = self.pick(items, tie)
        self.assertEqual(picked["full_name"], "b/newer", "同档同 fit 时按最近推送")

        decided = screening(
            ("a/older", "keep", "high", "更适配"),
            ("b/newer", "keep", "medium", "一般"),
        )
        picked2, _ = self.pick(items, decided)
        self.assertEqual(picked2["full_name"], "a/older", "fit 不同就不看新鲜度")

    def test_uncertain_is_backup_only(self) -> None:
        items = [repo("a/one", "Java", "2026-09-21T00:00:00Z")]
        picked, note = self.pick(items, screening(("a/one", "uncertain", "high", "不确定原因")))
        self.assertEqual(picked["full_name"], "a/one")
        self.assertIn("decision=uncertain", note)

    def test_excluded_and_unusable_candidates_are_skipped(self) -> None:
        items = [
            repo("a/excluded", "Python", "2026-09-21T00:00:00Z"),
            repo("b/fork", "Python", "2026-09-21T00:00:00Z", fork=True),
            repo("c/archived", "Python", "2026-09-21T00:00:00Z", archived=True),
            repo("d/node", "JavaScript", "2026-09-01T00:00:00Z"),
        ]
        screen = screening(
            ("a/excluded", "exclude", "", "与主题无关"),
            ("b/fork", "keep", "high", "理由"),
            ("c/archived", "keep", "high", "理由"),
            ("d/node", "keep", "medium", "理由"),
        )
        picked, _ = self.pick(items, screen)
        self.assertEqual(picked["full_name"], "d/node")

    def test_no_suitable_candidate_means_no_recommendation(self) -> None:
        items = [repo("a/one", "Python", "2026-09-21T00:00:00Z")]
        screen = screening(("a/one", "exclude", "", "与主题无关"))
        picked, note = self.pick(items, screen)
        self.assertIsNone(picked)
        self.assertIn("没有可用候选", note)

    def test_language_alone_never_excludes(self) -> None:
        """只有陌生语言的候选时仍需可被选中。"""
        items = [repo("a/haskell", "Haskell", "2026-09-21T00:00:00Z")]
        screen = screening(("a/haskell", "keep", "medium", "领域相关，语言陌生但门槛可控"))
        picked, _ = self.pick(items, screen)
        self.assertEqual(picked["full_name"], "a/haskell")


if __name__ == "__main__":
    unittest.main()
