"""Independent web processes and both entry points, without workers or analysis."""
from pathlib import Path
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace

from learnradar.tasks import TaskQueue
from learnradar.auth_store import AuthStore
from tests.test_auth import Client

ROOT=Path(__file__).resolve().parents[1]


class AuthProcessTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory=Path(self.tmp.name)
        self.queue=TaskQueue(self.directory,demo=True)
        self.processes=[]
        self.streams=[]
        self.addCleanup(self.stop)

    def stop(self):
        for process in self.processes:
            if process.poll() is None:
                process.terminate();process.wait(timeout=10)
        for stream in self.streams:stream.close()

    def start(self,legacy=False):
        path=self.directory/f'web-{len(self.processes)}.log'
        stream=path.open('w',encoding='utf-8');self.streams.append(stream)
        command=[sys.executable,'-m','learnradar.cli' if legacy else 'learnradar.task_cli','serve' if legacy else 'web','--demo','--data-dir',str(self.directory),'--port','0']
        process=subprocess.Popen(command,cwd=ROOT,stdout=stream,stderr=subprocess.STDOUT,env={**os.environ,'PYTHONIOENCODING':'utf-8'})
        self.processes.append(process)
        deadline=time.monotonic()+15
        while time.monotonic()<deadline:
            output=path.read_text(encoding='utf-8')
            match=re.search(r'http://127\.0\.0\.1:(\d+)',output)
            if match:
                return process,Client(SimpleNamespace(server_port=int(match[1])))
            if process.poll() is not None:self.fail('Web process did not start; exit='+str(process.returncode))
            time.sleep(.05)
        self.fail('Web startup timeout')

    def test_both_entry_points_share_protection_and_saved_sessions_after_restart(self):
        first,client=self.start()
        self.assertEqual(client.request('/history')[0],303)
        self.assertEqual(client.login()[0],303)
        cookie=client.cookie
        self.assertIn('历史',client.request('/history')[2])
        first.terminate();first.wait(timeout=10)
        second,legacy=self.start(legacy=True)
        self.assertEqual(legacy.request('/history')[0],303)
        legacy.cookie=cookie
        self.assertEqual(legacy.request('/history')[0],200)
        self.assertEqual(legacy.request('/feedback/fixture',{'note':'must not save'})[0],403)
        AuthStore(self.queue.path).revoke_all()
        self.assertEqual(legacy.request('/history')[0],303)
        self.assertEqual(len(self.queue.list()),0)
        with self.queue.store() as store:
            for table in ('feedback','usage_log','emails'):
                self.assertEqual(store.conn.execute('SELECT COUNT(*) FROM '+table).fetchone()[0],0)

    def test_formal_entry_points_refuse_missing_account_without_private_page(self):
        formal=self.directory/'formal'
        TaskQueue(formal)
        for module,command in (('learnradar.task_cli','web'),('learnradar.cli','serve')):
            result=subprocess.run([sys.executable,'-m',module,command,'--data-dir',str(formal),'--web-mode','loopback','--port','0'],cwd=ROOT,env={**os.environ,'PYTHONIOENCODING':'utf-8'},capture_output=True,encoding='utf-8',timeout=15)
            self.assertNotEqual(result.returncode,0)
            self.assertIn('账号未初始化',result.stdout)
            self.assertNotIn('http://',result.stdout)
