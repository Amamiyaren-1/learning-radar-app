"""Offline end-to-end public-source check, no private configuration or network.

Starts a fresh fixture web/worker, uses actual native CSRF forms and verifies
published report/feedback, then stops exactly these child processes. Stdlib only.
"""
from pathlib import Path
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from learnradar.auth import FIXTURE_USERNAME, FIXTURE_PASSWORD


def main():
    import http.cookiejar
    from learnradar.demo import DemoServices
    from scripts.ui_fixture import audit
    directory = Path(tempfile.mkdtemp(prefix='public-smoke-', dir=ROOT / 'data'))
    log = (directory / 'process.log').open('w', encoding='utf-8'); children = []
    env = {k: v for k, v in os.environ.items() if not k.startswith(('LEARNRADAR_', 'GITHUB_'))}
    env.update(PYTHONIOENCODING='utf-8', NO_PROXY='127.0.0.1,localhost')
    try:
        script = "import sys;from scripts.ui_fixture import audit;sys.addaudithook(audit);from learnradar.task_cli import main;raise SystemExit(main())"
        web = subprocess.Popen([sys.executable, '-c', script, 'web', '--demo', '--data-dir', str(directory), '--port', '0'], cwd=ROOT, env=env, stdout=log, stderr=log)
        children.append(web); base = ''
        for _ in range(200):
            text = (directory / 'process.log').read_text(encoding='utf-8')
            match = re.search(r'http://127\.0\.0\.1:\d+', text)
            if match: base = match.group(0); break
            if web.poll() is not None: raise RuntimeError('fixture web failed')
            time.sleep(.05)
        assert base
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        def get(url):
            with opener.open(base + url) as r: return r.geturl(), r.read().decode()
        def post(url, fields):
            request = urllib.request.Request(base + url, data=urllib.parse.urlencode(fields).encode(), headers={'Origin': base, 'Content-Type': 'application/x-www-form-urlencoded'})
            with opener.open(request) as r: return r.geturl(), r.read().decode()
        def token(text): return re.search(r'name="csrf_token" value="([^"]+)"', text).group(1)
        _, login = get('/login'); _, home = post('/login', {'username': FIXTURE_USERNAME, 'password': FIXTURE_PASSWORD, 'next': '/', 'csrf_token': token(login)})
        key = re.search(r'name="submission_key" value="([^"]+)"', home).group(1)
        url, task = post('/tasks', {'submission_key': key, 'discovery_mode': 'broad', 'csrf_token': token(home)})
        task_id = url.rsplit('/', 1)[-1]
        worker = subprocess.Popen([sys.executable, '-c', script, 'worker', '--demo', '--data-dir', str(directory), '--once', '--demo-delay', '0'], cwd=ROOT, env=env, stdout=log, stderr=log)
        children.append(worker); assert worker.wait(timeout=40) == 0
        from learnradar.tasks import TaskQueue
        q = TaskQueue(directory, demo=True); row = q.get(task_id); assert row['status'] == 'completed' and row['model_requests'] == 2
        run = row['run_id']; _, report = get('/report/' + run); assert 'fixture' in report and 'data-lr-role="report-body"' in report
        url, saved = post('/feedback/' + run, {'note': 'fixture public export smoke / 未实测', 'understood_what': '', 'understood_highlight': '', 'want_more': '', 'csrf_token': token(report)})
        assert 'saved=1' in url
        with q.store() as s:
            r = s.get_report(run); assert r['status'] == 'published'
            f = s.conn.execute('SELECT * FROM feedback WHERE run_id=?', (run,)).fetchone(); assert f and f['note'] == 'fixture public export smoke / 未实测'
        print(json.dumps({'fixture': True, 'login': True, 'discovery_mode': 'broad', 'task': 'completed', 'report': 'published', 'feedback': 'saved', 'real_external_requests': 0, 'commercial_fonts_required': False}))
    finally:
        for process in children:
            if process.poll() is None: process.terminate(); process.wait(timeout=10)
        log.close()


if __name__ == '__main__':
    (ROOT / 'data').mkdir(exist_ok=True)
    main()
