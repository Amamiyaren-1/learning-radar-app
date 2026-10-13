"""行为验证：正常 CLI 编排下，页面正文/成功与失败清单/模型证据都要落盘。"""

from __future__ import annotations

import argparse
import dataclasses
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from learnradar import cli
from learnradar.config import load_settings
from learnradar.models import ModelError
from learnradar.net import FetchError, FetchResult, now_iso
from learnradar.screening import ScreeningOutcome

README = "x [Docs](https://docs.example.dev/get-started) [Examples](https://github.com/a/one-examples)\n\n"


def fake_fetch(url, **kwargs):
    if "missing" in url:
        raise FetchError("HTTP 404")
    return FetchResult(
        url=url,
        final_url=url,
        status=200,
        headers={"Content-Type": "text/html"},
        body=b"<pre>npm install demo</pre>",
        retrieved_at=now_iso(),
        sha256="deadbeef",
        content_type="text/html",
        attempts=1,
    )


class OrchestrationPagesTest(unittest.TestCase):
    def test_cmd_run_persists_pages_and_evidence(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = load_settings()
        settings = dataclasses.replace(base, app={**base.app, 'data_dir': tmp.name})
        item = {"full_name": "a/one", "description": "d", "language": "Python",
                "pushed_at": "2026-09-01T00:00:00Z", "stargazers_count": 10,
                "fork": False, "archived": False, "default_branch": "main",
                "html_url": "https://github.com/a/one"}
        def make_item(name: str):
            data = dict(item)
            data["full_name"] = name
            data["html_url"] = f"https://github.com/{name}"
            return data

        items = [make_item("a/one"), make_item("a/two"), make_item("a/three")]
        client = mock.Mock()
        client.search_repositories.return_value = items
        client.get_repository.side_effect = lambda name: next(
            (entry for entry in items if entry["full_name"] == name), items[0]
        )
        client.latest_commits.return_value = [{"sha": "a" * 40, "commit": {"committer": {"date": "2026-09-01T00:00:00Z"}, "message": "init"}}]
        client.get_readme.return_value = (README, {"path": "README.md", "html_url": "https://github.com/a/one#readme"})
        client.latest_release.return_value = None
        client.notes = []
        client.audit = []
        client.mode = "api"
        client.user_agent = "ua"
        client.timeout = 5
        client.max_bytes = 100000
        screening = ScreeningOutcome(
            entries=[{"repo": "a/one", "decision": "keep", "fit": "high", "reason": "相关", "gate": ""}]
        )
        args = argparse.Namespace(
            topic=None, candidate=None, compare=None, model=None, per_page=5, limit=5,
            candidates_from=None, screening_only=False, screening_from=None,
        )
        with mock.patch.object(cli, "_client", return_value=client), \
             mock.patch.object(cli, "_run_screening_stage", return_value=screening), \
             mock.patch("learnradar.collector.fetch", side_effect=fake_fetch), \
             mock.patch.object(cli, "build_adapter", side_effect=ModelError("测试用：无真实模型")):
            code = cli.cmd_run(settings, args)
        self.assertEqual(code, cli.EXIT_MODEL_UNAVAILABLE)
        runs = list((Path(tmp.name) / "runs").iterdir())
        self.assertEqual(len(runs), 1)
        run_dir = runs[0]
        pages = json.loads((run_dir / "pages.json").read_text(encoding="utf-8"))
        self.assertTrue(pages, "必须留下页面获取记录")
        self.assertTrue(any(p.get("ok") for p in pages), "要有成功记录")
        saved = [p for p in pages if p.get("saved_text")]
        self.assertTrue(saved, "成功页面要落盘正文")
        for record in saved:
            self.assertIn("npm install demo", Path(record["saved_text"]).read_text(encoding="utf-8"))
        self.assertTrue((run_dir / "evidence.json").exists(), "模型证据要落盘")
        evidence = json.loads((run_dir / "evidence.json").read_text(encoding="utf-8"))
        self.assertTrue(any(item["kind"] == "web_page" for item in evidence))
        self.assertTrue((run_dir / "usage.json").exists(), "用量要落盘")
        for record in pages:
            self.assertIn("retrieved_at", record)
            self.assertIn("url", record)


if __name__ == "__main__":
    unittest.main()
