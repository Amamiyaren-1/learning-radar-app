"""HTTP feedback persists and reads back; rejected drafts accept feedback while damaged payloads show diagnostics only."""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from learnradar.store import Store
from learnradar.web import serve
from learnradar.web_config import WebConfig
from tests import auth_helpers

FIXTURES = Path(__file__).parent / "fixtures"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:  # noqa: D102
        return None


class PageFeedbackTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.data_dir = Path(self._tmp.name)
        self.store_path = self.data_dir / "learning-radar.sqlite"
        self.store = Store(self.store_path)
        self.addCleanup(self.store.close)
        auth_helpers.initialize_account(self.store_path)
        self.server = serve(self.store_path, host="127.0.0.1", port=0, web_config=WebConfig("loopback"))
        self.port = int(self.server.server_address[1])
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.opener = auth_helpers.opener()
        auth_helpers.login(self.opener, self.url(""))

    # --- helpers
    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def get(self, path: str) -> tuple[int, str]:
        with self.opener.open(self.url(path), timeout=5) as response:
            return response.status, response.read().decode("utf-8")

    def post(self, path: str, data: dict[str, str]) -> tuple[int, str, str]:
        data = {**data, "csrf_token": auth_helpers.csrf(self.opener, self.url(""))}
        body = urllib.parse.urlencode(data).encode("utf-8")
        request = urllib.request.Request(self.url(path), data=body, method="POST", headers={"Origin": self.url(""), "Sec-Fetch-Site": "same-origin"})
        try:
            with self.opener.open(request, timeout=5) as response:
                return response.status, response.headers.get("Location") or "", response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.headers.get("Location") or "", exc.read().decode("utf-8")

    def seed(
        self,
        run_id: str,
        status: str,
        *,
        payload: dict[str, Any] | None = None,
        markdown: str | None = "正文",
        validation: dict[str, Any] | None = None,
    ) -> None:
        md_path = self.data_dir / "reports" / f"{run_id}.md"
        md_path.parent.mkdir(parents=True, exist_ok=True)
        if markdown is not None:
            md_path.write_text(markdown, encoding="utf-8")
        if payload is None:
            payload = json.loads((FIXTURES / "offline_analysis.json").read_text(encoding="utf-8"))
        self.store.save_report(
            run_id,
            report_id=run_id,
            created_at="2026-09-23 21:00:00",
            status=status,
            title="示例报告",
            markdown_path=str(md_path) if markdown is not None else "",
            html_path="",
            payload=payload,
            validation=validation
            or {
                "ok": status == "published",
                "errors": [],
                "warnings": [],
                "rules_version": "qa-5",
                "content_review": "not_performed",
            },
        )

    # --- Published
    def test_published_report_saves_and_reopens_feedback(self) -> None:
        self.seed("run-1", "published", markdown="# 示例报告\n\n正文一段。")
        status, html = self.get("/report/run-1")
        self.assertEqual(status, 200)
        self.assertIn("是否读懂项目做什么", html)
        self.assertIn("是否看懂至少一个具体亮点", html)
        self.assertIn("是否想进一步了解", html)
        self.assertIn('action="/feedback/run-1"', html)

        code, location, _ = self.post(
            "/feedback/run-1",
            {
                "understood_what": "yes",
                "understood_highlight": "unsure",
                "want_more": "no",
                "note": "备注一",
            },
        )
        self.assertEqual(code, 303)
        self.assertEqual(location, "/report/run-1?saved=1")
        row = self.store.get_feedback("run-1")
        self.assertIsNotNone(row)
        self.assertEqual(row["repo"], "example-offline/target")
        self.assertEqual(row["want_more"], "no")
        self.assertEqual(row["note"], "备注一")
        self.assertTrue(row["answered_at"])

        status, html = self.get("/report/run-1?saved=1")
        self.assertEqual(status, 200)
        self.assertIn("反馈已保存", html)
        self.assertIn('value="no" checked', html)
        self.assertIn('value="unsure" checked', html)
        self.assertIn("备注一", html)

    def test_resubmit_updates_instead_of_duplicating(self) -> None:
        self.seed("run-1", "published", markdown="# 示例报告\n\n正文一段。")
        self.post("/feedback/run-1", {"understood_what": "no", "want_more": "no", "note": "第一次"})
        self.post("/feedback/run-1", {"understood_what": "yes", "want_more": "yes", "note": "第二次"})
        count = self.store.conn.execute("SELECT COUNT(*) AS c FROM feedback").fetchone()["c"]
        self.assertEqual(count, 1)
        row = self.store.get_feedback("run-1")
        self.assertEqual(row["understood_what"], "yes")
        self.assertEqual(row["want_more"], "yes")
        self.assertEqual(row["note"], "第二次")

    def test_note_is_escaped_on_the_page(self) -> None:
        self.seed("run-1", "published", markdown="# 示例报告\n\n正文一段。")
        self.post("/feedback/run-1", {"want_more": "no", "note": "<script>alert(1)</script>"})
        _status, html = self.get("/report/run-1")
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)

    # --- Rejected draft / damaged payload
    def test_rejected_preview_keeps_status_and_accepts_feedback(self) -> None:
        self.seed(
            "run-2",
            "rejected",
            markdown="# 报告未通过结构检查（未发布）\n\n诊断",
            validation={
                "ok": False,
                "errors": ["claims[1].quote 无法在所引证据中匹配"],
                "warnings": [],
                "rules_version": "qa-5",
                "content_review": "not_performed",
            },
        )
        status, html = self.get("/report/run-2")
        self.assertEqual(status, 200)
        self.assertIn("未发布：结构检查未通过", html)
        self.assertIn("claims[1].quote", html)
        self.assertIn("未发布草稿", html, "结构完整的预览要展示正文，而不是假装没有报告")
        self.assertIn('action="/feedback/run-2"', html)
        code, _location, _html = self.post(
            "/feedback/run-2", {"understood_what": "yes", "want_more": "no"}
        )
        self.assertEqual(code, 303)
        self.assertIsNotNone(self.store.get_feedback("run-2"))

    def test_broken_run_shows_diagnostics_only(self) -> None:
        self.seed(
            "run-3",
            "render-failed",
            markdown=None,
            payload={"error": "ValueError: 渲染失败"},
            validation={"ok": False, "errors": ["渲染失败"], "warnings": [], "rules_version": "qa-5"},
        )
        status, html = self.get("/report/run-3")
        self.assertEqual(status, 200)
        self.assertIn("未发布：渲染失败", html)
        self.assertIn("不提供反馈表单", html)
        self.assertNotIn('action="/feedback/', html)
        code, _location, _html = self.post("/feedback/run-3", {"want_more": "no"})
        self.assertEqual(code, 400)
        self.assertIsNone(self.store.get_feedback("run-3"))

    def test_unknown_run_returns_404(self) -> None:
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get("/report/not-a-run")
        self.assertEqual(ctx.exception.code, 404)


if __name__ == "__main__":
    unittest.main()
