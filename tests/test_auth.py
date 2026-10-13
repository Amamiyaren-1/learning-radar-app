"""Offline security behavior over actual loopback HTTP, with private fixture databases."""
import dataclasses
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock
from urllib.parse import urlencode

from learnradar.auth import AuthService, HASH_SLOT, LoginLimiter, FIXTURE_PASSWORD, FIXTURE_USERNAME, password_hash, password_matches
from learnradar.auth_store import AuthStore, token_digest, ANONYMOUS_LIMIT, AUTHENTICATED_LIMIT, SESSION_LIMIT
from learnradar.auth_http import safe_next
from learnradar.config import ConfigError, load_settings
from learnradar.tasks import TaskQueue
from learnradar.web import serve
from learnradar.web_config import WebConfig, load_web_config
from tests.auth_helpers import csrf_from


class Client:
    def __init__(self, server):
        self.server, self.cookie = server, ''
        self.base = f'http://127.0.0.1:{server.server_port}'

    def request(self, path='/', fields=None, *, headers=None, method=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        method = method or ('POST' if fields is not None else 'GET')
        supplied = {'Cookie': self.cookie} if self.cookie else {}
        body = None
        if fields is not None:
            body = urlencode(fields)
            supplied.update({'Origin': self.base, 'Sec-Fetch-Site': 'same-origin', 'Content-Type': 'application/x-www-form-urlencoded'})
        supplied.update(headers or {})
        supplied = {key:value for key,value in supplied.items() if value is not None}
        conn.request(method, path, body=body, headers=supplied)
        response = conn.getresponse()
        status, response_headers, document = response.status, dict(response.getheaders()), response.read().decode('utf-8')
        cookie = response_headers.get('Set-Cookie')
        if cookie:
            self.cookie = cookie.split(';', 1)[0]
        conn.close()
        return status, response_headers, document

    def login(self, password=FIXTURE_PASSWORD, username=FIXTURE_USERNAME, next_path='/'):
        status, _, document = self.request('/login')
        if status == 303:
            return status, {}, ''
        return self.request('/login', {'username': username, 'password': password, 'csrf_token': csrf_from(document), 'next': next_path})

    def csrf(self):
        return csrf_from(self.request('/')[2])


class HttpAuthTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.queue = TaskQueue(self.directory, demo=True)
        self.settings = load_settings(local_overrides=False)
        self.server = serve(self.queue.path, queue=self.queue, settings=self.settings, port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.client = Client(self.server)

    def business_state(self):
        with self.queue.store() as store:
            return {table: [tuple(r) for r in store.conn.execute('SELECT * FROM '+table)] for table in ('tasks','runs','reports','feedback','cycles','emails','task_requests')}

    def seed_report(self):
        fixture = json.loads((Path(__file__).parent/'fixtures/offline_analysis.json').read_text(encoding='utf-8'))
        with self.queue.store() as store:
            store.start_run('fixture-saved', kind='fixture', started_at='fixture', snapshot={'topic':'agent-memory'})
            store.finish_run('fixture-saved', status='rejected', finished_at='fixture')
            store.save_report('fixture-saved', report_id='fixture-saved', created_at='fixture', status='rejected', title='fixture 保存结果', markdown_path='', html_path='', payload=fixture, validation={'ok':False,'errors':['fixture 原拒绝原因'],'warnings':[]})

    def test_anonymous_all_private_paths_and_minimal_health(self):
        self.seed_report()
        before = self.business_state()
        for path in ('/','/index.html','/history','/task/private','/report/fixture-saved','/feedback/fixture-saved','/anything'):
            status, headers, body = self.client.request(path)
            self.assertEqual(status, 303)
            self.assertTrue(headers['Location'].startswith('/login'))
            self.assertNotIn('fixture 保存结果', body)
        status, headers, body = self.client.request('/health')
        self.assertEqual((status,body), (200,'ok'))
        self.assertNotIn('learning-radar', headers.get('Server',''))
        self.assertEqual(before,self.business_state())

    def test_login_rotation_storage_and_private_report(self):
        self.seed_report()
        self.client.request('/login')
        before = self.client.cookie
        self.assertEqual(self.client.login()[0],303)
        self.assertTrue(self.client.cookie != before)
        with self.queue.store() as store:
            rows = store.conn.execute('SELECT digest FROM web_sessions').fetchall()
            self.assertEqual(len(rows),1)
            self.assertTrue(rows[0][0] != self.client.cookie.split('=',1)[1])
        self.assertIn('fixture 保存结果',self.client.request('/report/fixture-saved')[2])
        self.assertIn('name="csrf_token"',self.client.request('/report/fixture-saved')[2])

    def test_wrong_password_and_fake_cookie(self):
        before=self.business_state()
        self.assertEqual(self.client.login(password='incorrect-fixture')[0],401)
        self.client.cookie = 'lr_session='+'a'*64
        self.assertEqual(self.client.request('/history')[0],303)
        self.assertEqual(before,self.business_state())

    def test_expired_idle_and_absolute_sessions(self):
        for column in ('seen','expires'):
            with self.subTest(column=column):
                self.client.cookie=''
                self.assertEqual(self.client.login()[0],303)
                with self.queue.store() as store:
                    store.conn.execute('UPDATE web_sessions SET '+column+'=0')
                    store.conn.commit()
                self.assertEqual(self.client.request('/')[0],303)

    def test_logout_is_post_only_and_revokes_cookie(self):
        self.client.login()
        old=self.client.cookie
        self.assertEqual(self.client.request('/logout')[0],405)
        result=self.client.request('/logout',{'csrf_token':self.client.csrf()})
        self.assertEqual(result[0],303)
        self.assertIn('Max-Age=0',result[1]['Set-Cookie'])
        self.client.cookie=old
        self.assertEqual(self.client.request('/history')[0],303)

    def test_reset_and_revoke_all_invalidate_old_sessions(self):
        for reset in (False,True):
            self.client.cookie=''
            self.client.login()
            if reset:
                self.server.auth.store.set_account(FIXTURE_USERNAME,password_hash(FIXTURE_PASSWORD),fixture=True,reset=True)
            else:
                self.server.auth.store.revoke_all()
            self.assertEqual(self.client.request('/')[0],303)

    def test_login_itself_requires_csrf_and_origin(self):
        before=self.business_state()
        _,_,body=self.client.request('/login')
        fields={'username':FIXTURE_USERNAME,'password':FIXTURE_PASSWORD}
        self.assertEqual(self.client.request('/login',fields)[0],403)
        fields['csrf_token']=csrf_from(body)
        for origin in (None, '', 'null'):
            self.assertEqual(self.client.request('/login',fields,headers={'Origin':origin})[0],403)
        self.assertEqual(self.client.request('/login',fields,headers={'Origin':'https://other.invalid'})[0],403)
        self.assertEqual(self.client.request('/login',fields,headers={'Sec-Fetch-Site':'cross-site'})[0],403)
        self.assertEqual(before,self.business_state())

    def test_all_writes_reject_missing_wrong_and_cross_session_tokens(self):
        self.seed_report()
        task=self.queue.submit('agent-memory','fixture-csrf-existing')
        self.client.login()
        other=Client(self.server);other.login()
        wrong=other.csrf()
        before=self.business_state()
        for path in ('/tasks',f'/task/{task["task_id"]}/cancel',f'/task/{task["task_id"]}/retry-email','/feedback/fixture-saved','/logout'):
            for token in (None,'incorrect',wrong):
                fields={'topic':'agent-memory','submission_key':'fixture-csrf-denied','note':'must not save'}
                if token is not None: fields['csrf_token']=token
                self.assertEqual(self.client.request(path,fields)[0],403)
        self.assertEqual(before,self.business_state())

    def test_unauthenticated_write_has_no_business_effect(self):
        before=self.business_state()
        for path in ('/tasks','/task/fixture/cancel','/task/fixture/retry-email','/feedback/fixture','/logout'):
            self.assertEqual(self.client.request(path,{'csrf_token':'fake'})[0],401)
        self.assertEqual(before,self.business_state())

    def test_same_origin_response_policy_and_strict_write_origins(self):
        for path in ('/health','/login','/history'):
            self.assertEqual(self.client.request(path)[1]['Referrer-Policy'],'same-origin')
        self.client.login()
        before=self.business_state()
        for path in ('/tasks','/task/fixture/cancel','/task/fixture/retry-email','/feedback/fixture','/logout'):
            for origin in (None,'','null','https://other.invalid'):
                fields={'csrf_token':self.client.csrf(),'topic':'agent-memory','submission_key':'fixture-origin-denied'}
                self.assertEqual(self.client.request(path,fields,headers={'Origin':origin})[0],403)
        self.assertEqual(before,self.business_state())

    def test_login_capacity_exhaustion_is_service_unavailable(self):
        before=self.business_state()
        with mock.patch.object(self.server.auth.store,'create_session',return_value=None):
            status,headers,body=self.client.request('/login')
        self.assertEqual(status,503)
        self.assertNotIn('Set-Cookie',headers)
        self.assertNotIn('csrf_token',body)
        self.assertEqual(before,self.business_state())

    def test_hosts_origins_fetch_metadata_and_proxy_spoofing(self):
        self.client.login()
        fields={'topic':'agent-memory','submission_key':'fixture-source-denied','csrf_token':self.client.csrf()}
        before=self.business_state()
        for headers in ({'Host':'attacker.invalid'},{'Origin':'null'},{'Origin':'https://other.invalid'},{'Origin':self.client.base+'/'},{'Sec-Fetch-Site':'same-site'},{'X-Forwarded-Proto':'https'},{'X-Forwarded-For':'127.0.0.1'},{'Forwarded':'proto=https'}):
            self.assertEqual(self.client.request('/tasks',fields,headers=headers)[0],403)
        self.assertEqual(self.client.request('/history',headers={'Host':'attacker.invalid'})[0],403)
        self.assertEqual(before,self.business_state())

    def test_authenticated_submit_idempotency_quota_and_cancel(self):
        self.client.login()
        fields={'topic':'agent-memory','submission_key':'fixture-auth-idempotent','csrf_token':self.client.csrf()}
        first=self.client.request('/tasks',fields)
        second=self.client.request('/tasks',fields)
        self.assertEqual(first[0],303);self.assertEqual(first[1]['Location'],second[1]['Location'])
        self.assertEqual(len(self.queue.list()),1)
        for number in range(2):
            self.assertEqual(self.client.request('/tasks',{**fields,'submission_key':f'fixture-auth-task-{number}'})[0],303)
        self.assertEqual(self.client.request('/tasks',{**fields,'submission_key':'fixture-fourth-task'})[0],429)
        self.assertEqual(self.client.request(first[1]['Location']+'/cancel',{'csrf_token':self.client.csrf()})[0],303)
        self.assertEqual(self.queue.get(first[1]['Location'].split('/')[-1])['status'],'cancelled')
        self.assertEqual(sum(t['model_requests'] for t in self.queue.list()),0)

    def test_limiter_and_hash_concurrency_bound(self):
        self.assertEqual(self.client.login(password='fixture-wrong')[0],401)
        with mock.patch('learnradar.auth.password_matches') as verify:
            self.assertEqual(self.client.login(password='fixture-wrong')[0],429)
            verify.assert_not_called()
        self.server.auth.limiter.clock=lambda:100000000000
        self.assertEqual(self.client.login()[0],303)
        other=Client(self.server)
        HASH_SLOT.acquire()
        try:
            with mock.patch('learnradar.auth.password_matches') as verify:
                self.assertEqual(other.login()[0],429)
                verify.assert_not_called()
        finally: HASH_SLOT.release()

    def test_local_cookie_flags_safe_redirect_and_no_get_mutations(self):
        result=self.client.login(next_path='https://other.invalid')
        cookie=result[1]['Set-Cookie']
        for flag in ('HttpOnly','SameSite=Strict','Path=/'):
            self.assertIn(flag,cookie)
        self.assertNotIn('Secure',cookie);self.assertNotIn('Domain',cookie)
        self.assertEqual(result[1]['Location'],'/')
        before=self.business_state()
        self.client.request('/task/fixture/cancel')
        self.client.request('/feedback/fixture')
        self.assertEqual(before,self.business_state())

    def test_https_policy_secure_cookie_and_explicit_proxy_only(self):
        self.server.auth.config=WebConfig('https','https://radar.example.invalid',('127.0.0.1',)).validate()
        headers={'Host':'radar.example.invalid','X-Forwarded-Proto':'https','X-Forwarded-Host':'radar.example.invalid'}
        self.assertEqual(self.client.request('/login')[0],403)
        status,response_headers,body=self.client.request('/login',headers=headers)
        self.assertEqual(status,200)
        self.assertIn('__Host-lr_session=',response_headers['Set-Cookie'])
        self.assertIn('; Secure',response_headers['Set-Cookie'])
        fields={'csrf_token':csrf_from(body),'username':FIXTURE_USERNAME,'password':FIXTURE_PASSWORD}
        result=self.client.request('/login',fields,headers={**headers,'Origin':'https://radar.example.invalid'})
        self.assertEqual(result[0],303)
        self.server.auth.config=dataclasses.replace(self.server.auth.config,trusted_proxies=('127.0.0.2',))
        self.assertEqual(self.client.request('/history',headers=headers)[0],403)

    def test_minimal_logs_do_not_contain_credentials_tokens_or_form(self):
        self.client.request('/login')
        pre_cookie=self.client.cookie
        with self.assertLogs('learnradar.web.auth',level='INFO') as logs:
            self.client.login()
            self.client.request('/tasks',{'csrf_token':'wrong','note':'private-fixture-note'})
        combined='\n'.join(logs.output)
        for value in (FIXTURE_PASSWORD,pre_cookie,self.client.cookie,'private-fixture-note'):
            self.assertTrue(value not in combined)
        self.assertIn('event=login result=accepted',combined)


class AuthUnitTests(unittest.TestCase):
    def test_scrypt_salted_and_password_not_saved(self):
        first,second=password_hash(FIXTURE_PASSWORD),password_hash(FIXTURE_PASSWORD)
        self.assertTrue(first != second)
        self.assertTrue(FIXTURE_PASSWORD not in first)
        self.assertTrue(password_matches(FIXTURE_PASSWORD,first))
        self.assertFalse(password_matches('incorrect',first))
        self.assertFalse(password_matches(FIXTURE_PASSWORD,'malformed'))

    def test_limiter_has_cleanup_and_size_bound(self):
        clock=[0]
        limiter=LoginLimiter(lambda:clock[0])
        for i in range(1000):limiter.failure(str(i))
        self.assertEqual(len(limiter.entries),128)
        self.assertFalse(limiter.allowed('999'))
        clock[0]=301
        self.assertTrue(limiter.allowed('999'));self.assertEqual(len(limiter.entries),0)

    def test_redirects_are_only_relative_local(self):
        for value in ('https://other.invalid','//other.invalid','/\\other','/%2fother','/x%0d%0aLocation:x'):
            self.assertEqual(safe_next(value),'/')
        self.assertEqual(safe_next('/history?topic=agent-memory'),'/history?topic=agent-memory')

    def test_formal_missing_account_and_missing_mode_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            queue=TaskQueue(Path(directory))
            with self.assertRaises(ConfigError):serve(queue.path,port=0,web_config=WebConfig('loopback'))
            with self.assertRaises(ConfigError):serve(queue.path,port=0)
            with self.assertRaises(ConfigError):serve(queue.path,host='0.0.0.0',port=0,web_config=WebConfig('loopback'))

    def test_auth_demo_and_production_do_not_mix(self):
        with tempfile.TemporaryDirectory() as directory:
            queue=TaskQueue(Path(directory),demo=True)
            AuthService(queue.path,WebConfig('loopback'),demo=True)
            with self.assertRaises(ConfigError):AuthService(queue.path,WebConfig('loopback'),demo=False)

    def test_session_storage_is_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            queue=TaskQueue(Path(directory),demo=True)
            auth=AuthService(queue.path,WebConfig('loopback'),demo=True)
            for i in range(300):auth.store.create_session(i,auth.config)
            with queue.store() as store:
                self.assertLessEqual(store.conn.execute('SELECT COUNT(*) FROM web_sessions').fetchone()[0],256)

    def test_session_pools_remain_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            queue=TaskQueue(Path(directory),demo=True)
            auth=AuthService(queue.path,WebConfig('loopback'),demo=True)
            for i in range(300):
                auth.store.create_session(100+i,auth.config,authenticated=True)
                auth.store.create_session(100+i,auth.config)
            with queue.store() as store:
                counts=dict(store.conn.execute('SELECT authenticated,COUNT(*) FROM web_sessions GROUP BY authenticated').fetchall())
            self.assertEqual(counts,{0:ANONYMOUS_LIMIT,1:AUTHENTICATED_LIMIT})
            self.assertEqual(sum(counts.values()),SESSION_LIMIT)

    def test_legacy_full_authenticated_pool_refuses_anonymous_without_eviction(self):
        with tempfile.TemporaryDirectory() as directory:
            queue=TaskQueue(Path(directory),demo=True)
            auth=AuthService(queue.path,WebConfig('loopback'),demo=True)
            token,_=auth.store.create_session(100,auth.config,authenticated=True)
            with queue.store() as store:
                for i in range(255):
                    store.conn.execute('INSERT INTO web_sessions SELECT ?,csrf,authenticated,generation,created,seen,expires FROM web_sessions LIMIT 1',(f'{i:064x}',))
                store.conn.commit()
                before=[tuple(row) for row in store.conn.execute('SELECT * FROM web_sessions ORDER BY digest')]
            self.assertIsNone(auth.store.create_session(101,auth.config))
            with queue.store() as store:
                self.assertEqual(before,[tuple(row) for row in store.conn.execute('SELECT * FROM web_sessions ORDER BY digest')])
            self.assertIsNotNone(auth.store.session(token,101,auth.config))

    def test_anonymous_session_flood_cannot_evict_authenticated_session(self):
        with tempfile.TemporaryDirectory() as directory:
            queue=TaskQueue(Path(directory),demo=True)
            auth=AuthService(queue.path,WebConfig('loopback'),demo=True)
            token,_=auth.store.create_session(100,auth.config,authenticated=True)
            for i in range(400):auth.store.create_session(101+i,auth.config)
            self.assertTrue(auth.store.session(token,501,auth.config) is not None)
            with queue.store() as store:
                self.assertLessEqual(store.conn.execute('SELECT COUNT(*) FROM web_sessions').fetchone()[0],256)

    def test_corrupt_verifier_refuses_server(self):
        with tempfile.TemporaryDirectory() as directory:
            queue=TaskQueue(Path(directory),demo=True)
            AuthStore(queue.path).set_account('fixture','malformed',fixture=True)
            with self.assertRaises(ConfigError):AuthService(queue.path,WebConfig('loopback'),demo=True)

    def test_incremental_migration_preserves_all_business_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            queue=TaskQueue(Path(directory),demo=True)
            task=queue.submit('agent-memory','fixture-migration-task')
            with queue.store() as store:
                store.start_run('fixture-legacy',kind='fixture',started_at='fixture',snapshot={})
                store.finish_run('fixture-legacy',status='rejected',finished_at='fixture')
                store.save_report('fixture-legacy',report_id='fixture',created_at='fixture',status='rejected',title='fixture',markdown_path='',html_path='',payload={},validation={'ok':False})
                store.save_feedback('fixture-legacy','fixture/project',{'want_more':'no'},'fixture note',now='fixture')
                store.save_cycle('fixture-cycle',topic='agent-memory',status='completed',run_id='fixture-legacy',started_at='fixture')
                store.create_email('fixture-mail',cycle_id='fixture-cycle',run_id='fixture-legacy',kind='failure',recipient='fixture@example.invalid',subject='fixture',body='fixture',status='sent')
                tables=('runs','reports','feedback','tasks','cycles','emails','candidates','usage_log')
                before={table:[tuple(r) for r in store.conn.execute('SELECT * FROM '+table)] for table in tables}
                store.conn.executescript('DROP TABLE web_account; DROP TABLE web_sessions;')
            with queue.store() as store:
                after={table:[tuple(r) for r in store.conn.execute('SELECT * FROM '+table)] for table in tables}
            self.assertEqual(before,after)

    def test_cli_hidden_password_init_reset_and_revoke(self):
        from learnradar.auth_cli import main
        import contextlib,io
        with tempfile.TemporaryDirectory() as directory:
            output=io.StringIO()
            for action in ('init','reset','revoke-sessions'):
                with mock.patch('learnradar.auth_cli.load_settings',return_value=load_settings(local_overrides=False)),mock.patch('sys.stdin.isatty',return_value=True),mock.patch('getpass.getpass',side_effect=[FIXTURE_PASSWORD,FIXTURE_PASSWORD]),contextlib.redirect_stdout(output):
                    self.assertEqual(main([action,'--data-dir',directory]),0)
            self.assertTrue(FIXTURE_PASSWORD not in output.getvalue())

    def test_configuration_rejects_unsafe_origins_proxies_and_ttls(self):
        for config in (WebConfig(''),WebConfig('https','http://radar.invalid',('127.0.0.1',)),WebConfig('https','https://radar.invalid/path',('127.0.0.1',)),WebConfig('https','https://radar.invalid',()),WebConfig('https','https://radar.invalid',('0.0.0.0',)),WebConfig('loopback',idle_seconds=0)):
            with self.assertRaises(ConfigError):config.validate()

    def test_saved_example_https_configuration_loads_without_network(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'config').mkdir()
            (root/'config/web.toml').write_text((Path(__file__).resolve().parents[1]/'config/web.example.toml').read_text(encoding='utf-8'),encoding='utf-8')
            settings=dataclasses.replace(load_settings(local_overrides=False),root=root)
            config=load_web_config(settings)
            self.assertTrue(config.secure)
            self.assertEqual(config.public_origin,'https://radar.example.invalid')
            self.assertEqual(config.trusted_proxies,('127.0.0.1',))
            (root/'config/web.toml').write_text('invalid TOML [',encoding='utf-8')
            with self.assertRaises(ConfigError):load_web_config(settings)


if __name__ == '__main__':
    unittest.main()
