"""B1 real loopback HTTP and pure projections, all data/assets fixture-only."""
import dataclasses
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

from learnradar.config import load_settings
from learnradar.tasks import TaskQueue
from learnradar.ui_assets import AssetRegistry
from learnradar.ui_projection import envelope, home_data, task_actions
from learnradar.ui_shell import shell, safe_json
from learnradar.web import serve
from tests import test_auth as auth_helpers


def bootstrap(document):
    return json.loads(document.split('<script id="lr-bootstrap" type="application/json">', 1)[1].split('</script>', 1)[0])


def fixture_assets(root):
    root.mkdir(parents=True, exist_ok=True)
    files = {'entry.js':b'// fixture only; no business logic', 'style.css':b'/* fixture */',
             'model.glb':b'glTF-fixture-not-real-model', 'font.woff2':b'fixture-font',
             'audio.ogg':b'fixture-audio', 'public.json':b'{"fixture":true}'}
    for name, content in files.items(): (root / name).write_bytes(content)
    manifest = {'version':1, 'files':list(files), 'scripts':['entry.js'], 'styles':['style.css']}
    (root / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    return files


class ProjectionTests(unittest.TestCase):
    def test_safe_bootstrap_cannot_close_script_or_copy_private_fields(self):
        task={'task_id':'fixture-task', 'run_id':'fixture-run', 'status':'queued', 'model_requests':0,
              'input':{'discovery_mode':'broad', 'api_key':'never-project'}, 'worker_token':'never-project'}
        value=envelope('home', fixture=True, **home_data([task], []))
        value['data']['reports']=[{'title':'</script><script>alert(1)</script>&\u2028\u2029'}]
        raw=safe_json(value)
        self.assertNotIn('<', raw); self.assertNotIn('never-project', raw)
        self.assertEqual(json.loads(raw), value)

    def test_bounded_collections_keep_real_counts_and_stable_ids(self):
        for count in (0,1,7,8,9,40,100):
            tasks=[{'task_id':'fixture-'+str(n), 'run_id':'fixture-run-'+str(n), 'status':'completed'} for n in range(count)]
            data=home_data(tasks, [])
            self.assertEqual(data['task_count'],count)
            self.assertEqual(len(data['tasks']),min(count,40))
            self.assertEqual(data['truncated'],count>40)
            self.assertEqual([t['task_id'] for t in data['tasks']], [t['task_id'] for t in tasks[:40]])

    def test_action_flags_keep_weekly_paid_unknown_restrictions(self):
        self.assertTrue(task_actions({'status':'queued','model_requests':0})['cancel'])
        for task in ({'status':'queued','cycle_id':'fixture-cycle'}, {'status':'queued','model_requests':1}):
            self.assertFalse(task_actions(task)['cancel'])
        self.assertFalse(task_actions({'status':'completed','run':{'status':'rejected'},'email':{'status':'unknown'}})['retry_email'])

    def test_missing_or_invalid_template_uses_native_fallback(self):
        with mock.patch('learnradar.ui_shell.ROOT',Path('fixture-nonexistent-root')):
            result=shell('fixture', '<form method="post" action="/tasks"></form>', '', kind='home')
        self.assertIn('id="lr-business"',result); self.assertIn('action="/tasks"',result)


class AssetTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name); self.files=fixture_assets(self.root)

    def test_manifest_mime_and_traversal_whitelist(self):
        assets=AssetRegistry(self.root)
        self.assertTrue(assets.available)
        self.assertEqual(assets.read('model.glb')[1], 'model/gltf-binary')
        for name in ('../model.glb','%2e%2e/model.glb','%252e%252e/model.glb','/model.glb',
                     'manifest.json','.env','config/email.toml','learning-radar.sqlite','entry.js.map','..\\model.glb'):
            self.assertIsNone(assets.read(name))
        (self.root/'model.glb').unlink()
        self.assertIsNone(assets.read('model.glb'))

    def test_invalid_missing_and_oversize_builds_disable_enhancement(self):
        (self.root/'model.glb').unlink()
        self.assertFalse(AssetRegistry(self.root).available)
        (self.root/'model.glb').write_bytes(b'fixture')
        with mock.patch('learnradar.ui_assets.MAX_FILE_BYTES', 2):
            self.assertFalse(AssetRegistry(self.root).available)
        (self.root/'manifest.json').write_text('{"version":1,"files":["../config/email.toml"]}',encoding='utf-8')
        self.assertFalse(AssetRegistry(self.root).available)

    def test_symlink_outside_build_not_readable(self):
        external=self.root.parent/('fixture-outside-'+self.root.name+'.js')
        external.write_text('fixture outside',encoding='utf-8'); self.addCleanup(external.unlink)
        (self.root/'entry.js').unlink()
        try: (self.root/'entry.js').symlink_to(external)
        except OSError:
            self.skipTest('Host does not permit fixture symlink creation; containment still tested')
        self.assertFalse(AssetRegistry(self.root).available)

    def test_symlink_policy_without_os_privileges(self):
        original=Path.is_symlink
        with mock.patch.object(Path,'is_symlink',lambda p: p.name=='entry.js' or original(p)):
            self.assertFalse(AssetRegistry(self.root).available)
        with mock.patch.object(Path,'is_symlink',lambda p: p==self.root or original(p)):
            self.assertFalse(AssetRegistry(self.root).available)

    def test_offline_manifest_generator(self):
        result=subprocess.run([sys.executable,str(Path(__file__).resolve().parents[1]/'scripts/ui_manifest.py'),
                               '--dist',str(self.root),'--script','entry.js','--style','style.css'],
                              capture_output=True, timeout=10)
        self.assertEqual(result.returncode,0,result.stderr.decode('utf-8',errors='replace'))
        self.assertTrue(AssetRegistry(self.root).available)


