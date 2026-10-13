"""README 章节取证与学习入口识别的单元测试（离线）。"""

from __future__ import annotations

import unittest

from learnradar.readme import (
    analyze_example_content,
    extract_links,
    pick_learning_entries,
    pick_learning_entry,
    section_locator,
    select_excerpt,
    split_sections,
)

HEADER = "这是一个项目的自我介绍。\n" * 130  # 约 2340 字符，超过旧实现的 1800 字符截断点
README = (
    HEADER
    + "\n## Installation\n\n```bash\npip install demo\n```\n"
    + "\n## Quickstart\n\n运行 `python demo.py --hello` 可以看到一次完整的工具调用。\n"
    + "\n## Examples\n\n- [最小示例](https://example.com/docs/examples/minimal)\n"
    + "\n## Limitations\n\n目前只支持 Linux 与 macOS；Docker 沙箱需要额外权限。\n"
)

MARKETING_README = (
    "<div align=\"center\"><h3>How Python does AI</h3></div>\n\n"
    "## What are you building?\n\n### Coding agent\n\n```python\n#> label='positive' score=0.9\n```\n"
    + ("中间的内容说明。\n" * 200)
    + "\n## Next Steps\n\n- [快速上手文档](https://example.com/docs/quickstart)\n"
    + ("结尾的一段内容。\n" * 60)
)


class SectionSelectionTests(unittest.TestCase):
    def test_quickstart_beyond_old_truncation_point_is_included(self) -> None:
        excerpt, used = select_excerpt(README)
        self.assertIn("Quickstart", excerpt)
        self.assertIn("python demo.py --hello", excerpt)
        titles = [item["title"] for item in used]
        self.assertIn("Quickstart", titles)
        self.assertIn("Installation", titles)

    def test_excerpt_stays_within_budget(self) -> None:
        excerpt, _ = select_excerpt(README, budget=1200, per_section=400, head_chars=200)
        self.assertLessEqual(len(excerpt), 1200)

    def test_sections_carry_line_locators(self) -> None:
        _, used = select_excerpt(README)
        locator = section_locator(used)
        self.assertIn("行", locator)
        quickstart = next(item for item in used if item["title"] == "Quickstart")
        self.assertGreaterEqual(quickstart["start_line"], 1)
        self.assertGreater(quickstart["end_line"], quickstart["start_line"])

    def test_limitations_section_is_kept_for_difficulty_judgement(self) -> None:
        excerpt, _ = select_excerpt(README)
        self.assertIn("Limitations", excerpt)

    def test_headings_inside_code_fences_are_ignored(self) -> None:
        titles = [section.title for section in split_sections(MARKETING_README)]
        self.assertNotIn("> label='positive' score=0.9", titles)
        self.assertTrue(any("Next Steps" in title for title in titles))

    def test_marketing_readme_uses_next_steps_and_windowed_fallback(self) -> None:
        excerpt, used = select_excerpt(MARKETING_README)
        self.assertTrue(used, "应当命中 Next Steps 之类的章节")
        self.assertIn("Next Steps", excerpt)

    def test_readme_without_matching_sections_samples_three_windows(self) -> None:
        text = "开头部分内容。\n" + ("中间填充。\n" * 300) + "结尾部分内容。\n"
        excerpt, used = select_excerpt(text, budget=900, head_chars=200)
        self.assertEqual(used, [])
        self.assertIn("片段：字符", excerpt)
        self.assertIn("结尾部分内容", excerpt)
        self.assertIn("未匹配到", section_locator(used))

    def test_split_sections_handles_preamble(self) -> None:
        sections = split_sections("前言文字\n\n## 标题\n正文\n")
        self.assertEqual(sections[0].title, "（前言）")
        self.assertEqual(sections[1].title, "标题")


