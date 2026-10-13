"""B3: actual built assets + HTTP + an independent, externally blocked worker.

All accounts, artifacts and budgets are temporary fixtures. A demo request or
saved FakeSMTP .eml is never evidence of a real model/search/mail call.
"""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from urllib.parse import urlencode

from learnradar.config import load_settings
from learnradar.tasks import TaskQueue
from learnradar.web import serve
from scripts.ui_fixture import seed
from tests.auth_helpers import csrf_from
from tests.test_auth import Client
from tests.test_ui_backend import bootstrap

ROOT = Path(__file__).resolve().parents[1]


class IntegratedHttpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name) / 'fixture'
        self.queue = TaskQueue(self.directory, demo=True)
        self.settings = load_settings(local_overrides=False)
        self.server = serve(self.queue.path, queue=self.queue, settings=self.settings, port=0)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close); self.addCleanup(self.server.shutdown)
        self.client = Client(self.server)
        self.assertEqual(self.client.login()[0], 303)

    def state(self):
        with self.queue.store() as store:
            return {t: [tuple(r) for r in store.conn.execute('SELECT * FROM ' + t)]
                    for t in ('tasks', 'task_requests', 'runs', 'reports', 'emails', 'feedback', 'cycles')}

    def submit(self):
        home = self.client.request('/')[2]
        import re
        key = re.search(r'name="submission_key" value="([^"]+)"', home).group(1)
        fields = {'submission_key': key, 'discovery_mode': 'broad', 'csrf_token': csrf_from(home)}
        status, headers, _ = self.client.request('/tasks', fields)
        self.assertEqual(status, 303)
        return headers['Location'].split('/')[-1], fields

    def worker(self, scenario='success', delay=0):
        code = """
import sys
from pathlib import Path
from scripts.ui_fixture import audit
sys.addaudithook(audit)
from learnradar.config import load_settings
from learnradar.demo import demo_settings
from learnradar.tasks import TaskQueue
from learnradar.worker import run_worker
directory=Path(sys.argv[1])
run_worker(demo_settings(load_settings(local_overrides=False),directory),TaskQueue(directory,demo=True),once=True,demo_delay=float(sys.argv[3]),scenario=sys.argv[2])
"""
        return subprocess.Popen([sys.executable, '-c', code, str(self.directory), scenario, str(delay)],
                                cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

    def finish(self, process):
        _, error = process.communicate(timeout=30)
        self.assertEqual(process.returncode, 0, error.decode('utf-8', errors='replace'))

    def test_actual_manifest_bytes_mime_csp_and_no_login_html(self):
        dist = ROOT / 'frontend/dist'
        self.assertTrue((dist / 'manifest.json').exists(), 'run npm build before this integration suite')
        manifest = json.loads((dist / 'manifest.json').read_text(encoding='utf-8'))
        anonymous = Client(self.server)
        for name in manifest['files']:
            import http.client
            conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port)
            conn.request('GET', '/static/ui/' + name)
            response = conn.getresponse(); payload = response.read()
            self.assertEqual(response.status, 200, name)
            self.assertNotIn('text/html', response.getheader('Content-Type'))
            self.assertEqual(hashlib.sha256(payload).digest(), hashlib.sha256((dist / name).read_bytes()).digest())
            self.assertEqual(response.getheader('X-Content-Type-Options'), 'nosniff')
            conn.close()
        self.assertEqual(anonymous.request('/history')[0], 303)
        status, headers, _ = anonymous.request('/static/ui/missing.glb')
        self.assertEqual(status, 404); self.assertTrue(headers['Content-Type'].startswith('text/plain'))
        self.assertNotIn('Location', headers)
        _, headers, home = self.client.request('/')
        for policy in ("default-src 'none'", "form-action 'self'", "object-src 'none'", "frame-ancestors 'none'"):
            self.assertIn(policy, headers['Content-Security-Policy'])
        self.assertNotIn('blob:', headers['Content-Security-Policy'])
        self.assertIn('src="/static/ui/assets/app.js"', home)
        self.assertTrue(bootstrap(home)['ui_resources']['available'])

    def test_concurrent_same_native_submission_key_and_cancel(self):
        identifier, fields = self.submit()
        def again(_):
            client = Client(self.server); client.cookie = self.client.cookie
            return client.request('/tasks', fields)[:2]
        with ThreadPoolExecutor(max_workers=4) as pool:
            replies = list(pool.map(again, range(8)))
        self.assertTrue(all(status == 303 and headers['Location'] == '/task/' + identifier for status, headers in replies))
        self.assertEqual(len(self.queue.list()), 1)
        task = self.client.request('/task/' + identifier)[2]
        self.assertEqual(self.client.request('/task/' + identifier + '/cancel', {'csrf_token': csrf_from(task)})[0], 303)
        saved = self.queue.get(identifier)
        self.assertEqual((saved['status'], saved['model_requests'], saved['reserved_day']), ('cancelled', 0, None))
        before = self.state()
        for path in ('/', '/task/' + identifier, '/history', '/index.html'):
            self.assertEqual(self.client.request(path)[0], 200)
        self.assertEqual(before, self.state())

    def test_compiled_page_csrf_origin_and_expired_adoptable_report(self):
        seed(self.directory, 1, state_samples=False)
        report = self.client.request('/report/fixture-f4-001')[2]
        fields = {'note': 'fixture-only', 'csrf_token': csrf_from(report)}
        before = self.state()
        for origin in (None, '', 'null', 'https://cross-origin.invalid'):
            self.assertEqual(self.client.request('/feedback/fixture-f4-001', fields, headers={'Origin': origin})[0], 403)
        for token in ('', 'invalid-fixture'):
            self.assertEqual(self.client.request('/feedback/fixture-f4-001', {**fields, 'csrf_token': token})[0], 403)
        self.assertEqual(before, self.state())
        with self.queue.store() as store:
            store.conn.execute('UPDATE web_sessions SET expires=0'); store.conn.commit()
        self.assertEqual(self.client.request('/report/fixture-f4-001')[0], 303)
        self.assertEqual(self.client.request('/feedback/fixture-f4-001', fields)[0], 401)
        self.assertEqual(before, self.state())

    def test_independent_worker_report_reads_and_mail_retry_never_analyze(self):
        identifier, _ = self.submit()
        self.finish(self.worker('mail-failed'))
        task = self.queue.get(identifier)
        self.assertEqual((task['status'], task['report']['status'], task['email']['status']), ('completed', 'published', 'failed'))
        self.assertEqual(task['model_requests'], 2)
        before = self.state()
        for path in ('/', '/task/' + identifier, '/report/' + task['run_id'], '/history', '/report/' + task['run_id'] + '?saved=1'):
            self.assertEqual(self.client.request(path)[0], 200)
        self.assertEqual(before, self.state())
        page = self.client.request('/task/' + identifier)[2]
        self.assertEqual(self.client.request('/task/' + identifier + '/retry-email', {'csrf_token': csrf_from(page)})[0], 303)
        self.finish(self.worker())
        after = self.queue.get(identifier)
        self.assertEqual(after['model_requests'], 2)
        self.assertEqual(after['email']['status'], 'sent')
        self.assertEqual(after['email']['attempts'], 2)
        self.assertEqual(len(list((self.directory / 'fixture-mail').glob('*.eml'))), 2)

    def test_worker_rejected_model_failed_and_unknown_email(self):
        for scenario, status, report_status, mail_status in (
            ('rejected', 'completed', 'rejected', 'sent'),
            ('model-failed', 'failed', None, 'sent'),
            ('mail-unknown', 'completed', 'published', 'unknown'),
        ):
            with self.subTest(scenario=scenario):
                identifier, _ = self.submit(); self.finish(self.worker(scenario))
                task = self.queue.get(identifier)
                self.assertEqual(task['status'], status)
                self.assertEqual(task['report']['status'] if task['report'] else None, report_status)
                self.assertEqual(task['email']['status'], mail_status)
                if mail_status == 'unknown':
                    page = self.client.request('/task/' + identifier)[2]
                    self.assertNotIn('data-lr-action="retry-email"', page)
                    before = self.state()
                    self.assertEqual(self.client.request('/task/' + identifier + '/retry-email', {'csrf_token': csrf_from(page)})[0], 400)
                    self.assertEqual(before, self.state())
        home = self.client.request('/')[2]
        self.assertEqual(self.client.request('/tasks', {'csrf_token': csrf_from(home), 'discovery_mode': 'broad', 'submission_key': 'fixture-fourth-task-quota'})[0], 429)

    def test_feedback_overwrite_escape_damaged_and_legacy_links(self):
        seed(self.directory, 9, state_samples=False)
        for run in ('fixture-f4-001', 'fixture-f4-rejected'):
            for note in ('fixture first', '<script>fixtureBad()</script>&"'):
                page = self.client.request('/report/' + run)[2]
                status, headers, _ = self.client.request('/feedback/' + run, {'csrf_token': csrf_from(page), 'understood_what': 'unsure', 'note': note})
                self.assertEqual((status, headers['Location']), (303, '/report/' + run + '?saved=1'))
            saved = self.client.request('/report/' + run)[2]
            self.assertIn('&lt;script&gt;fixtureBad()&lt;/script&gt;', saved)
            self.assertNotIn('<script>fixtureBad()', saved)
        with self.queue.store() as store:
            self.assertEqual(store.conn.execute('SELECT COUNT(*) FROM feedback').fetchone()[0], 2)
            self.assertEqual(store.get_feedback('fixture-f4-001')['repo'], 'fixture-ui/project-001')
        damaged = self.client.request('/report/fixture-f4-damaged')[2]
        self.assertFalse(bootstrap(damaged)['data']['feedback']['available'])
        self.assertNotIn('data-lr-action="feedback"', damaged)
        self.assertEqual(self.client.request('/feedback/fixture-f4-damaged', {'csrf_token': self.client.csrf(), 'note': 'fixture'})[0], 400)
        for query in ('topic=agent-memory', 'q=case-007', 'tags=fixture,group-a', 'project=missing', 'project=fixture-ui%2Fproject-001'):
            self.assertEqual(self.client.request('/history?' + query)[0], 200)

    def test_count_windows_keep_all_native_identities(self):
        for count in (0, 1, 7, 8, 9, 40, 100):
            with self.subTest(count=count), tempfile.TemporaryDirectory() as tmp:
                queue = seed(Path(tmp), count, state_samples=False)
                server = serve(queue.path, queue=queue, settings=self.settings, port=0)
                threading.Thread(target=server.serve_forever, daemon=True).start()
                try:
                    client = Client(server); self.assertEqual(client.login()[0], 303)
                    page = client.request('/history')[2]; boot = bootstrap(page)
                    self.assertEqual(boot['data']['project_count'], count)
                    self.assertEqual(len(boot['data']['projects']), min(count, 40))
                    self.assertEqual(boot['data']['truncated'], count > 40)
                    self.assertEqual(page.count('data-lr-role="project"'), count)
                    if count:
                        self.assertIn('fixture-ui/project-' + f'{count:03}', page)
                finally:
                    server.shutdown(); server.server_close()


if __name__ == '__main__':
    unittest.main()
