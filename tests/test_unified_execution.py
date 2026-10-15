"""Unified entry tests with isolated data, fake models/SMTP, competing processes, and persisted results."""
import contextlib
import dataclasses
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from learnradar import cli
from learnradar.config import load_settings
from learnradar.demo import DemoServices, demo_settings
from learnradar.tasks import TaskBudget, TaskError, TaskQueue, QuotaWait, WorkerLock
from learnradar.worker import run_worker, task_exit_code
from tests import test_task_processes as process_helpers

ROOT=Path(__file__).resolve().parents[1]


class UnifiedQueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory=Path(self.tmp.name)
        self.time=datetime.fromisoformat("2026-10-08T23:59:00+08:00")
        self.queue=TaskQueue(self.directory,demo=True,clock=lambda:self.time)
        self.settings=demo_settings(load_settings(local_overrides=False),self.directory)

    def execute(self,*argv):
        args=cli.build_parser().parse_args([*argv,"--demo","--demo-delay","0"])
        with contextlib.redirect_stdout(io.StringIO()):
            return args.func(self.settings,args)

    def test_cli_all_options_and_saved_input_idempotency(self):
        source="fixture-source-shared01"
        result="fixture-result-shared01"
        self.assertEqual(self.execute("run","--topic","agent-memory","--run-id",source,"--screening-only"),0)
        names=DemoServices(self.settings,result).names
        flags=["run","--topic","agent-memory","--run-id",result,"--candidate",names[0],
               "--compare",names[1],"--compare",names[2],"--model","demo-analysis", "--per-page","7","--limit","2",
               "--candidates-from",source,"--screening-from",source]
        self.assertEqual(self.execute(*flags),0)
        task=next(t for t in self.queue.list() if t["run_id"]==result)
        row=self.queue.get(task["task_id"])
        self.assertEqual(row["input"],{"candidate":names[0],"compare":names[1:],"model":"demo-analysis",
            "per_page":7,"limit":2,"candidates_from":source,"screening_only":False,"screening_from":source,
        "_fixture_delay":0.0,"_fixture_scenario":"success","discovery_mode":"focused"})
        self.assertEqual(row["model_requests"],1)
        self.assertEqual(row["report"]["status"],"published")
        self.assertIsNone(row["email"])
        saved=(self.directory/"runs"/result/"report.json").read_bytes()
        with mock.patch("learnradar.worker.execute_recommendation",side_effect=AssertionError("repeat must not analyze")):
            self.assertEqual(self.execute(*flags),0)
        self.assertEqual((self.directory/"runs"/result/"report.json").read_bytes(),saved)
        self.assertEqual(len(self.queue.list()),2)
        self.assertNotEqual(self.execute(*flags,"--limit","3"),0)
        self.assertEqual(self.queue.get(task["task_id"])["model_requests"],1)

    def test_manual_web_run_replay_budget_shared_weekly_independent_across_day(self):
        kinds=("manual_recommendation","manual_recommendation","replay_analysis")
        for i,kind in enumerate(kinds):
            task=self.queue.submit("agent-memory",f"fixture-entry-budget-{i}",kind=kind,
                                   notify_mode="manual" if i==0 else "none")
            self.queue.claim("fixture")
            attempt=TaskBudget(self.queue,task["task_id"]).consume()
            self.queue.end_request(task["task_id"],attempt,"unknown" if i==2 else "failed")
            self.queue.finish(task["task_id"],"interrupted" if i==2 else "failed")
        with self.assertRaises(QuotaWait): self.queue.submit("replay","fixture-entry-fourth",kind="replay_analysis")
        weekly=self.queue.submit_weekly("agent-memory","fixture-W41")
        self.queue.claim("fixture")
        for _ in range(4):
            budget=TaskBudget(TaskQueue(self.directory,demo=True,clock=lambda:self.time),weekly["task_id"])
            attempt=budget.consume();budget.finish_request(attempt,"failed")
        from learnradar.cli import ModelBudgetExceeded
        with self.assertRaises(ModelBudgetExceeded): budget.consume()
        self.queue.finish(weekly["task_id"],"failed")
        self.assertEqual(self.queue.get(weekly["task_id"])["budget_scope"],"weekly")
        self.time+=timedelta(days=1)
        zero=self.queue.submit("agent-memory","fixture-entry-zero")
        self.queue.claim("fixture");self.queue.finish(zero["task_id"],"failed")
        for i in range(3): self.queue.submit("agent-memory",f"fixture-new-day-entry-{i}")
        with self.assertRaises(QuotaWait): self.queue.submit("agent-memory","fixture-new-day-fourth")

    def test_concurrent_cycle_binding_and_redo_preserves_old_task(self):
        def submit(_):
            return TaskQueue(self.directory,demo=True).submit_weekly("agent-memory","fixture-cycle")
        with ThreadPoolExecutor(max_workers=6) as pool:
            rows=list(pool.map(submit,range(8)))
        self.assertEqual(len({r["task_id"] for r in rows}),1)
        task=rows[0]
        with self.assertRaises(TaskError): self.queue.submit_weekly("agent-memory","fixture-cycle",force_redo=True)
        self.queue.claim("fixture");attempt=TaskBudget(self.queue,task["task_id"]).consume()
        lock=WorkerLock(self.directory);self.assertTrue(lock.acquire())
        try: self.queue.recover(lock)
        finally: lock.release()
        interrupted=self.queue.get(task["task_id"])
        self.assertEqual(interrupted["requests"][0]["status"],"unknown")
        self.assertEqual(interrupted["model_requests"],1)
        redo=self.queue.submit_weekly("agent-memory","fixture-cycle",force_redo=True)
        self.assertNotEqual(redo["task_id"],task["task_id"])
        self.assertNotEqual(redo["run_id"],task["run_id"])
        self.assertEqual(self.queue.get(task["task_id"])["model_requests"],1)
        with self.queue.store() as store:
            self.assertEqual(store.get_cycle("fixture-cycle")["task_id"],redo["task_id"])

    def test_weekly_restart_in_notification_preserves_unknown_and_requests(self):
        task=self.queue.submit_weekly("agent-memory","fixture-notification-cycle")
        with mock.patch("learnradar.demo.DemoServices.sender",side_effect=KeyboardInterrupt),contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(KeyboardInterrupt): run_worker(self.settings,self.queue,once=True,demo_delay=0)
        before=self.queue.get(task["task_id"])
        self.assertEqual(before["status"],"running")
        self.assertEqual(before["email"]["status"],"unknown")
        with mock.patch("learnradar.worker.execute_recommendation",side_effect=AssertionError("no reanalysis")),mock.patch("learnradar.demo.DemoServices.sender",side_effect=AssertionError("no resend")),contextlib.redirect_stdout(io.StringIO()):
            run_worker(self.settings,self.queue,once=True,demo_delay=0)
        after=self.queue.get(task["task_id"])
        self.assertEqual(after["status"],"completed")
        self.assertEqual(after["email"],before["email"])
        self.assertEqual(after["requests"],before["requests"])
        self.assertEqual(after["model_requests"],2)
        self.assertNotEqual(task_exit_code(after),0)
        with self.assertRaises(TaskError): self.queue.retry_email(task["task_id"])

    def test_unexpected_manual_failure_is_saved_before_notification_finishes(self):
        task=self.queue.submit("agent-memory","fixture-notify-saved-failure")
        original=DemoServices.sender
        observed=[]
        def sender(services,config,**kwargs):
            row=self.queue.get(task["task_id"])
            observed.append((row["status"],row["run"]["status"]))
            return original(services,config,**kwargs)
        with mock.patch("learnradar.worker.execute_recommendation",side_effect=RuntimeError("fixture pipeline failed")),mock.patch.object(DemoServices,"sender",sender),contextlib.redirect_stdout(io.StringIO()):
            run_worker(self.settings,self.queue,once=True,demo_delay=0)
        self.assertEqual(observed,[("running","failed")])
        row=self.queue.get(task["task_id"])
        self.assertEqual(row["status"],"failed")
        self.assertEqual(row["email"]["kind"],"failure")
        self.assertEqual(row["email"]["status"],"sent")
        self.assertEqual(row["model_requests"],0)

    def test_actual_repair_requests_share_persistent_four_call_limit(self):
        task=self.queue.submit("agent-memory","fixture-four-real-boundaries")
        original=DemoServices.adapter
        observed=[]
        role_calls={}
        def adapter(services,candidate,**kwargs):
            inner=original(services,candidate,**kwargs)
            class RepairFixture:
                def complete(self,**request):
                    saved=self_queue.get(task["task_id"])
                    observed.append((saved["model_requests"],saved["requests"][-1]["status"]))
                    role_calls[candidate.name]=role_calls.get(candidate.name,0)+1
                    response=inner.complete(**request)
                    return dataclasses.replace(response,text="fixture invalid JSON") if role_calls[candidate.name]==1 else response
            return RepairFixture()
        self_queue=self.queue
        with mock.patch.object(DemoServices,"adapter",adapter),contextlib.redirect_stdout(io.StringIO()):
            run_worker(self.settings,self.queue,once=True,demo_delay=0)
        row=self.queue.get(task["task_id"])
        self.assertEqual(row["status"],"completed")
        self.assertEqual(row["model_requests"],4)
        self.assertEqual(observed,[(1,"pending"),(2,"pending"),(3,"pending"),(4,"pending")])
        with mock.patch("learnradar.worker.execute_recommendation",side_effect=AssertionError("restart must not analyze")),contextlib.redirect_stdout(io.StringIO()):
            run_worker(self.settings,TaskQueue(self.directory,demo=True),once=True,demo_delay=0)
        self.assertEqual(TaskBudget(TaskQueue(self.directory,demo=True),task["task_id"]).remaining,0)

    def test_weekly_wait_timeout_does_not_mark_other_worker_stopped(self):
        from learnradar import cloud
        from learnradar.cycle import TaskTimeout
        task=self.queue.submit_weekly("agent-memory","fixture-live-timeout")
        self.queue.claim("fixture-live-worker")
        TaskBudget(self.queue,task["task_id"]).consume()
        lock=WorkerLock(self.directory);self.assertTrue(lock.acquire())
        try:
            with mock.patch("learnradar.worker.wait_for_task",side_effect=TaskTimeout("fixture wait timeout")),contextlib.redirect_stdout(io.StringIO()):
                code=self.execute("weekly","--cycle","fixture-live-timeout")
            self.assertNotEqual(code,0)
            self.assertEqual(self.queue.get(task["task_id"])["status"],"running")
            self.assertEqual(self.queue.get(task["task_id"])["requests"][0]["status"],"pending")
            with self.queue.store() as store:
                self.assertEqual(store.get_cycle("fixture-live-timeout")["status"],"running")
        finally: lock.release()

    def test_previous_task_schema_upgrade_preserves_bound_task_and_history(self):
        task=self.queue.submit("agent-memory","fixture-old-schema-task")
        self.queue.claim("fixture");attempt=TaskBudget(self.queue,task["task_id"]).consume()
        self.queue.end_request(task["task_id"],attempt,"unknown")
        with self.queue.store() as store:
            store.start_run(task["run_id"],kind="fixture",started_at="fixture",snapshot={})
            store.finish_run(task["run_id"],status="failed",finished_at="fixture")
            store.save_feedback(task["run_id"],"fixture/repo",{},"fixture preserved",now="fixture")
            store.save_cycle("legacy-cycle",status="completed",run_id="legacy-run",started_at="fixture")
            store.create_email("fixture-email",cycle_id="legacy-cycle",run_id="legacy-run",kind="status",recipient="fixture@example.invalid",subject="fixture",body="fixture",status="unknown",now="fixture")
            for column in ("payload","budget_scope","notify_mode","cycle_id","exit_code"):
                store.conn.execute(f"ALTER TABLE tasks DROP COLUMN {column}")
            store.conn.execute("ALTER TABLE cycles DROP COLUMN task_id")
            store.conn.commit()
        upgraded=TaskQueue(self.directory,demo=True)
        lock=WorkerLock(self.directory);self.assertTrue(lock.acquire())
        try: upgraded.recover(lock)
        finally: lock.release()
        row=upgraded.get(task["task_id"])
        self.assertEqual(row["status"],"failed")
        self.assertEqual(row["model_requests"],1)
        self.assertEqual(row["input"],{})
        self.assertEqual(row["budget_scope"],"manual")
        with upgraded.store() as store:
            self.assertEqual(store.get_feedback(task["run_id"])["note"],"fixture preserved")
            self.assertEqual(store.get_cycle("legacy-cycle")["status"],"completed")
            self.assertIsNone(store.get_cycle("legacy-cycle")["task_id"])
            self.assertEqual(store.get_email("fixture-email")["status"],"unknown")


