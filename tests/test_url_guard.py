"""Reject templates/invalid URLs without requests while preserving other candidates and finalization."""
from __future__ import annotations

import contextlib
import io
import json
import unittest
from unittest import mock

from learnradar import collector
from learnradar.net import FetchError, FetchResult, fetch, now_iso, unrequestable_reason
from learnradar.readme import docs_followup_links
from tests.test_weekly import WeeklyTestCase

BASE = "https://docs.example.dev/get-started"
TEMPLATE_HREF = "/compose/install/{{ meta.url | default(url) | safeUrl }}"
TEMPLATE_URL = "https://docs.example.dev/compose/install/{{ meta.url | default(url) | safeUrl }}"
DOCS_HTML = (
    "<html><body><h1>Install</h1>"
    f'<a href="{TEMPLATE_HREF}">Install with Compose</a>'
    '<a href="./getting-started">Getting started</a>'
    "</body></html>"
)


class UrlGuardTests(unittest.TestCase):
    """URL guards reject only genuinely unrequestable addresses."""

    def test_template_url_is_rejected_without_any_request(self) -> None:
        self.assertIn("模板", unrequestable_reason(TEMPLATE_URL))
        with mock.patch("learnradar.net._OPENER") as opener, mock.patch(
            "learnradar.net.socket.getaddrinfo"
        ) as dns:
            with self.assertRaises(FetchError) as ctx:
                fetch(TEMPLATE_URL, retries=0)
        self.assertIn("模板", str(ctx.exception))
        opener.open.assert_not_called()
        dns.assert_not_called()

    def test_space_in_url_is_rejected_before_dns(self) -> None:
        with mock.patch("learnradar.net._OPENER") as opener, mock.patch(
            "learnradar.net.socket.getaddrinfo"
        ) as dns:
            with self.assertRaises(FetchError):
                fetch("https://docs.example.dev/a b", retries=0)
        opener.open.assert_not_called()
        dns.assert_not_called()

    def test_invalid_url_at_send_stage_becomes_fetch_error(self) -> None:
        """A send-time InvalidURL becomes FetchError without aborting the entire task."""
        with mock.patch("learnradar.net.unrequestable_reason", return_value=""), mock.patch(
            "learnradar.net.socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("93.184.216.34", 443))],
        ), mock.patch(
            "learnradar.net.socket.create_connection",
            side_effect=AssertionError("不应该真的建立连接"),
        ):
            with self.assertRaises(FetchError) as ctx:
                fetch("https://docs.example.dev/a b", retries=0)
        self.assertIn("地址不合法", str(ctx.exception))

    def test_relative_chinese_and_encoded_urls_are_not_rejected(self) -> None:
        for url in (
            "/docs/getting-started",
            "https://example.com/文档/入门",
            "https://example.com/%E6%96%87%E6%A1%A3",
            "https://example.com/a?q=1&b=2#frag",
            "https://github.com/owner/repo/blob/main/examples/demo.py",
        ):
            self.assertEqual(unrequestable_reason(url), "", url)

    def test_docs_followup_skips_template_and_keeps_valid_link(self) -> None:
        skipped: list[dict[str, str]] = []
        links = docs_followup_links(DOCS_HTML, BASE, limit=1, skipped=skipped)
        self.assertEqual([item[0] for item in links], ["https://docs.example.dev/getting-started"])
        self.assertEqual(len(skipped), 1, skipped)
        self.assertIn("模板", skipped[0]["reason"])
        self.assertIn("{{", skipped[0]["url"])

    def test_docs_followup_without_skipped_list_still_skips(self) -> None:
        links = docs_followup_links(DOCS_HTML, BASE, limit=1)
        self.assertEqual([item[0] for item in links], ["https://docs.example.dev/getting-started"])


class CollectorBadLinkTests(unittest.TestCase):
    """Bad links record gaps without aborting later valid evidence candidates."""

    def _client(self, readme: str):
        client = mock.Mock()
        client.user_agent = "ua"
        client.timeout = 5
        client.max_bytes = 100000
        client.audit = []
        client.get_readme.return_value = (
            readme,
            {"path": "README.md", "html_url": "https://github.com/a/one#readme"},
        )
        return client

    def _run_entry(self, readme: str, pages: dict[str, object]):
        client = self._client(readme)
        seen: list[str] = []

        def fake_fetch(url: str, **kwargs: object):
            seen.append(url)
            entry = pages.get(url)
            if isinstance(entry, Exception):
                raise entry
            if entry is None:
                raise FetchError(f"HTTP 404：{url}")
            return FetchResult(
                url=url,
                final_url=url,
                status=200,
                headers={"Content-Type": "text/html"},
                body=str(entry).encode("utf-8"),
                retrieved_at=now_iso(),
                sha256=f"sha-{len(seen)}",
                content_type="text/html",
                attempts=1,
            )

        with mock.patch.object(collector, "fetch", side_effect=fake_fetch):
            entry = collector._collect_learning_entry(
                client,
                {"full_name": "a/one"},
                readme,
                max_attempts=3,
                max_files=0,
                docs_followup_max=1,
                entry_evidence_max=2,
                remaining_sources=4,
                stamp=now_iso(),
            )
        return entry, seen

    def test_template_followup_is_skipped_and_valid_page_is_used(self) -> None:
        readme = "## Docs\n\n[Getting started](https://docs.example.dev/get-started)\n"
        entry, seen = self._run_entry(
            readme,
            {
                "https://docs.example.dev/get-started": DOCS_HTML,
                "https://docs.example.dev/getting-started": "<pre>npm install demo</pre>\nGetting started",
            },
        )
        self.assertTrue(entry["verified"])
        self.assertTrue(entry["example_signal"], entry)
        self.assertIn("模板", entry["skipped_links"][0]["reason"])
        self.assertFalse(
            any("{{" in url for url in seen), f"模板地址绝不能被请求：{seen}"
        )
        self.assertIn("https://docs.example.dev/getting-started", seen)

    def test_all_entry_candidates_unavailable_records_gap(self) -> None:
        readme = "## Docs\n\n[Getting started](https://docs.example.dev/missing)\n"
        entry, _seen = self._run_entry(readme, {})
        self.assertFalse(entry["verified"])
        self.assertIn("候选学习入口都读取失败", entry["error"])
        self.assertIn("missing", entry["error"])
        self.assertEqual(entry["attempts"][0]["ok"], False)


