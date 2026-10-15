"""Exercise actual PowerShell start, deduplication, and stop with isolated loopback fixtures."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from learnradar.demo_launcher import owned_alive,stop_owned
from learnradar.tasks import TaskQueue,WorkerLock
from tests import auth_helpers

ROOT=Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.name=="nt","PowerShell 启动器只支持 Windows")
class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.directory=Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(lambda:self.launch("stop"))
        self.queue=TaskQueue(self.directory,demo=True)

    def launch(self,action="start"):
        return subprocess.run(["powershell","-NoProfile","-ExecutionPolicy","Bypass","-File",str(ROOT/"scripts"/"demo.ps1"),"-Action",action,"-DataDir",str(self.directory)],cwd=ROOT,
            env={**os.environ,"LEARNRADAR_PYTHON":sys.executable,"PYTHONIOENCODING":"utf-8"},capture_output=True,encoding="utf-8",timeout=30)

    def state(self):
        return json.loads((self.directory/"launcher"/"state.json").read_text(encoding="utf-8"))

    def get(self,path):
        if not hasattr(self,"browser"):
            self.browser=auth_helpers.opener(redirects=True)
            auth_helpers.login(self.browser,self.state()["url"])
        opener=self.browser
        return opener.open(self.state()["url"]+path,timeout=5).read().decode("utf-8")

    def wait(self,check):
        end=time.monotonic()+20
        while time.monotonic()<end:
            value=check()
            if value:return value
            time.sleep(.05)
        self.fail("等待一键演示状态超时")

    def test_one_command_duplicate_submit_worker_survives_reopen_and_owned_stop(self):
        first=self.launch();self.assertEqual(first.returncode,0,first.stdout+first.stderr)
        state=self.state();self.assertEqual(len(state["processes"]),2)
        self.assertIn(state["url"],first.stdout);self.assertIn("ok",self.get("/health"))
        self.assertTrue(all(owned_alive(p) for p in state["processes"]))
        repeated=self.launch();self.assertEqual(repeated.returncode,0,repeated.stdout+repeated.stderr)
        self.assertEqual(self.state(),state)
        home=self.get("/");key=re.search(r'name="submission_key" value="([^"]+)"',home)[1]
        from urllib.parse import urlencode
        opener=self.browser
        request=urllib.request.Request(state["url"]+"/tasks",data=urlencode({"topic":"agent-memory","submission_key":key,"csrf_token":auth_helpers.csrf(opener,state["url"])}).encode(),headers={"Origin":state["url"]})
        with opener.open(request,timeout=5) as response: identifier=response.url.rsplit("/",1)[1]
        self.wait(lambda:self.queue.get(identifier)["phase"]=="analysis")
        # The launcher has returned and no web requests occur; the independent worker continues.
        self.wait(lambda:self.queue.get(identifier)["status"]=="completed")
        task=self.queue.get(identifier)
        self.assertEqual(task["model_requests"],2);self.assertEqual(task["email"]["status"],"sent")
        self.assertIn(identifier,self.get("/"))
        self.assertIn("处理完成",self.get("/task/"+identifier))
        project=json.loads(task["report"]["payload"])["target"]["repo"]
        self.assertIn("已发布",self.get("/history?"+urlencode({"topic":"agent-memory","project":project})))
        unknown=subprocess.Popen([sys.executable,"-c","import time; time.sleep(30)"],creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            self.assertFalse(stop_owned({"pid":unknown.pid,"birth":"0"}))
            result=self.launch("stop");self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertIsNone(unknown.poll())
            self.assertTrue(all(not owned_alive(p) for p in state["processes"]))
            self.assertEqual(self.queue.get(identifier)["model_requests"],2)
        finally: unknown.terminate();unknown.wait(timeout=10)

    def test_web_restart_preserves_existing_owned_worker(self):
        self.assertEqual(self.launch().returncode,0)
        before=self.state()
        web=next(p for p in before["processes"] if p["role"]=="web")
        worker=next(p for p in before["processes"] if p["role"]=="worker")
        self.assertTrue(stop_owned(web))
        restarted=self.launch();self.assertEqual(restarted.returncode,0,restarted.stdout+restarted.stderr)
        self.assertEqual(next(p for p in self.state()["processes"] if p["role"]=="worker"),worker)
        self.assertIn("ok",self.get("/health"))

    def test_external_worker_lock_failure_logs_and_preserves_tasks(self):
        task=self.queue.submit("agent-memory","fixture-launcher-lock-task")
        before=self.queue.get(task["task_id"])
        lock=WorkerLock(self.directory);self.assertTrue(lock.acquire())
        try:
            result=self.launch()
            self.assertNotEqual(result.returncode,0)
            self.assertIn("日志",result.stdout)
            self.assertEqual(self.queue.get(task["task_id"]),before)
            self.assertTrue(all(not owned_alive(p) for p in self.state()["processes"]))
        finally:lock.release()

    def test_refuses_production_database_without_processes_or_mutation(self):
        directory=self.directory/"formal"
        queue=TaskQueue(directory)
        task=queue.submit("agent-memory","fixture-formal-launcher-task")
        before=queue.get(task["task_id"])
        result=subprocess.run([sys.executable,"-m","learnradar.demo_launcher","start","--data-dir",str(directory)],cwd=ROOT,capture_output=True,encoding="utf-8",timeout=20)
        self.assertNotEqual(result.returncode,0)
        self.assertIn("演示与正式数据目录不能混用",result.stdout)
        self.assertEqual(queue.get(task["task_id"]),before)
        self.assertFalse((directory/"launcher"/"state.json").exists())

    def test_corrupt_launcher_state_fails_clearly_without_touching_tasks(self):
        task=self.queue.submit("agent-memory","fixture-corrupt-launcher-task")
        before=self.queue.get(task["task_id"])
        folder=self.directory/"launcher";folder.mkdir()
        path=folder/"state.json"
        raw=json.dumps({"mode":"demo","directory":str(self.directory),"processes":[None]})
        path.write_text(raw,encoding="utf-8")
        result=self.launch()
        self.assertNotEqual(result.returncode,0)
        self.assertIn("记录损坏",result.stdout)
        self.assertNotIn("Traceback",result.stdout+result.stderr)
        self.assertEqual(self.queue.get(task["task_id"]),before)
        self.assertEqual(path.read_text(encoding="utf-8"),raw)