class UnifiedProcessTests(unittest.TestCase):
    setUp=process_helpers.ProcessTests.setUp
    stop_all=process_helpers.ProcessTests.stop_all
    start=process_helpers.ProcessTests.start
    wait_until=process_helpers.ProcessTests.wait_until
    get=process_helpers.ProcessTests.get
    post=process_helpers.ProcessTests.post
    start_web=process_helpers.ProcessTests.start_web

    def start_cli(self,*argv):
        output=(self.directory/f"process-{len(self.processes)}.log").open("w",encoding="utf-8")
        self.streams.append(output)
        p=subprocess.Popen([sys.executable,"-m","learnradar.cli",*argv,"--demo","--data-dir",str(self.directory)],
            cwd=ROOT,env={**os.environ,"PYTHONIOENCODING":"utf-8"},stdout=output,stderr=subprocess.STDOUT)
        self.processes.append(p)
        return p

    def test_http_worker_cli_weekly_and_replay_compete_under_one_lock(self):
        self.start_web()
        code,path,_=self.post("/tasks",{"topic":"agent-memory","submission_key":"fixture-global-http-first"})
        self.assertEqual(code,303)
        web_id=path.split("/")[-1]
        web_task=self.queue.get(web_id)
        worker=self.start("worker","--once","--demo-delay","2")
        self.wait_until(lambda:self.queue.get(web_id)["phase"]=="analysis")
        run=self.start_cli("run","--run-id","fixture-global-cli","--topic","retrieval","--screening-only","--demo-delay",".5")
        self.wait_until(lambda:len(self.queue.list())==2)
        weekly=self.start_cli("weekly","--cycle","fixture-global-week","--demo-delay",".5")
        repeat=self.start_cli("weekly","--cycle","fixture-global-week","--demo-delay",".5")
        self.wait_until(lambda:len(self.queue.list())==3)
        names=DemoServices(demo_settings(load_settings(local_overrides=False),self.directory),web_task["run_id"]).names
        replay=self.start_cli("replay-analysis","--run",web_task["run_id"],"--run-id","fixture-global-replay",
            "--target",names[0],"--compare",names[1],"--compare",names[2],"--model","demo-analysis","--demo-delay",".5")
        self.wait_until(lambda:len(self.queue.list())==4)
        self.assertIsNone(run.poll(),"CLI 不能刚入队就成功退出")
        self.assertIsNone(weekly.poll(),"oneshot 不能刚入队就成功退出")
        deadline=time.monotonic()+30
        while any(p.poll() is None for p in (worker,run,weekly,repeat,replay)) and time.monotonic()<deadline:
            self.assertLessEqual(sum(t["status"]=="running" for t in self.queue.list()),1)
            with self.queue.store() as store:
                self.assertLessEqual(store.conn.execute("SELECT COUNT(*) FROM task_requests WHERE status='pending'").fetchone()[0],1)
            time.sleep(.03)
        for p in (worker,run,weekly,repeat,replay): self.assertEqual(p.wait(timeout=5),0)
        rows=[self.queue.get(t["task_id"]) for t in self.queue.list()]
        self.assertEqual(len(rows),4)
        self.assertTrue(all(r["status"]=="completed" for r in rows))
        self.assertEqual(sorted(r["model_requests"] for r in rows),[1,1,2,2])
        weekly_row=next(r for r in rows if r["budget_scope"]=="weekly")
        self.assertEqual(weekly_row["email"]["cycle_id"],"fixture-global-week")
        self.assertEqual(weekly_row["email"]["attempts"],1)
        self.assertEqual(len(list((self.directory/"fixture-mail").glob("*.eml"))),2)
        fourth=self.start_cli("run","--screening-only","--demo-delay","0")
        self.assertNotEqual(fourth.wait(timeout=10),0)
        self.assertEqual(len(self.queue.list()),4)

    def test_weekly_email_failed_retry_and_unknown_never_repeat_analysis(self):
        for scenario in ("mail-failed","mail-unknown"):
            cycle="fixture-cycle-"+scenario
            p=self.start_cli("weekly","--cycle",cycle,"--demo-scenario",scenario,"--demo-delay","0")
            self.assertNotEqual(p.wait(timeout=20),0)
            task=next(t for t in self.queue.list() if t["cycle_id"]==cycle)
            before=self.queue.get(task["task_id"])
            repeated=self.start_cli("weekly","--cycle",cycle,"--demo-delay","0")
            self.assertNotEqual(repeated.wait(timeout=10),0)
            self.assertEqual(self.queue.get(task["task_id"])["requests"],before["requests"])
            if scenario=="mail-unknown":
                with self.assertRaises(TaskError): self.queue.retry_email(task["task_id"])
                self.assertEqual(self.queue.get(task["task_id"])["email"],before["email"])
            else:
                self.queue.retry_email(task["task_id"])
                retry=self.start("worker","--once","--demo-delay","0")
                self.assertEqual(retry.wait(timeout=10),0)
                after=self.queue.get(task["task_id"])
                self.assertEqual(after["model_requests"],2)
                self.assertEqual(after["email"]["status"],"sent")
                self.assertEqual(after["email"]["cycle_id"],cycle)
                repeated=self.start_cli("weekly","--cycle",cycle,"--demo-delay","0")
                self.assertEqual(repeated.wait(timeout=10),0)

    def test_interrupted_weekly_counter_survives_cli_restart(self):
        p=self.start_cli("weekly","--cycle","fixture-killed-cycle","--demo-delay","15")
        task=self.wait_until(lambda:next(iter(self.queue.list()),None))
        self.wait_until(lambda:self.queue.get(task["task_id"])["model_requests"]==1)
        p.terminate();p.wait(timeout=10)
        repeated=self.start_cli("weekly","--cycle","fixture-killed-cycle","--demo-delay","0")
        self.assertNotEqual(repeated.wait(timeout=10),0)
        after=self.queue.get(task["task_id"])
        self.assertEqual(after["status"],"interrupted")
        self.assertEqual(after["model_requests"],1)
        self.assertEqual(after["requests"][0]["status"],"unknown")
        self.assertIsNone(after["email"])
