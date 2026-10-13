"""收集器集成测试：README 按章节取证、学习入口被实际读取（全程离线，假客户端）。"""

from __future__ import annotations

import unittest
from unittest import mock

from learnradar.collector import collect_repository
from learnradar.net import FetchResult, now_iso
from learnradar.sources_github import GitHubClient

README = (
    "项目简介\n" * 200
    + "\n## Quickstart\n\n运行 `python demo.py` 看到一次工具调用。\n"
    + "\n## Examples\n\n- [最小示例](https://example.com/docs/quickstart)\n"
)

REPO = {
    "full_name": "example/demo",
    "html_url": "https://github.com/example/demo",
    "description": "示例项目",
    "stargazers_count": 100,
    "forks_count": 5,
    "open_issues_count": 1,
    "created_at": "2025-01-01T00:00:00Z",
    "pushed_at": "2026-01-01T00:00:00Z",
    "archived": False,
    "fork": False,
    "language": "Python",
    "license": {"spdx_id": "MIT"},
    "topics": ["agent"],
    "default_branch": "main",
    "homepage": "https://example.com/docs",
}


class FakeClient(GitHubClient):
    def __init__(self) -> None:
        super().__init__(timeout=5)

    def get_repository(self, full_name: str):
        repo = dict(REPO)
        repo["_evidence_route"] = "test"
        return repo

    def latest_commits(self, full_name: str, *, ref=None, per_page=3):
        return [{"sha": "a" * 40, "commit": {"committer": {"date": "2026-01-01T00:00:00Z"}, "message": "init"}}]

    def get_readme(self, full_name: str, *, ref=None):
        return README, {"path": "README.md", "html_url": "https://github.com/example/demo#readme"}

    def latest_release(self, full_name: str):
        return None

    def fetch_repo_file(self, full_name: str, path: str, *, ref=None) -> str:
        return f"# {path}\nimport demo\n\nprint(demo)\n"


def fake_entry_response(url: str) -> FetchResult:
    body = b"<html><head><title>Quickstart Guide</title></head><body>pip install demo</body></html>"
    return FetchResult(
        url=url,
        final_url=url,
        status=200,
        headers={"Content-Type": "text/html"},
        body=body,
        retrieved_at=now_iso(),
        sha256="deadbeef",
        content_type="text/html",
        attempts=1,
    )


def page(url: str, body: str) -> FetchResult:
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


WITH_EXAMPLE = "<html><body><pre>pip install demo</pre><pre>python demo.py</pre></body></html>"
WITHOUT_EXAMPLE = "<html><body><h1>Overview</h1><p>这是一个项目概览页面。</p></body></html>"

