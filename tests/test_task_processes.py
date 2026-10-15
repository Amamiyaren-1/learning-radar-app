"""Actual HTTP/subprocess acceptance using loopback, isolated data, and fake models/SMTP."""
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from learnradar.tasks import TaskBudget, TaskQueue
from tests import auth_helpers

ROOT=Path(__file__).resolve().parents[1]


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs): return None


class ProcessTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory=Path(self.tmp.name)
        self.queue=TaskQueue(self.directory,demo=True)
        self.processes=[]
        self.streams=[]
        self.addCleanup(self.stop_all)
        self.opener=auth_helpers.opener()
        with socket.socket() as s:
            s.bind(("127.0.0.1",0)); self.port=s.getsockname()[1]

    def stop_all(self):
        for p in self.processes:
            if p.poll() is None:
                p.terminate();p.wait(timeout=10)
        for stream in self.streams: stream.close()

    def start(self,command,*flags):
        output=(self.directory/f"process-{len(self.processes)}.log").open("w",encoding="utf-8")
        self.streams.append(output)
        env={**os.environ,"PYTHONIOENCODING":"utf-8","NO_PROXY":"127.0.0.1,localhost"}
        p=subprocess.Popen([sys.executable,"-m","learnradar.task_cli",command,"--demo","--data-dir",str(self.directory),*flags],cwd=ROOT,env=env,stdout=output,stderr=subprocess.STDOUT)
        self.processes.append(p)
        return p

    def wait_until(self,condition,timeout=20):
        end=time.monotonic()+timeout
        while time.monotonic()<end:
            value=condition()
            if value: return value
            time.sleep(.05)
        self.fail("超时等待实际进程状态；日志："+"\n".join(path.read_text(encoding="utf-8") for path in self.directory.glob("process-*.log")))

    def get(self,path):
        return self.opener.open(f"http://127.0.0.1:{self.port}{path}",timeout=3).read().decode("utf-8")

    def start_web(self):
        p=self.start("web","--port",str(self.port))
        def healthy():
            try: return "ok" in self.get("/health")
            except (OSError,urllib.error.URLError): return False
        self.wait_until(healthy)
        auth_helpers.login(self.opener, f"http://127.0.0.1:{self.port}")
        return p

    def post(self,path,fields,headers=None):
        base=f"http://127.0.0.1:{self.port}"
        fields={**fields,"csrf_token":auth_helpers.csrf(self.opener,base)}
        request=urllib.request.Request(base+path,data=urllib.parse.urlencode(fields).encode(),headers={"Origin":base,"Sec-Fetch-Site":"same-origin",**(headers or {})})
        try:
            with self.opener.open(request,timeout=5) as r: return r.status,r.headers.get("Location"),r.read().decode()
        except urllib.error.HTTPError as r:
            return r.code,r.headers.get("Location"),r.read().decode()

    def test_http_duplicate_submit_page_close_worker_continues_and_reopen_feedback(self):
        web=self.start_web()
        home=self.get("/")
        self.assertIn("演示模式",home)
        key=re.search(r'name="submission_key" value="([^"]+)"',home)[1]
        fields={"topic":"agent-memory","submission_key":key}
        first=self.post("/tasks",fields); second=self.post("/tasks",fields)
        self.assertEqual(first[0],303)
        self.assertEqual(first[1],second[1])
        identifier=first[1].split("/")[-1]
        self.assertEqual(len(self.queue.list()),1)
        worker=self.start("worker","--once","--demo-delay","1")
        self.wait_until(lambda:self.queue.get(identifier)["phase"]=="analysis")
        web.terminate();web.wait(timeout=10)
        self.assertEqual(worker.wait(timeout=20),0)
        saved=self.queue.get(identifier)
        self.assertEqual(saved["status"],"completed")
        self.assertEqual(saved["model_requests"],2)
        self.assertEqual(saved["email"]["status"],"sent")
        self.start_web()
        self.assertIn(identifier,self.get("/"))
        detail=self.get(first[1])
        self.assertIn("处理完成",detail)
        self.assertIn("假 SMTP",detail)
        report_path="/report/"+saved["run_id"]
        self.assertIn("你的反馈",self.get(report_path))
        posted=self.post("/feedback/"+saved["run_id"],{"understood_what":"yes","note":"fixture browser feedback"})
        self.assertEqual(posted[0],303)
        with self.queue.store() as store:
            self.assertEqual(store.get_feedback(saved["run_id"])["note"],"fixture browser feedback")

    def test_port_zero_prints_actual_address_and_serves_health_and_home(self):
        self.start("web","--port","0")
        path=self.directory/"process-0.log"
        match=self.wait_until(lambda:re.search(r'http://127\.0\.0\.1:(\d+)',path.read_text(encoding="utf-8")))
        self.port=int(match[1])
        self.assertGreater(self.port,0)
        self.assertIn("ok",self.get("/health"))
        auth_helpers.login(self.opener, f"http://127.0.0.1:{self.port}")
        self.assertIn("演示模式",self.get("/"))

    def test_two_worker_processes_same_or_different_tasks_never_overlap(self):
        first=self.queue.submit("agent-memory","fixture-process-first")
        second=self.queue.submit("retrieval","fixture-process-second")
        a=self.start("worker","--once","--demo-delay","2")
        self.wait_until(lambda:self.queue.get(first["task_id"])["model_requests"]==1)
        b=self.start("worker","--once","--demo-delay","0")
        self.assertEqual(b.wait(timeout=10),0)
        self.assertEqual(self.queue.get(second["task_id"])["status"],"queued")
        self.assertEqual(self.queue.get(second["task_id"])["model_requests"],0)
        self.assertEqual(a.wait(timeout=20),0)
        c=self.start("worker","--once","--demo-delay","0")
        self.assertEqual(c.wait(timeout=20),0)
        rows=[self.queue.get(x["task_id"]) for x in (first,second)]
        self.assertEqual([r["model_requests"] for r in rows],[2,2])
        self.assertEqual([r["status"] for r in rows],["completed","completed"])
        self.assertLessEqual(rows[0]["requests"][-1]["finished_at"],rows[1]["requests"][0]["started_at"])
        d=self.start("worker","--once","--demo-delay","0")
        self.assertEqual(d.wait(timeout=10),0)
        self.assertEqual(self.queue.get(first["task_id"])["model_requests"],2)

    def test_killed_worker_paid_request_is_not_rerun_and_queued_task_survives(self):
        first=self.queue.submit("agent-memory","fixture-crash-first")
        second=self.queue.submit("retrieval","fixture-crash-pending")
        a=self.start("worker","--once","--demo-delay","15")
        self.wait_until(lambda:self.queue.get(first["task_id"])["model_requests"]==1)
        a.terminate();a.wait(timeout=10)
        b=self.start("worker","--once","--demo-delay","0")
        self.assertEqual(b.wait(timeout=20),0)
        interrupted=self.queue.get(first["task_id"])
        self.assertEqual(interrupted["status"],"interrupted")
        self.assertEqual(interrupted["model_requests"],1)
        self.assertEqual(interrupted["requests"][0]["status"],"unknown")
        self.assertIsNotNone(interrupted["consumed_day"])
        self.assertEqual(self.queue.get(second["task_id"])["status"],"completed")
        with self.queue.store() as store:
            self.assertEqual(store.conn.execute("SELECT COUNT(*) FROM task_requests WHERE task_id=?",(first["task_id"],)).fetchone()[0],1)

    def test_new_worker_recovers_saved_no_report_terminal_during_notification(self):
        for run_status,expected,requests in (("all-candidates-in-cooldown","completed",0),
                ("no-suitable-candidate","completed",1),("screening-failed","failed",1),
                ("running","interrupted",1)):
            with self.subTest(run_status=run_status):
                task=self.queue.submit("agent-memory",f"fixture-process-terminal-{run_status}")
                self.assertEqual(self.queue.claim("fixture-exited-worker")["task_id"],task["task_id"])
                if requests:
                    budget=TaskBudget(self.queue,task["task_id"])
                    attempt=budget.consume();budget.finish_request(attempt,"response_received")
                with self.queue.store() as store:
                    store.start_run(task["run_id"],kind="fixture",started_at="fixture",snapshot={})
                    if run_status!="running":
                        store.finish_run(task["run_id"],status=run_status,finished_at="fixture-finished",notes="fixture terminal")
                    store.create_email(task["run_id"],cycle_id="",run_id=task["run_id"],kind="status",recipient="fixture@example.invalid",subject="fixture",body="fixture notice",status="unknown",now="fixture")
                self.queue.stage(task["task_id"],"notification")
                artifact=self.directory/"runs"/task["run_id"]/"usage.json"
                artifact.parent.mkdir(parents=True,exist_ok=True)
                artifact.write_text(json.dumps({"fixture":True,"model_requests":requests}),encoding="utf-8")
                saved_bytes=artifact.read_bytes()
                before=self.queue.get(task["task_id"])
                worker=self.start("worker","--once","--demo-delay","0")
                self.assertEqual(worker.wait(timeout=20),0)
                after=self.queue.get(task["task_id"])
                self.assertEqual(after["status"],expected)
                self.assertEqual(after["requests"],before["requests"])
                self.assertEqual(after["model_requests"],requests)
                self.assertEqual(after["email"],before["email"])
                self.assertEqual(after["events"],before["events"])
                self.assertEqual(artifact.read_bytes(),saved_bytes)
                self.assertFalse(list((self.directory/"fixture-mail").glob("*.eml")))

    def test_real_http_validation_quota_cancel_and_cross_origin(self):
        self.start_web()
        key="fixture-cross-origin-request"
        status,_,_=self.post("/tasks",{"topic":"agent-memory","submission_key":key},headers={"Origin":"https://example.invalid"})
        self.assertEqual(status,403)
        self.assertEqual(self.queue.list(),[])
        locations=[]
        for i in range(3):
            code,location,_=self.post("/tasks",{"topic":"agent-memory","submission_key":f"fixture-http-key-{i:03}"})
            self.assertEqual(code,303);locations.append(location)
        code,_,body=self.post("/tasks",{"topic":"agent-memory","submission_key":"fixture-http-fourth"})
        self.assertEqual(code,429);self.assertIn("预留",body)
        self.assertIn('name="action" value="cancel"',self.get(locations[0]))
        self.assertEqual(self.post(locations[0]+"/cancel",{"action":"cancel"})[0],303)
        self.assertEqual(self.post("/tasks",{"topic":"agent-memory","submission_key":"fixture-http-fourth"})[0],303)
        self.assertEqual(self.post("/tasks",{"topic":"not-configured","submission_key":"fixture-invalid-topic"})[0],400)

    def test_http_weekly_cancel_button_hidden_and_direct_post_rejected(self):
        weekly=self.queue.submit_weekly("agent-memory","fixture-http-cancel-cycle")
        before=self.queue.get(weekly["task_id"])
        with self.queue.store() as store:
            cycle_before=store.get_cycle("fixture-http-cancel-cycle")
        self.start_web()  # Start only an isolated web server, without an analysis worker.
        path="/task/"+weekly["task_id"]
        self.assertIn("定时周报任务",self.get("/"))
        detail=self.get(path)
        self.assertIn("定时周报任务",detail)
        self.assertIn("暂不支持取消",detail)
        self.assertNotIn('name="action" value="cancel"',detail)
        status,_,body=self.post(path+"/cancel",{"action":"cancel"})
        self.assertEqual(status,400)
        self.assertIn("定时周报任务暂不支持取消",body)
        self.assertEqual(self.queue.get(weekly["task_id"]),before)
        with self.queue.store() as store:
            self.assertEqual(store.get_cycle("fixture-http-cancel-cycle"),cycle_before)
        self.assertEqual(self.queue.submit_weekly("agent-memory","fixture-http-cancel-cycle"),before)
        manual=self.queue.submit("agent-memory","fixture-http-manual-cancel")
        manual_path="/task/"+manual["task_id"]
        self.assertIn('name="action" value="cancel"',self.get(manual_path))
        self.assertEqual(self.post(manual_path+"/cancel",{"action":"cancel"})[0],303)
        self.assertEqual(self.queue.get(manual["task_id"])["status"],"cancelled")
        self.assertIsNone(self.queue.get(manual["task_id"])["reserved_day"])

    def test_http_displays_rejected_failed_and_unknown_from_saved_results(self):
        self.start_web()
        for scenario in ("rejected","mail-failed","mail-unknown"):
            code,path,_=self.post("/tasks",{"topic":"agent-memory","submission_key":f"fixture-http-state-{scenario}"})
            self.assertEqual(code,303)
            p=self.start("worker","--once","--demo-delay","0","--demo-scenario",scenario)
            self.assertEqual(p.wait(timeout=20),0)
            identifier=path.split("/")[-1]
            row=self.queue.get(identifier)
            detail=self.get(path)
            self.assertIn("处理完成",detail)
            if scenario=="rejected":
                self.assertEqual(row["report"]["status"],"rejected")
                self.assertIn("报告未发布/未通过检查",detail)
                self.assertIn("未发布",self.get("/report/"+row["run_id"]))
                self.assertEqual(row["email"]["kind"],"failure")
            elif scenario=="mail-failed":
                self.assertIn("failed",detail)
                self.assertIn('name="action" value="retry-email"',detail)
                self.assertEqual(self.post(path+"/retry-email",{"action":"retry-email"})[0],303)
                retry=self.start("worker","--once","--demo-delay","0")
                self.assertEqual(retry.wait(timeout=20),0)
                saved=self.queue.get(identifier)
                self.assertEqual(saved["model_requests"],row["model_requests"])
                self.assertEqual(saved["email"]["status"],"sent")
            else:
                self.assertIn("unknown",detail)
                self.assertIn("请人工核对",detail)
                self.assertNotIn('name="action" value="retry-email"',detail)
                self.assertEqual(self.post(path+"/retry-email",{"action":"retry-email"})[0],400)
