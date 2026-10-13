"""SQLite 存储测试：去重主键、报告与用量读回。"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from learnradar.evidence import Evidence
from learnradar.store import Store


class StoreTests(unittest.TestCase):
    def test_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "t.sqlite"
            evidence = Evidence(
                evidence_id="E-repo-x",
                kind="github_repo",
                url="https://github.com/a/b",
                title="a/b",
                retrieved_at="2026-09-21T00:00:00Z",
            )
            with Store(path) as store:
                store.start_run("run-1", kind="test", started_at="2026-09-21T00:00:00Z", snapshot={})
                store.save_evidence("run-1", [evidence])
                store.save_evidence("run-1", [evidence])  # 重复写入不应报错
                store.save_report(
                    "run-1",
                    report_id="run-1",
                    created_at="2026-09-21T00:00:00Z",
                    status="published",
                    title="标题",
                    markdown_path="x.md",
                    html_path="",
                    payload={"title": "标题"},
                    validation={"ok": True},
                )
                store.save_usage(
                    "run-1",
                    [{"ts": "2026-09-21T00:00:00Z", "stage": "analysis", "provider": "p", "model_id": "m",
                      "calls": 1, "cost_value": None, "pricing_known": False}],
                )
                store.finish_run("run-1", status="published", finished_at="2026-09-21T00:01:00Z")
                self.assertEqual(len(store.get_evidence("run-1")), 1)
                self.assertEqual(store.list_reports()[0]["title"], "标题")
                self.assertEqual(store.get_report("run-1")["status"], "published")
                self.assertEqual(len(store.usage_for_run("run-1")), 1)


    def test_usage_log_migration_adds_ok_and_error_columns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "legacy.sqlite"
            legacy = sqlite3.connect(path)
            legacy.execute(
                "CREATE TABLE usage_log (run_id TEXT, ts TEXT, stage TEXT, provider TEXT,"
                " model_id TEXT, input_tokens INTEGER, output_tokens INTEGER, reasoning_tokens INTEGER,"
                " calls INTEGER, cost_value REAL, cost_currency TEXT, pricing_known INTEGER)"
            )
            legacy.commit()
            legacy.close()
            with Store(path) as store:
                columns = {row[1] for row in store.conn.execute("PRAGMA table_info(usage_log)")}
                self.assertIn("ok", columns)
                self.assertIn("error", columns)
                store.save_usage(
                    "run-legacy",
                    [
                        {
                            "ts": "2026-09-21T00:00:00Z",
                            "stage": "screening",
                            "provider": "chat_completions",
                            "model_id": "deepseek-flash",
                            "calls": 0,
                            "ok": False,
                            "error": "模拟失败",
                            "pricing_known": False,
                        }
                    ],
                )
                row = store.usage_for_run("run-legacy")[0]
                self.assertEqual(row["ok"], 0)
                self.assertEqual(row["error"], "模拟失败")

if __name__ == "__main__":
    unittest.main()