class HttpUiTests(unittest.TestCase):
    business_state=auth_helpers.HttpAuthTests.business_state
    seed_report=auth_helpers.HttpAuthTests.seed_report

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.directory=Path(self.tmp.name)/'data'
        self.queue=TaskQueue(self.directory,demo=True)
        source=Path(self.tmp.name)/'source'; fixture_assets(source/'frontend/dist')
        self.settings=dataclasses.replace(load_settings(local_overrides=False),root=source)
        self.server=serve(self.queue.path,queue=self.queue,settings=self.settings,port=0)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True); self.thread.start()
        self.addCleanup(self.server.server_close); self.addCleanup(self.server.shutdown)
        self.client=auth_helpers.Client(self.server)

    def test_native_login_home_submit_cancel_logout_and_no_secret_bootstrap(self):
        before=self.business_state()
        self.assertEqual(self.client.request('/')[0],303)
        login=self.client.request('/login')[2]
        self.assertEqual(bootstrap(login)['page_kind'],'login')
        self.assertTrue(bootstrap(login)['fixture'])
        self.assertIn('data-lr-action="login"',login)
        self.assertNotIn('csrf_token',json.dumps(bootstrap(login)))
        self.assertEqual(before,self.business_state())
        self.assertEqual(self.client.login()[0],303)
        home=self.client.request('/')[2]
        self.assertIn('data-lr-action="logout"',home)
        self.assertEqual(bootstrap(home)['page_kind'],'home')
        self.assertNotIn('csrf_token',json.dumps(bootstrap(home)))
        self.assertNotIn('submission_key',json.dumps(bootstrap(home)))
        fields={'discovery_mode':'broad','submission_key':'fixture-b1-submit-key','csrf_token':self.client.csrf()}
        first=self.client.request('/tasks',fields); repeated=self.client.request('/tasks',fields)
        self.assertEqual((first[0],repeated[0]),(303,303)); self.assertEqual(first[1]['Location'],repeated[1]['Location'])
        task_page=self.client.request(first[1]['Location'])[2]
        data=bootstrap(task_page)
        self.assertEqual(data['data']['task']['discovery_mode'],'broad')
        self.assertEqual(data['data']['task']['model_requests'],0)
        self.assertIn('data-lr-action="cancel"',task_page)
        self.assertEqual(self.client.request(first[1]['Location']+'/cancel',{'csrf_token':self.client.csrf()})[0],303)
        self.assertEqual(self.client.request('/logout',{'csrf_token':self.client.csrf()})[0],303)
        self.assertEqual(self.client.request('/history')[0],303)

    def test_report_rejection_feedback_saved_and_script_closure(self):
        self.seed_report(); self.client.login()
        with self.queue.store() as store:
            store.conn.execute('UPDATE reports SET title=?',('</script><script>fixture evil</script>',)); store.conn.commit()
        document=self.client.request('/report/fixture-saved')[2]
        value=bootstrap(document)
        self.assertEqual(value['data']['report']['status'],'rejected')
        self.assertEqual(value['data']['report']['errors'],['fixture 原拒绝原因'])
        self.assertIn('data-lr-role="report-body"',document)
        self.assertIn('data-lr-action="feedback"',document)
        self.assertNotIn('<script>fixture evil',document)
        fields={'csrf_token':self.client.csrf(),'understood_what':'yes','note':'<script>fixture note</script>'}
        result=self.client.request('/feedback/fixture-saved',fields)
        self.assertEqual(result[1]['Location'],'/report/fixture-saved?saved=1')
        saved=self.client.request(result[1]['Location'])[2]
        self.assertTrue(bootstrap(saved)['data']['feedback']['saved'])
        self.assertIn('&lt;script&gt;fixture note',saved)

    def test_public_assets_do_not_redirect_to_html_and_private_pages_stay_private(self):
        before=self.business_state()
        for name, mime in (('model.glb','model/gltf-binary'),('font.woff2','font/woff2'),('audio.ogg','audio/ogg'),('public.json','application/json')):
            status,headers,_=self.client.request('/static/ui/'+name)
            self.assertEqual(status,200); self.assertEqual(headers['Content-Type'],mime)
            self.assertNotIn('Set-Cookie',headers)
        status,headers,_=self.client.request('/static/ui/missing.glb')
        self.assertEqual(status,404); self.assertTrue(headers['Content-Type'].startswith('text/plain'))
        self.assertEqual(self.client.request('/static/ui/entry.js',headers={'Host':'evil.example'})[0],403)
        self.assertEqual(self.client.request('/static/ui/entry.js',headers={'X-Forwarded-Proto':'https'})[0],403)
        self.client.login()
        with self.queue.store() as store:
            store.conn.execute('UPDATE web_sessions SET expires=0'); store.conn.commit()
        self.assertEqual(self.client.request('/history')[0],303)
        self.assertEqual(self.client.request('/static/ui/model.glb')[0],200)
        self.assertEqual(before,self.business_state())

    def test_csp_forms_and_rejected_writes_preserve_business_state(self):
        self.client.login(); before=self.business_state()
        status,headers,document=self.client.request('/')
        csp=headers['Content-Security-Policy']
        for directive in ("default-src 'none'", "script-src 'self'", "connect-src 'self'", "form-action 'self'", "frame-ancestors 'none'", "base-uri 'none'"):
            self.assertIn(directive,csp)
        self.assertNotIn('*',csp); self.assertNotIn('blob:',csp); self.assertNotIn('data:',csp)
        self.assertEqual(headers['Referrer-Policy'],'same-origin')
        self.assertEqual(headers['Cache-Control'],'no-store')
        for fields, extra in (({'submission_key':'fixture-unauthorized-key'},{}),
                              ({'csrf_token':'incorrect','submission_key':'fixture-unauthorized-key'},{}),
                              ({'csrf_token':self.client.csrf(),'submission_key':'fixture-unauthorized-key'},{'Origin':'null'})):
            self.assertEqual(self.client.request('/tasks',fields,headers=extra)[0],403)
        self.assertEqual(before,self.business_state())
        forms=re.findall(r'<form\b[^>]*method="post"[^>]*>(.*?)</form>',document,re.S)
        self.assertTrue(forms)
        self.assertTrue(all(form.count('name="csrf_token"')==1 for form in forms))

    def test_missing_asset_native_page_still_works(self):
        (self.settings.root/'frontend/dist/entry.js').unlink()
        self.client.login()
        document=self.client.request('/')[2]
        self.assertIn('id="lr-business"',document)
        self.assertIn('name="submission_key"',document)
        self.assertEqual(self.client.request('/static/ui/entry.js')[0],404)