class LearningEntryTests(unittest.TestCase):
    def test_prefers_examples_link_from_readme(self) -> None:
        repo = {"homepage": "https://example.com", "full_name": "example/demo"}
        url = pick_learning_entry(repo, README)
        self.assertEqual(url, "https://example.com/docs/examples/minimal")

    def test_falls_back_to_homepage(self) -> None:
        repo = {"homepage": "https://example.com/docs", "full_name": "example/demo"}
        self.assertEqual(pick_learning_entry(repo, "没有任何链接的 README"), "https://example.com/docs")

    def test_returns_empty_when_nothing_available(self) -> None:
        self.assertEqual(pick_learning_entry({"homepage": ""}, "没有链接"), "")

    def test_extract_links(self) -> None:
        links = extract_links("[文档](https://a.example/docs) 和 [b](https://b.example)")
        self.assertEqual(links[0], ("文档", "https://a.example/docs"))
        self.assertEqual(len(links), 2)


class ExampleContentTests(unittest.TestCase):
    def test_page_with_install_and_code_block_counts_as_example(self) -> None:
        page = "## Quickstart\n\n```bash\npip install demo\n```\n\n```python\nimport demo\n```\n"
        result = analyze_example_content(page)
        self.assertTrue(result["example_signal"])
        self.assertIn("安装命令", result["signals"])

    def test_html_code_blocks_are_recognized(self) -> None:
        page = "<html><body><pre>uv add demo</pre><code>python -m demo</code></body></html>"
        result = analyze_example_content(page)
        self.assertTrue(result["example_signal"])
        self.assertGreaterEqual(result["code_blocks"], 1)

    def test_overview_page_without_runnable_steps_is_not_an_example(self) -> None:
        page = "<html><body><h1>Overview</h1><p>这是一个项目概览与设计说明页面。</p></body></html>"
        result = analyze_example_content(page)
        self.assertFalse(result["example_signal"])
        self.assertEqual(result["signals"], [])

    def test_prose_without_code_is_not_an_example(self) -> None:
        self.assertFalse(analyze_example_content("这个项目很好用，请阅读文档。")["example_signal"])

    def test_pick_learning_entries_ranks_example_links_first(self) -> None:
        repo = {"homepage": "https://example.com/docs", "full_name": "example/demo"}
        entries = pick_learning_entries(repo, README, limit=3)
        self.assertEqual(entries[0], "https://example.com/docs/examples/minimal")
        self.assertIn("https://example.com/docs", entries)

class BadgeHeadTests(unittest.TestCase):
    def test_badges_do_not_fill_the_head_window(self) -> None:
        from learnradar.readme import select_excerpt, strip_badge_lines

        badge = '[![Build](https://img.shields.io/a.svg)](https://x/a) ' * 20
        readme = (
            "# Title\n\n" + badge + "\n\n## Introduction\n\n"
            + "The goal is to simplify integrating LLMs into Java applications. " * 6
        )
        excerpt, used = select_excerpt(readme, head_chars=400)
        self.assertNotIn("shields.io", excerpt, "徽章不应占满 head 窗口")
        self.assertIn("simplify integrating LLMs", excerpt)
        self.assertIn("Introduction", [item["title"] for item in used])

    def test_strip_badge_lines_keeps_prose(self) -> None:
        from learnradar.readme import strip_badge_lines

        text = "[![a](x)](y)\n正文一段\n<img src=\"z\">\n<img src=\"w\">\n"
        cleaned = strip_badge_lines(text)
        self.assertIn("正文一段", cleaned)
        self.assertNotIn("img src", cleaned)

    def test_get_started_link_is_an_entry_candidate(self) -> None:
        from learnradar.readme import pick_learning_entries

        readme = "Getting started guide can be found [here](https://docs.example.dev/get-started)."
        entries = pick_learning_entries({"homepage": "", "full_name": "a/b"}, readme, limit=2)
        self.assertEqual(entries[0], "https://docs.example.dev/get-started")

if __name__ == "__main__":
    unittest.main()
