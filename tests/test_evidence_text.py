"""Preserve code blocks/links during extraction to avoid false missing-example results."""

from __future__ import annotations

import unittest

from learnradar.evidence import Evidence, matchable, strip_html


class StripHtmlTests(unittest.TestCase):
    def test_code_blocks_are_preserved(self) -> None:
        raw = "<p>Install:</p><pre><code>npm install @deuz-sdk/core</code></pre><p>Done.</p>"
        text = strip_html(raw)
        self.assertIn("```", text, "代码块要保留围栏，否则会被判定为没有示例")
        self.assertIn("npm install @deuz-sdk/core", text)

    def test_links_keep_their_targets(self) -> None:
        raw = '<a href="https://docs.example.dev/get-started">here</a>'
        text = strip_html(raw)
        self.assertIn("https://docs.example.dev/get-started", text)
        self.assertIn("here", text)

    def test_inline_code_is_kept(self) -> None:
        text = strip_html("<p>run <code>python -m demo</code> now</p>")
        self.assertIn("`python -m demo`", text)


class QuoteMatchingTests(unittest.TestCase):
    """Quote normalization preserves the contiguous-source requirement."""

    def _evidence(self, excerpt: str) -> Evidence:
        return Evidence(
            evidence_id="E-readme-x",
            kind="github_readme",
            url="https://github.com/o/r#readme",
            title="t",
            retrieved_at="2026-09-23T00:00:00Z",
            owner="o/r",
            excerpt=excerpt,
        )

    def test_markdown_emphasis_does_not_block_a_verbatim_quote(self) -> None:
        excerpt = "## Quickstart\n\nRequires **Python 3.10–3.14**.\n"
        quote = "Requires Python 3.10–3.14."
        self.assertIn("python 3.10–3.14.", matchable(excerpt))
        self.assertNotIn("3.14 .", matchable(excerpt))

    def test_unrelated_quote_is_still_rejected(self) -> None:
        evidence = self._evidence("本工具只支持 Linux。")
        from learnradar.evidence import quote_supported_by

        matched, _ = quote_supported_by("本工具支持 Windows。", [evidence])
        self.assertFalse(matched)

    def test_visible_link_text_is_a_contiguous_quote(self) -> None:
        from learnradar.evidence import quote_supported_by

        evidence = self._evidence(
            "Get a free key at [console.example.dev](https://console.example.dev/), no\n"
            "   credit card required."
        )
        self.assertTrue(quote_supported_by(
            "Get a free key at console.example.dev, no credit card required.", [evidence]
        )[0])
        self.assertFalse(quote_supported_by(
            "Get an unlimited key at console.example.dev, no credit card required.", [evidence]
        )[0])
        self.assertFalse(quote_supported_by(
            "https://unrelated.example.dev/", [evidence]
        )[0])
        self.assertTrue(quote_supported_by(
            "https://console.example.dev/", [evidence]
        )[0])


if __name__ == "__main__":
    unittest.main()