class UiProcessTests(unittest.TestCase):
    def test_independent_isolated_process_serves_fixture_assets_and_auth(self):
        with tempfile.TemporaryDirectory() as raw:
            directory=Path(raw); fixture_assets(directory/'source/frontend/dist')
            stream=(directory/'process.log').open('w',encoding='utf-8')
            code='''import dataclasses,sys
from pathlib import Path
from learnradar.config import load_settings
from learnradar.tasks import TaskQueue
from learnradar.web import serve
settings=dataclasses.replace(load_settings(local_overrides=False),root=Path(sys.argv[1]))
queue=TaskQueue(Path(sys.argv[2]),demo=True)
server=serve(queue.path,settings=settings,queue=queue,port=0)
print(server.server_port,flush=True)
server.serve_forever()
'''
            process=subprocess.Popen([sys.executable,'-c',code,str(directory/'source'),str(directory/'data')],
                                     cwd=Path(__file__).resolve().parents[1],stdout=stream,stderr=subprocess.STDOUT)
            try:
                deadline=time.monotonic()+15; port=None
                while time.monotonic()<deadline and process.poll() is None:
                    output=(directory/'process.log').read_text(encoding='utf-8')
                    if output.strip().isdigit(): port=int(output.strip()); break
                    time.sleep(.05)
                self.assertIsNotNone(port,'Isolated fixture web process did not report its port')
                client=auth_helpers.Client(type('Server',(),{'server_port':port})())
                self.assertEqual(client.request('/report/private')[0],303)
                self.assertEqual(client.request('/static/ui/model.glb')[0],200)
                self.assertEqual(client.login()[0],303)
                self.assertEqual(bootstrap(client.request('/history')[2])['page_kind'],'projects')
            finally:
                if process.poll() is None: process.terminate(); process.wait(timeout=10)
                stream.close()
