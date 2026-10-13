"""Daily launcher isolation, no real external adapters or formal data."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import urllib.request
from learnradar.auth import password_hash
from learnradar.auth_store import AuthStore
from learnradar.runtime_launcher import check_start
from learnradar.tasks import TaskQueue,TaskError
from learnradar.demo_launcher import owned_alive
ROOT=Path(__file__).resolve().parents[1]

class PendingGuardTests(unittest.TestCase):
    def test_old_queue_and_notification_block_but_explicit_task_is_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            q=TaskQueue(Path(directory),demo=False)
            task=q.submit('fixture-topic','fixture-pending-key-0001',notify_mode='none')
            with self.assertRaises(TaskError):check_start(q)
            check_start(q,[task['task_id']])
            with q.store() as store:
                store.conn.execute('UPDATE tasks SET notify_pending=1 WHERE task_id=?',(task['task_id'],));store.conn.commit()
            with self.assertRaises(TaskError):check_start(q,[task['task_id']])

@unittest.skipUnless(os.name=='nt','Windows ownership lifecycle')
class DailyLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.config=Path(self.temp.name);(self.config/'config').mkdir()
        for name in ('settings.toml','topics.toml'):
            shutil.copyfile(ROOT/'config'/name,self.config/'config'/name)
        self.q=TaskQueue(self.config/'data',demo=False)
        AuthStore(self.q.path).set_account('fixture-runtime-account',password_hash('fixture-runtime-password-2026'),fixture=False)
        self.addCleanup(lambda:self.launch('stop'))
    def launch(self,action,extra=()):
        env={k:v for k,v in os.environ.items() if not k.startswith(('LEARNRADAR_','GITHUB_'))}
        env.update(LEARNRADAR_PYTHON=sys.executable,PYTHONIOENCODING='utf-8')
        return subprocess.run(['powershell','-NoProfile','-ExecutionPolicy','Bypass','-File',str(ROOT/'scripts/start.ps1'),'-Action',action,'-ConfigRoot',str(self.config),*extra],cwd=ROOT,env=env,capture_output=True,encoding='utf-8',timeout=35)
    def state(self):return json.loads((self.config/'data/runtime-launcher/state.json').read_text())
    def test_idle_start_survives_web_client_close_and_duplicate_then_owned_stop(self):
        first=self.launch('start');self.assertEqual(first.returncode,0,first.stdout+first.stderr)
        state=self.state();self.assertEqual({x['role'] for x in state['processes']},{'web','worker'})
        with urllib.request.urlopen(state['url']+'/health') as response:self.assertEqual(response.status,200)
        self.assertTrue(all(owned_alive(x) for x in state['processes']))
        second=self.launch('start');self.assertEqual(second.returncode,0,second.stdout)
        self.assertEqual(self.state()['processes'],state['processes'])
        self.assertEqual(self.q.list(),[])
        status=self.launch('status');self.assertEqual(status.returncode,0)
        stopped=self.launch('stop');self.assertEqual(stopped.returncode,0,stopped.stdout)
        self.assertTrue(self.q.path.is_file());self.assertEqual(self.q.list(),[])
        self.assertFalse(any(owned_alive(x) for x in state['processes']))
    def test_unapproved_work_refused_without_launching_or_claiming(self):
        task=self.q.submit('fixture-topic','fixture-runtime-blocked',notify_mode='none')
        result=self.launch('start');self.assertEqual(result.returncode,1)
        self.assertEqual(self.q.get(task['task_id'])['status'],'queued')
        self.assertEqual(self.q.get(task['task_id'])['model_requests'],0)
        self.assertFalse((self.config/'data/runtime-launcher/state.json').exists())

if __name__=='__main__':unittest.main()
