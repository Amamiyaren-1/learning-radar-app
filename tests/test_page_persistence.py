"""回归：收集器返回的页面必须由调用方落盘（正文 + 记录，失败也留记录）。"""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from learnradar import cli
from learnradar.config import load_settings
from learnradar.demo import DemoServices, demo_settings


class PagePersistenceTests(unittest.TestCase):
    def test_persist_pages_writes_text_and_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs_dir = Path(tmp)
            pages = [
                {
                    "url": "https://docs.example.dev/get-started/",
                    "final_url": "https://docs.example.dev/get-started/",
                    "retrieved_at": "2026-09-23T00:00:00+00:00",
                    "sha256": "abc123def456",
                    "ok": True,
                    "bytes": 1234,
                    "code_blocks": 3,
                    "example_signal": False,
                    "reason": "README 链接（docs；章节：Getting Started）",
                    "text": "<html>安装步骤：npm install demo</html>",
                    "kept_as_evidence": True,
                },
                {
                    "url": "https://docs.example.dev/missing",
                    "retrieved_at": "2026-09-23T00:00:00+00:00",
                    "ok": False,
                    "error": "HTTP 404",
                    "reason": "README 链接（docs；章节：Getting Started）",
                },
            ]
            cli._persist_pages(runs_dir, pages)

            manifest = json.loads((runs_dir / "pages.json").read_text(encoding="utf-8"))
            self.assertEqual(len(manifest), 2)
            self.assertEqual(manifest[0]["url"], "https://docs.example.dev/get-started/")
            self.assertEqual(manifest[0]["sha256"], "abc123def456")
            self.assertIn("retrieved_at", manifest[0])
            self.assertTrue(Path(manifest[0]["saved_text"]).exists())
            self.assertIn("npm install demo", Path(manifest[0]["saved_text"]).read_text(encoding="utf-8"))
            # 失败也要有记录，且没有正文文件
            self.assertFalse(manifest[1]["ok"])
            self.assertEqual(manifest[1]["error"], "HTTP 404")
            self.assertNotIn("saved_text", manifest[1])

    def test_cmd_run_actually_calls_persist_pages(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory=Path(tmp)
            settings=demo_settings(load_settings(local_overrides=False),directory)
            run_id="fixture-page-persistence"
            services=DemoServices(settings,run_id,delay=0)
            original_collect=services.collector
            def collect(client,name,**kwargs):
                result=original_collect(client,name,**kwargs)
                result.pages=[{"url":f"https://docs.example.invalid/{name}","ok":True,
                               "sha256":name.split('/')[-1],"text":f"fixture page for {name}"},
                              {"url":f"https://docs.example.invalid/{name}/missing","ok":False,"error":"fixture 404"}]
                return result
            def fixture_collect(instance,client,name,**kwargs):
                return collect(client,name,**kwargs)
            args=cli.build_parser().parse_args(["run","--topic","agent-memory","--run-id",run_id])
            with mock.patch.object(DemoServices,"collector",fixture_collect),contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cli.cmd_run(settings,args),0)
            manifest=json.loads((directory/"runs"/run_id/"pages.json").read_text(encoding="utf-8"))
            self.assertEqual(len(manifest),2)
            for name in services.names[:1]:
                entry=next(row for row in manifest if row["url"]==f"https://docs.example.invalid/{name}")
                self.assertEqual(Path(entry["saved_text"]).read_text(encoding="utf-8"),f"fixture page for {name}")
            self.assertEqual(sum(not row["ok"] for row in manifest),1)


if __name__ == "__main__":
    unittest.main()