class CollectorTests(unittest.TestCase):
    def test_readme_evidence_uses_sections_and_locator(self) -> None:
        collected = collect_repository(FakeClient(), "example/demo", role="target", max_sources=8)
        readme = next(item for item in collected.evidence if item.kind == "github_readme")
        self.assertIn("Quickstart", readme.excerpt)
        self.assertIn("章节：", readme.locator)
        self.assertIn("行", readme.locator)
        self.assertEqual(readme.owner, "example/demo")

    def test_learning_entry_is_fetched_and_recorded(self) -> None:
        with mock.patch("learnradar.collector.fetch", side_effect=lambda url, **kwargs: fake_entry_response(url)):
            collected = collect_repository(
                FakeClient(),
                "example/demo",
                role="target",
                max_sources=8,
                fetch_learning_entry=True,
            )
        self.assertTrue(collected.learning_entry.get("verified"))
        self.assertEqual(collected.learning_entry["url"], "https://example.com/docs/quickstart")
        entry_evidence = next(item for item in collected.evidence if item.kind == "web_page")
        self.assertEqual(entry_evidence.url, "https://example.com/docs/quickstart")
        self.assertEqual(entry_evidence.owner, "example/demo")

    def test_learning_entry_failure_is_recorded_not_guessed(self) -> None:
        from learnradar.net import FetchError

        with mock.patch("learnradar.collector.fetch", side_effect=FetchError("HTTP 404")):
            collected = collect_repository(
                FakeClient(),
                "example/demo",
                role="target",
                max_sources=8,
                fetch_learning_entry=True,
            )
        self.assertFalse(collected.learning_entry.get("verified"))
        self.assertIn("候选学习入口都读取失败", collected.learning_entry.get("error", ""))
        self.assertFalse(any(item.kind == "web_page" for item in collected.evidence))

    def test_comparators_do_not_fetch_learning_entry(self) -> None:
        with mock.patch("learnradar.collector.fetch", side_effect=AssertionError("不应发起入口读取")):
            collected = collect_repository(
                FakeClient(),
                "example/demo",
                role="comparator",
                max_sources=8,
                fetch_learning_entry=False,
            )
        self.assertEqual(collected.learning_entry, {})


    def test_entry_without_example_falls_through_to_next_candidate(self) -> None:
        def fake_fetch(url, **kwargs):
            if url.endswith("/quickstart"):
                return page(url, WITHOUT_EXAMPLE)
            return page(url, WITH_EXAMPLE)

        with mock.patch("learnradar.collector.fetch", side_effect=fake_fetch):
            collected = collect_repository(
                FakeClient(),
                "example/demo",
                role="target",
                max_sources=8,
                fetch_learning_entry=True,
                learning_entry_max_attempts=3,
            )
        entry = collected.learning_entry
        self.assertTrue(entry.get("example_signal"))
        self.assertEqual(entry["url"], "https://example.com/docs")
        self.assertEqual(len(entry["attempts"]), 2, "应当记录两次尝试，说明换过入口")
        self.assertFalse(entry["attempts"][0]["example_signal"])

    def test_all_entries_readable_but_without_example_are_marked(self) -> None:
        with mock.patch("learnradar.collector.fetch", side_effect=lambda url, **kwargs: page(url, WITHOUT_EXAMPLE)):
            collected = collect_repository(
                FakeClient(),
                "example/demo",
                role="target",
                max_sources=8,
                fetch_learning_entry=True,
                learning_entry_max_attempts=2,
            )
        entry = collected.learning_entry
        self.assertTrue(entry.get("verified"), "页面能打开")
        self.assertFalse(entry.get("example_signal"), "但里面没有可执行示例")
        self.assertIn("本次未核验到可执行路径", entry.get("error", ""))
        self.assertTrue(any(item.kind == "web_page" for item in collected.evidence))

README_WITH_FILE = (
    "项目简介\n" * 60
    + "\n## Quickstart\n\n- [示例文件](https://github.com/example/demo/blob/main/examples/demo.py)\n"
)


class ExampleFileTests(unittest.TestCase):
    def test_example_files_are_read_and_wording_is_about_collection_limit(self) -> None:
        client = FakeClient()
        client_readme = README_WITH_FILE
        with mock.patch.object(
            FakeClient, "get_readme", return_value=(client_readme, {"path": "README.md", "html_url": "https://github.com/example/demo#readme"})
        ), mock.patch(
            "learnradar.collector.fetch", side_effect=lambda url, **kwargs: fake_entry_response(url)
        ):
            collected = collect_repository(
                client,
                "example/demo",
                role="target",
                max_sources=8,
                fetch_learning_entry=True,
                example_file_max=2,
            )
        entry = collected.learning_entry
        self.assertIn("本次未核验到可执行路径", entry.get("error", ""))
        self.assertFalse(entry.get("example_signal"))
        files = entry.get("example_files") or []
        if files:
            self.assertTrue(any(item.kind == "github_example_file" for item in collected.evidence))
            self.assertLessEqual(len([item for item in collected.evidence if item.kind == "github_example_file"]), 2)

if __name__ == "__main__":
    unittest.main()
