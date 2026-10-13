"""历史导航、旧上下文缺失与原报告反馈复用；隔离库与实际 HTTP。"""
import json
import tempfile
import unittest
from pathlib import Path
from learnradar.browse import history_records
from learnradar.config import load_settings
from learnradar.demo import demo_settings
from learnradar.store import Store
from learnradar.web import markdown_to_html
from tests import test_task_processes as helpers


class BrowseTests(unittest.TestCase):
    setUp=helpers.ProcessTests.setUp
    stop_all=helpers.ProcessTests.stop_all
    start=helpers.ProcessTests.start
    wait_until=helpers.ProcessTests.wait_until
    get=helpers.ProcessTests.get
    post=helpers.ProcessTests.post
    start_web=helpers.ProcessTests.start_web

    def seed(self,run_id,status,*,topic=None,context=None,date="2026-10-08T12:00:00+08:00"):
        directory=self.directory/"runs"/run_id;directory.mkdir(parents=True)
        if context is not None: (directory/"run-context.json").write_text(json.dumps(context),encoding="utf-8")
        with self.queue.store() as store:
            store.start_run(run_id,kind="fixture",started_at=date,snapshot={"topic":topic} if topic else {})
            store.finish_run(run_id,status=status,finished_at=date,notes="fixture 保存的失败原因")
            if status in ("published","rejected"):
                payload=json.loads((Path(__file__).parent/"fixtures"/"offline_analysis.json").read_text(encoding="utf-8"))
                md=self.directory/"reports"/(run_id+".md");md.parent.mkdir(exist_ok=True)
                md.write_text("# fixture 示例\n\n未实测",encoding="utf-8")
                store.save_report(run_id,report_id=run_id,created_at=date,status=status,title="fixture 示例报告",markdown_path=str(md),html_path="",payload=payload,
                    validation={"ok":status=="published","errors":["fixture 原拒绝原因"] if status=="rejected" else [],"warnings":[],"rules_version":"qa-6"})

    def test_grouping_saved_fields_missing_invalid_context_and_old_cycle_without_mutation(self):
        self.seed("old-published","published",topic="agent-memory")
        self.seed("old-rejected","rejected",context={"topic":"agent-memory"},date="2026-10-07")
        self.seed("old-failed","model-failed",context={"topic":"retrieval","target":"saved/project"})
        self.seed("2026-W39-agent-memory","failed")
        self.seed("broken-context","failed")
        (self.directory/"runs"/"broken-context"/"run-context.json").write_text("broken JSON",encoding="utf-8")
        self.seed("cycle-only","failed")
        with self.queue.store() as store:
            store.save_cycle("fixture-old-cycle",topic="tool-calling",status="failed",run_id="cycle-only",started_at="fixture")
            before="\n".join(store.conn.iterdump())
            records=history_records(store,load_settings(local_overrides=False).topics)
            self.assertEqual("\n".join(store.conn.iterdump()),before)
        by_id={r["run_id"]:r for r in records}
        self.assertEqual(len(records),6)
        self.assertEqual(by_id["old-published"]["project"],"example-offline/target")
        self.assertEqual(by_id["old-rejected"]["topic"],"agent-memory")
        self.assertEqual(by_id["old-failed"]["project"],"saved/project")
        self.assertEqual(by_id["2026-W39-agent-memory"]["topic"],"")
        self.assertEqual(by_id["broken-context"]["project"],"")
        self.assertEqual(by_id["cycle-only"]["topic"],"tool-calling")

    def test_http_theme_project_reports_rejection_unknown_and_saved_feedback(self):
        self.seed("fixture-published","published",topic="agent-memory")
        self.seed("fixture-rejected","rejected",topic="agent-memory",date="2026-10-07")
        self.seed("fixture-no-report","model-failed",context={"topic":"agent-memory","target":"example-offline/target"})
        self.seed("fixture-unclassified","failed")
        with self.queue.store() as store:
            store.create_email("fixture-mail",cycle_id="",run_id="fixture-published",kind="report",recipient="fixture@example.invalid",subject="fixture",body="fixture",status="unknown",now="fixture")
        self.start_web()
        self.assertIn('href="/history"',self.get("/"))
        self.assertIn("未分类",self.get("/history"))
        projects=self.get("/history?topic=agent-memory")
        self.assertIn("example-offline/target",projects)
        path="/history?topic=agent-memory&project=example-offline%2Ftarget"
        reports=self.get(path)
        self.assertIn("已发布",reports);self.assertIn("未通过检查",reports)
        self.assertIn("模型阶段失败",reports);self.assertIn("无报告",reports)
        self.assertIn("请人工核对，不自动重发",reports)
        self.assertIn('/report/fixture-published',reports)
        self.assertIn('/report/fixture-rejected',reports)
        rejected=self.get("/report/fixture-rejected")
        self.assertIn("fixture 原拒绝原因",rejected);self.assertIn("未发布草稿",rejected)
        self.assertIn("不推断长期偏好",self.get("/report/fixture-published"))
        status,location,_=self.post("/feedback/fixture-published",{"understood_what":"yes","understood_highlight":"unsure","want_more":"no","note":"fixture 历史反馈"})
        self.assertEqual(status,303)
        self.assertIn("反馈已保存",self.get(location))
        self.assertIn("fixture 历史反馈",self.get(path))
        self.assertIn("未分类",self.get("/history?topic=&project="))
        self.assertIn("fixture 保存的失败原因",self.get("/history?topic=&project="))

    def test_code_blocks_escape_and_scroll_independently(self):
        code="<script>"+"x"*500+"</script>"
        html=markdown_to_html("```python\n"+code+"\n```\n普通内容")
        self.assertIn('<pre tabindex="0"><code>&lt;script&gt;',html)
        self.assertNotIn("<script>",html)
        self.assertIn("普通内容",html)
