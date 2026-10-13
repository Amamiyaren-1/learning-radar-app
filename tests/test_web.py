"""本地页面渲染测试：Markdown 子集 -> HTML，确保表格/链接/代码能正确渲染且转义。"""

from __future__ import annotations

import unittest

from learnradar.web import markdown_to_html, render_index


class MarkdownRenderTests(unittest.TestCase):
    def test_renders_heading_table_and_links(self) -> None:
        markdown = (
            "# 标题\n\n"
            "> 引用一行\n\n"
            "| 列 A | 列 B |\n| --- | --- |\n| `code` | [链接](https://example.com) |\n\n"
            "- 第一项\n- 第二项\n"
        )
        html = markdown_to_html(markdown)
        self.assertIn("<h1>标题</h1>", html)
        self.assertIn("<blockquote>", html)
        self.assertIn("<table>", html)
        self.assertIn("<code>code</code>", html)
        self.assertIn('href="https://example.com"', html)
        self.assertIn("<li>第一项</li>", html)

    def test_escapes_html_in_report_text(self) -> None:
        html = markdown_to_html("# <script>alert(1)</script>")
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_index_page_lists_reports(self) -> None:
        html = render_index(
            [
                {
                    "run_id": "20260921T000000-topic",
                    "title": "示例报告",
                    "status": "published",
                    "created_at": "2026-09-21 00:00:00",
                    "model_id": "Sol",
                }
            ]
        )
        self.assertIn("示例报告", html)
        self.assertIn("/report/20260921T000000-topic", html)


if __name__ == "__main__":
    unittest.main()
