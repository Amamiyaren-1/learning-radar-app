"""Isolated transaction/concurrency/budget tests with fake models and SMTP."""
import contextlib
import dataclasses
import io
import json
import os
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from learnradar.cli import BudgetedAdapter, ModelBudgetExceeded
from learnradar.config import load_settings
from learnradar.cycle import collect_outcome
from learnradar.demo import demo_settings
from learnradar.history import CooldownConfig, decide, target_history
from learnradar.models import ChatCompletionsAdapter, ModelError
from learnradar.net import FetchError
from learnradar.store import Store, backup_database
from learnradar.tasks import TaskBudget, TaskError, TaskQueue, QuotaWait, WorkerLock
from learnradar.worker import run_worker


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory=Path(self.tmp.name)
        self.time=datetime.fromisoformat("2026-10-08T12:00:00+08:00")
        self.queue=TaskQueue(self.directory,demo=True,clock=lambda:self.time)
        self.settings=demo_settings(load_settings(local_overrides=False),self.directory)

    def submit(self,key="fixture-submission-1"):
        return self.queue.submit("agent-memory",key)

    def claim(self):
        return self.queue.claim("fixture-worker")

    def work(self,scenario="success"):
        with contextlib.redirect_stdout(io.StringIO()):
            return run_worker(self.settings,self.queue,once=True,demo_delay=0,scenario=scenario)

    def test_idempotent_submission_survives_new_queue_and_quota(self):
        first=self.submit()
        self.submit("fixture-submission-2")
        self.submit("fixture-submission-3")
        other=TaskQueue(self.directory,demo=True)
        self.assertEqual(other.submit("agent-memory","fixture-submission-1")["task_id"],first["task_id"])
        with self.assertRaises(QuotaWait):
            self.submit("fixture-submission-4")
        with self.assertRaises(TaskError):
            other.submit("retrieval","fixture-submission-1")

    def test_concurrent_submissions_reserve_only_three(self):
        def submit(i):
            q=TaskQueue(self.directory,demo=True,clock=lambda:self.time)
            try:
                return q.submit("agent-memory",f"fixture-parallel-{i:03}")["task_id"]
            except QuotaWait:
                return None
        with ThreadPoolExecutor(max_workers=8) as pool:
            values=list(pool.map(submit,range(12)))
        self.assertEqual(sum(x is not None for x in values),3)
        self.assertEqual(len(self.queue.list()),3)

    def test_concurrent_retransmits_and_claims_have_one_winner(self):
        def submit(_):
            return TaskQueue(self.directory,demo=True).submit("agent-memory","fixture-same-payload")["task_id"]
        with ThreadPoolExecutor(max_workers=6) as pool:
            ids=list(pool.map(submit,range(6)))
        self.assertEqual(len(set(ids)),1)
        self.submit("fixture-new-same-topic")
        with ThreadPoolExecutor(max_workers=6) as pool:
            rows=list(pool.map(lambda _:TaskQueue(self.directory,demo=True).claim("test"),range(6)))
        self.assertEqual(sum(row is not None for row in rows),1)

    def test_cross_day_wait_releases_old_reservation_then_converts_new_date(self):
        old=self.submit()
        self.claim()
        self.time+=timedelta(days=1)
        for i in range(3): self.submit(f"fixture-next-day-{i}")
        budget=TaskBudget(self.queue,old["task_id"])
        with self.assertRaises(QuotaWait): budget.consume()
        saved=self.queue.get(old["task_id"])
        self.assertEqual(saved["status"],"waiting_quota")
        self.assertEqual(saved["model_requests"],0)
        self.assertIsNone(saved["reserved_day"])
        self.assertIsNone(saved["consumed_day"])
        current=next(row for row in self.queue.list() if row["task_id"]!=old["task_id"])
        self.queue.cancel(current["task_id"])
        self.assertEqual(self.claim()["task_id"],old["task_id"])
        number=budget.consume()
        budget.finish_request(number,"failed")
        saved=self.queue.get(old["task_id"])
        self.assertEqual(saved["consumed_day"],"2026-10-09")
        self.assertIsNone(saved["reserved_day"])
        self.queue.finish(old["task_id"],"failed")
        with self.assertRaises(QuotaWait): self.submit("fixture-next-day-extra")

    def test_day_uses_shanghai_not_host_timezone(self):
        self.time=datetime.fromisoformat("2026-10-08T17:00:00+00:00")
        task=self.submit()
        self.assertEqual(task["reserved_day"],"2026-10-09")

    def test_zero_call_failure_and_cancellation_release_reservations(self):
        tasks=[self.submit(f"fixture-release-{i}") for i in range(3)]
        self.claim()
        self.queue.finish(tasks[0]["task_id"],"failed")
        self.submit("fixture-release-new")
        self.queue.cancel(tasks[1]["task_id"])
        self.submit("fixture-release-newer")
        self.assertEqual(self.queue.get(tasks[0]["task_id"])["model_requests"],0)

    def test_weekly_cancel_rejected_without_record_or_quota_changes_and_reuses_task(self):
        for i in range(3): self.submit(f"fixture-weekly-cancel-manual-{i}")
        weekly=self.queue.submit_weekly("agent-memory","fixture-cancel-cycle")
        before=self.queue.get(weekly["task_id"])
        with self.queue.store() as store:
            cycle_before=store.get_cycle("fixture-cancel-cycle")
            tasks_before=[dict(r) for r in store.conn.execute("SELECT * FROM tasks ORDER BY rowid")]
            occupied_before=self.queue.occupied(store.conn,"2026-10-08")
        with self.assertRaisesRegex(TaskError,"定时周报任务暂不支持取消"):
            self.queue.cancel(weekly["task_id"])
        self.assertEqual(self.queue.get(weekly["task_id"]),before)
        with self.queue.store() as store:
            self.assertEqual(store.get_cycle("fixture-cancel-cycle"),cycle_before)
            self.assertEqual([dict(r) for r in store.conn.execute("SELECT * FROM tasks ORDER BY rowid")],tasks_before)
            self.assertEqual(self.queue.occupied(store.conn,"2026-10-08"),occupied_before)
        repeated=self.queue.submit_weekly("agent-memory","fixture-cancel-cycle")
        self.assertEqual(repeated,before)
        self.assertEqual(len(self.queue.list()),4)

    def test_manual_cancel_releases_reservation_but_model_started_cannot_cancel(self):
        tasks=[self.submit(f"fixture-cancel-boundary-{i}") for i in range(3)]
        with self.assertRaises(QuotaWait): self.submit("fixture-cancel-boundary-new")
        self.queue.cancel(tasks[1]["task_id"])
        cancelled=self.queue.get(tasks[1]["task_id"])
        self.assertEqual(cancelled["status"],"cancelled")
        self.assertIsNone(cancelled["reserved_day"])
        self.assertIsNone(cancelled["consumed_day"])
        self.assertEqual(cancelled["model_requests"],0)
        self.submit("fixture-cancel-boundary-new")
        started=self.claim()
        TaskBudget(self.queue,started["task_id"]).consume()  # Persist isolated request accounting without sending a request.
        before=self.queue.get(started["task_id"])
        with self.assertRaisesRegex(TaskError,"只能取消尚未执行且没有模型请求"):
            self.queue.cancel(started["task_id"])
        self.assertEqual(self.queue.get(started["task_id"]),before)

    def test_waiting_quota_manual_task_can_still_be_cancelled(self):
        task=self.submit("fixture-cancel-waiting-quota")
        self.claim()
        self.time+=timedelta(days=1)
        for i in range(3): self.submit(f"fixture-cancel-waiting-new-day-{i}")
        with self.assertRaises(QuotaWait): TaskBudget(self.queue,task["task_id"]).consume()
        self.assertEqual(self.queue.get(task["task_id"])["status"],"waiting_quota")
        self.queue.cancel(task["task_id"])
        row=self.queue.get(task["task_id"])
        self.assertEqual(row["status"],"cancelled")
        self.assertIsNone(row["reserved_day"])
        self.assertIsNone(row["consumed_day"])
        self.assertEqual(row["model_requests"],0)
        with self.queue.store() as store:
            self.assertEqual(self.queue.occupied(store.conn,"2026-10-09"),3)

    def test_persistent_request_limit_rebuild_and_failure_count(self):
        task=self.submit();self.claim()
        for _ in range(4):
            budget=TaskBudget(TaskQueue(self.directory,demo=True,clock=lambda:self.time),task["task_id"])
            attempt=budget.consume();budget.finish_request(attempt,"failed")
        with self.assertRaises(ModelBudgetExceeded): TaskBudget(self.queue,task["task_id"]).consume()
        saved=self.queue.get(task["task_id"])
        self.assertEqual(saved["model_requests"],4)
        self.assertEqual([r["attempt"] for r in saved["requests"]],[1,2,3,4])
        self.assertEqual(saved["consumed_day"],"2026-10-08")

    def test_missing_model_credentials_do_not_consume_at_http_boundary(self):
        task=self.submit();self.claim()
        candidate=dataclasses.replace(load_settings(local_overrides=False).models["pro"],api_key_env="FIXTURE_MISSING_SECRET")
        adapter=BudgetedAdapter(ChatCompletionsAdapter(candidate),TaskBudget(self.queue,task["task_id"]))
        with mock.patch.dict(os.environ,{"FIXTURE_MISSING_SECRET":""}),mock.patch("learnradar.models.post_json") as post:
            with self.assertRaises(ModelError): adapter.complete(system="fixture",user="fixture")
            post.assert_not_called()
        self.assertEqual(self.queue.get(task["task_id"])["model_requests"],0)

    def test_http_attempt_is_committed_before_dispatch_and_errors_stay_uncertain(self):
        task=self.submit();self.claim()
        candidate=dataclasses.replace(load_settings(local_overrides=False).models["pro"],api_key_env="FIXTURE_SECRET")
        adapter=BudgetedAdapter(ChatCompletionsAdapter(candidate),TaskBudget(self.queue,task["task_id"]))
        def dispatch(*args,**kwargs):
            row=self.queue.get(task["task_id"])
            self.assertEqual(row["model_requests"],1)
            self.assertEqual(row["requests"][0]["status"],"pending")
            self.assertEqual(kwargs["retries"],0)
            raise FetchError("fixture transport result unknown")
        with mock.patch.dict(os.environ,{"FIXTURE_SECRET":"fixture"}),mock.patch("learnradar.models.post_json",side_effect=dispatch):
            with self.assertRaises(ModelError): adapter.complete(system="fixture",user="fixture")
        row=self.queue.get(task["task_id"])
        self.assertTrue(row["uncertain"])
        self.assertEqual(row["requests"][0]["status"],"unknown")

    def test_recovery_requires_system_lock_retains_paid_attempts_and_next_task(self):
        paid=self.submit();waiting=self.submit("fixture-pending-other")
        self.claim();TaskBudget(self.queue,paid["task_id"]).consume()
        lock=WorkerLock(self.directory)
        with self.assertRaises(TaskError): self.queue.recover(lock)
        self.assertTrue(lock.acquire())
        try: self.queue.recover(lock)
        finally: lock.release()
        row=self.queue.get(paid["task_id"])
        self.assertEqual(row["status"],"interrupted")
        self.assertEqual(row["model_requests"],1)
        self.assertEqual(row["requests"][0]["status"],"unknown")
        self.assertEqual(self.claim()["task_id"],waiting["task_id"])

    def test_demo_cannot_migrate_real_database_or_formal_use_demo(self):
        other=self.directory/"other"
        with Store(other/"learning-radar.sqlite") as store:
            store.start_run("saved",kind="legacy",started_at="fixture",snapshot={})
        before=(other/"learning-radar.sqlite").read_bytes()
        with self.assertRaises(TaskError): TaskQueue(other,demo=True)
        self.assertEqual((other/"learning-radar.sqlite").read_bytes(),before)
        with self.assertRaises(TaskError): TaskQueue(self.directory,demo=False)

    def test_concurrent_environment_initialization_cannot_mix_modes(self):
        directory=self.directory/"mode-race"
        barrier=threading.Barrier(2)
        def open_mode(demo):
            barrier.wait()
            try:
                TaskQueue(directory,demo=demo)
                return "demo" if demo else "production"
            except TaskError:
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            modes=list(pool.map(open_mode,(True,False)))
        self.assertEqual(sum(mode is not None for mode in modes),1)
        with Store(directory/"learning-radar.sqlite") as store:
            self.assertIn(store.conn.execute("SELECT mode FROM task_environment").fetchone()[0],modes)

    def test_concurrent_request_attempts_cannot_exceed_persistent_limit(self):
        task=self.submit();self.claim()
        def request(_):
            budget=TaskBudget(TaskQueue(self.directory,demo=True,clock=lambda:self.time),task["task_id"])
            try:
                return budget.consume()
            except ModelBudgetExceeded:
                return None
        with ThreadPoolExecutor(max_workers=8) as pool:
            attempts=list(pool.map(request,range(12)))
        self.assertEqual(sorted(n for n in attempts if n is not None),[1,2,3,4])
        self.assertEqual(self.queue.get(task["task_id"])["model_requests"],4)

    def test_legacy_database_copy_upgrade_and_online_backup_preserve_records(self):
        source=self.directory/"legacy.sqlite"
        with Store(source) as store:
            store.start_run("old",kind="legacy",started_at="2026-10-07T12:00:00+08:00",snapshot={})
            store.save_report("old",report_id="old",created_at="2026-10-07T12:00:00+08:00",status="rejected",title="fixture",markdown_path="",html_path="",payload={"target":{"repo":"fixture/repo"}},validation={"ok":False})
            store.save_feedback("old","fixture/repo",{},"fixture",now="fixture")
            store.save_cycle("fixture-cycle",status="completed",run_id="old",started_at="fixture")
            store.create_email("old",cycle_id="fixture-cycle",run_id="old",kind="failure",recipient="fixture@example.invalid",subject="fixture",body="fixture",status="unknown",now="fixture")
            for table in ("task_events","task_requests","tasks","task_environment"):
                store.conn.execute(f"DROP TABLE {table}")
            store.conn.commit()
        copy=self.directory/"upgrade"/"learning-radar.sqlite"
        backup_database(source,copy)
        TaskQueue(copy.parent,demo=False)
        with Store(copy) as store:
            self.assertEqual(store.get_report("old")["status"],"rejected")
            self.assertEqual(store.get_feedback("old")["note"],"fixture")
            self.assertEqual(store.get_cycle("fixture-cycle")["status"],"completed")
            self.assertEqual(store.get_email("old")["status"],"unknown")
            history=target_history(store,runs_dir=copy.parent/"runs")
            self.assertEqual(decide("fixture/repo",history["fixture/repo"],config=CooldownConfig(),now="2026-10-08T12:00:00+08:00").state,"skipped")
        backup_database(copy,self.directory/"snapshot.sqlite")
        with contextlib.closing(sqlite3.connect(self.directory/"snapshot.sqlite")) as connection:
            self.assertEqual(connection.execute("SELECT mode FROM task_environment").fetchone()[0],"production")

    def test_success_stages_saved_results_and_weekly_is_untouched(self):
        task=self.submit();self.work()
        row=self.queue.get(task["task_id"])
        self.assertEqual(row["status"],"completed")
        self.assertEqual(row["report"]["status"],"published")
        self.assertEqual(row["email"]["status"],"sent")
        self.assertEqual(row["email"]["cycle_id"],"")
        self.assertEqual(row["model_requests"],2)
        self.assertEqual([e["phase"] for e in row["events"]],["search","screening","evidence","analysis","check","archive","notification"])
        with self.queue.store() as store:
            self.assertEqual(store.list_cycles(),[])
        self.assertTrue(list((self.directory/"fixture-mail").glob("*.eml")))

    def test_rejected_processing_and_failure_notification_are_separate(self):
        task=self.submit();self.work("rejected")
        row=self.queue.get(task["task_id"])
        self.assertEqual(row["status"],"completed")
        self.assertEqual(row["report"]["status"],"rejected")
        self.assertEqual(row["email"]["kind"],"failure")
        self.assertEqual(row["email"]["status"],"sent")
        self.assertFalse(row["email"]["attachment_path"])

    def test_mail_failed_retry_does_not_analyze_and_unknown_is_refused(self):
        task=self.submit();self.work("mail-failed")
        row=self.queue.get(task["task_id"])
        self.assertEqual(row["report"]["status"],"published")
        self.assertEqual(row["email"]["status"],"failed")
        self.queue.retry_email(task["task_id"])
        with mock.patch("learnradar.worker.execute_recommendation",side_effect=AssertionError("retry must not analyze")):
            self.work()
        self.assertEqual(self.queue.get(task["task_id"])["model_requests"],2)
        self.assertEqual(self.queue.get(task["task_id"])["email"]["status"],"sent")
        other=self.submit("fixture-unknown-email");self.work("mail-unknown")
        row=self.queue.get(other["task_id"])
        self.assertEqual(row["email"]["status"],"unknown")
        with self.assertRaises(TaskError): self.queue.retry_email(other["task_id"])
        self.work()
        self.assertEqual(self.queue.get(other["task_id"])["email"]["attempts"],1)

    def test_request_failure_consumes_task_day(self):
        task=self.submit();self.work("model-failed")
        row=self.queue.get(task["task_id"])
        self.assertEqual(row["status"],"failed")
        self.assertEqual(row["model_requests"],1)
        self.assertEqual(row["consumed_day"],"2026-10-08")
        self.assertIsNone(row["reserved_day"])

    def test_requests_after_midnight_keep_first_consumption_date(self):
        task=self.submit();self.claim()
        budget=TaskBudget(self.queue,task["task_id"])
        attempt=budget.consume();budget.finish_request(attempt,"failed")
        self.time+=timedelta(days=1)
        for i in range(3): self.submit(f"fixture-full-next-day-{i}")
        attempt=budget.consume();budget.finish_request(attempt,"response_received")
        row=self.queue.get(task["task_id"])
        self.assertEqual(row["consumed_day"],"2026-10-08")
        self.assertEqual(row["model_requests"],2)
        self.assertEqual(row["requests"][1]["started_at"][:10],"2026-10-09")

    def test_crash_after_smtp_dispatch_recovers_report_and_keeps_unknown(self):
        task=self.submit()
        with mock.patch("learnradar.demo.DemoServices.sender",side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt): self.work()
        row=self.queue.get(task["task_id"])
        self.assertEqual(row["status"],"running")
        self.assertEqual(row["report"]["status"],"published")
        self.assertEqual(row["email"]["status"],"unknown")
        with mock.patch("learnradar.worker.execute_recommendation",side_effect=AssertionError("must not repeat")),mock.patch("learnradar.demo.DemoServices.sender",side_effect=AssertionError("must not resend")):
            self.work()
        row=self.queue.get(task["task_id"])
        self.assertEqual(row["status"],"completed")
        self.assertEqual(row["model_requests"],2)
        self.assertEqual(row["email"]["status"],"unknown")
        with self.assertRaises(TaskError): self.queue.retry_email(task["task_id"])

    def test_restart_during_notification_classifies_saved_runs_without_reports(self):
        cases=[("all-candidates-in-cooldown","completed",0),
               ("no-suitable-candidate","completed",1),
               ("insufficient-comparators","completed",1),
               ("failed","failed",0),("screening-failed","failed",1),
               ("model-failed","failed",1),("no-evidence","failed",1),
               ("running","interrupted",1),("unrecognized","interrupted",1)]
        for run_status,expected,requests in cases:
            with self.subTest(run_status=run_status):
                directory=self.directory/run_status
                queue=TaskQueue(directory,demo=True,clock=lambda:self.time)
                settings=demo_settings(load_settings(local_overrides=False),directory)
                task=queue.submit("agent-memory",f"fixture-recovery-{run_status}")
                def save_result(settings,request,hooks):
                    hooks.stage("screening")
                    if requests:
                        attempt=hooks.budget.consume()
                        hooks.budget.finish_request(attempt,"failed" if expected=="failed" else "response_received")
                    with queue.store() as store:
                        store.start_run(request.run_id,kind="fixture",started_at="fixture",snapshot={})
                        if run_status!="running":
                            store.finish_run(request.run_id,status=run_status,finished_at="fixture-finished",notes="fixture saved result")
                        return collect_outcome(store,settings,request.run_id)
                with mock.patch("learnradar.worker.execute_recommendation",side_effect=save_result),mock.patch("learnradar.demo.DemoServices.sender",side_effect=KeyboardInterrupt),contextlib.redirect_stdout(io.StringIO()):
                    with self.assertRaises(KeyboardInterrupt):
                        run_worker(settings,queue,once=True,demo_delay=0)
                before=queue.get(task["task_id"])
                self.assertEqual(before["status"],"running")
                self.assertEqual(before["phase"],"notification")
                self.assertIsNone(before["report"])
                self.assertEqual(before["email"]["status"],"unknown")
                restarted=TaskQueue(directory,demo=True,clock=lambda:self.time)
                with mock.patch("learnradar.worker.execute_recommendation") as analyze,mock.patch("learnradar.demo.DemoServices.sender") as sender,contextlib.redirect_stdout(io.StringIO()):
                    run_worker(settings,restarted,once=True,demo_delay=0)
                    run_worker(settings,restarted,once=True,demo_delay=0)
                analyze.assert_not_called()
                sender.assert_not_called()
                after=restarted.get(task["task_id"])
                self.assertEqual(after["status"],expected)
                self.assertEqual(after["model_requests"],requests)
                self.assertEqual(after["requests"],before["requests"])
                self.assertEqual(after["consumed_day"],before["consumed_day"])
                self.assertEqual(after["email"],before["email"])
                self.assertEqual(after["events"],before["events"])
                if run_status!="running":
                    self.assertEqual(after["run"],before["run"])
                with self.assertRaises(TaskError): restarted.retry_email(task["task_id"])

    def test_formal_redirect_configuration_is_rejected_before_requests(self):
        directory=self.directory/"formal-redirect"
        queue=TaskQueue(directory)
        settings=load_settings(local_overrides=False)
        settings=dataclasses.replace(settings,app={**settings.app,"data_dir":str(directory)},
            models={name:dataclasses.replace(value,follow_redirects=True) for name,value in settings.models.items()})
        task=queue.submit("agent-memory","fixture-formal-redirect")
        with mock.patch("learnradar.worker.execute_recommendation",side_effect=AssertionError("no search or model")),mock.patch("learnradar.worker._send_saved_or_new"),contextlib.redirect_stdout(io.StringIO()):
            run_worker(settings,queue,once=True)
        row=queue.get(task["task_id"])
        self.assertEqual(row["model_requests"],0)
        self.assertEqual(row["status"],"failed")
        self.assertIn("关闭重定向",row["detail"])

    def test_formal_missing_credentials_fails_without_search_or_fixture_fallback(self):
        directory=self.directory/"formal"
        queue=TaskQueue(directory)
        settings=load_settings(local_overrides=False)
        settings=dataclasses.replace(settings,app={**settings.app,"data_dir":str(directory)},
            models={name:dataclasses.replace(value,api_key_env="FIXTURE_ABSENT_KEY") for name,value in settings.models.items()})
        task=queue.submit("agent-memory","fixture-formal-no-secret")
        with mock.patch.dict(os.environ,{"FIXTURE_ABSENT_KEY":""}),mock.patch("learnradar.worker.execute_recommendation",side_effect=AssertionError("must fail before search")),mock.patch("learnradar.worker._send_saved_or_new"),contextlib.redirect_stdout(io.StringIO()):
            run_worker(settings,queue,once=True)
        row=queue.get(task["task_id"])
        self.assertEqual(row["status"],"failed")
        self.assertEqual(row["model_requests"],0)
        self.assertIsNone(row["reserved_day"])
        self.assertIn("缺少模型凭据",row["detail"])