class WeeklyBadLinkTests(WeeklyTestCase):
    """Unexpanded documentation URLs preserve finalization, usage, and cycle state."""

    README = (
        "# demo\n\n## Docs\n\n[Getting started](https://docs.example.dev/get-started)\n\n"
        "A tool-calling helper for agents that keeps every tool definition in one place.\n"
    )
    fetched: list[str] = []

    def fake_client(self, items):  # type: ignore[override]
        client = super().fake_client(items)
        client.get_readme.return_value = (
            self.README,
            {"path": "README.md", "html_url": "https://github.com/a/one#readme"},
        )
        return client

    @staticmethod
    def fake_fetch(url: str, **kwargs: object):
        WeeklyBadLinkTests.fetched.append(url)
        if "docs.example.dev" in url:
            body = DOCS_HTML
        else:
            body = "<pre>npm install demo</pre>"
        return FetchResult(
            url=url,
            final_url=url,
            status=200,
            headers={"Content-Type": "text/html"},
            body=body.encode("utf-8"),
            retrieved_at=now_iso(),
            sha256="deadbeef",
            content_type="text/html",
            attempts=1,
        )

    def test_weekly_completes_and_records_the_skipped_link(self) -> None:
        WeeklyBadLinkTests.fetched = []
        # All adapters and SMTP are fake in this case; only mock key presence.
        with mock.patch('learnradar.config.CandidateModel.api_key',
                        return_value='offline-test-placeholder'):
            code, output, _adapter = self.run_weekly(["a/one", "a/two", "a/three"])
        self.assertEqual(code, 0, output)
        self.assertEqual(self.store.get_cycle("2026-W39")["status"], "completed")
        emails = self.emails()
        self.assertEqual(len(emails), 1)
        self.assertEqual(emails[0]["status"], "sent")
        run_dir = self.data_dir / "runs" / "2026-W39-agent-memory"
        usage = json.loads((run_dir / "usage.json").read_text(encoding="utf-8"))
        self.assertGreaterEqual(usage["totals"]["calls"], 1, "初筛用量必须保留")
        context = json.loads((run_dir / "run-context.json").read_text(encoding="utf-8"))
        skipped = (context.get("learning_entry") or {}).get("skipped_links") or []
        self.assertEqual(len(skipped), 1, context.get("learning_entry"))
        self.assertIn("模板", skipped[0]["reason"])
        self.assertFalse(
            any("{{" in url for url in WeeklyBadLinkTests.fetched),
            f"模板地址绝不能被请求：{WeeklyBadLinkTests.fetched}",
        )


class FakeResponse:
    """Minimal response double for real net.fetch without connections."""

    def __init__(self, url: str, body: str, content_type: str = "text/html") -> None:
        self.status = 200
        self.headers = {"Content-Type": content_type}
        self._body = body.encode("utf-8")
        self._url = url

    def read(self, limit: int = -1) -> bytes:
        return self._body if limit < 0 else self._body[:limit]

    def geturl(self) -> str:
        return self._url

    def close(self) -> None:
        return None

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *exc_info: object) -> bool:
        return False


class RealFetchPathTests(unittest.TestCase):
    """Real net.fetch with fake DNS/opener never requests template URLs."""

    def test_template_link_never_reaches_the_network(self) -> None:
        calls: list[str] = []

        def fake_open(request, timeout=None):  # noqa: ANN001
            url = request.full_url
            calls.append(url)
            if "{{" in url:
                raise AssertionError(f"模板地址不应该进入网络层：{url}")
            if url.endswith("/get-started"):
                return FakeResponse(url, DOCS_HTML)
            return FakeResponse(url, "<pre>npm install demo</pre>")

        client = mock.Mock()
        client.user_agent = "ua"
        client.timeout = 5
        client.max_bytes = 100000
        client.audit = []
        readme = "## Docs\n\n[Getting started](https://docs.example.dev/get-started)\n"
        with mock.patch("learnradar.net._OPENER") as opener, mock.patch(
            "learnradar.net.socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("93.184.216.34", 443))],
        ), mock.patch(
            "learnradar.net.socket.create_connection",
            side_effect=AssertionError("不应该真的建立连接"),
        ):
            opener.open.side_effect = fake_open
            entry = collector._collect_learning_entry(
                client,
                {"full_name": "a/one"},
                readme,
                max_attempts=2,
                max_files=0,
                docs_followup_max=1,
                entry_evidence_max=2,
                remaining_sources=4,
                stamp=now_iso(),
            )
        self.assertFalse(any("{{" in url for url in calls), calls)
        self.assertEqual(calls, ["https://docs.example.dev/get-started", "https://docs.example.dev/getting-started"])
        self.assertTrue(entry["example_signal"], entry)
        self.assertEqual(len(entry["skipped_links"]), 1)
        self.assertIn("模板", entry["skipped_links"][0]["reason"])


if __name__ == "__main__":
    unittest.main()
