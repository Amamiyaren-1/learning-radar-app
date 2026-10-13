"""Isolated discovery, persistent rotation, optional tags and project browsing."""
import contextlib
import dataclasses
import io
import json
import unittest
from urllib.parse import urlencode
from pathlib import Path
from unittest import mock

from learnradar.archive import project_catalog
from learnradar.browse import history_records
from learnradar.demo import DemoServices
from learnradar.discovery import collect_candidates, request_options
from learnradar.evidence import Evidence
from learnradar.net import FetchError, now_iso
from learnradar.tasks import TaskQueue, QuotaWait
from learnradar.validate import validate_report
from tests import test_tasks as queue_helpers
from tests import test_task_processes as processes


class DiscoveryTests(unittest.TestCase):
    setUp = queue_helpers.QueueTests.setUp
    work = queue_helpers.QueueTests.work

    def broad(self, key='fixture-discovery-submit'):
        topic, options = request_options(self.settings)
        return self.queue.submit(topic, key, options=options)

    def test_default_plan_idempotent_rotation_and_restart(self):
        first = self.broad()
        self.assertEqual(first['input']['discovery_mode'], 'broad')
        self.assertEqual(len(first['input']['discovery_plan']['queries']), 3)
        self.assertEqual(first['task_id'], self.broad()['task_id'])
        second = self.broad('fixture-discovery-submit-2')
        self.assertEqual(second['input']['discovery_plan']['cursor'], 3)
        self.queue.cancel(second['task_id'])
        queue = TaskQueue(self.directory, demo=True)
        self.assertEqual(first['input'], queue.get(first['task_id'])['input'])
        third = self.broad('fixture-discovery-submit-3')
        self.assertEqual(third['input']['discovery_plan']['cursor'], 6)

    def test_quota_rejection_does_not_advance_rotation_and_old_task_unchanged(self):
        old = self.queue.submit('agent-memory', 'fixture-old-topic-task')
        self.assertNotIn('discovery_mode', old['input'])
        self.broad('fixture-broad-task-a'); self.broad('fixture-broad-task-b')
        with self.assertRaises(QuotaWait): self.broad('fixture-broad-task-c')
        with self.queue.store() as store:
            self.assertEqual(store.conn.execute('SELECT cursor FROM discovery_cursor').fetchone()[0], 6)
        self.assertEqual(request_options(self.settings, topic='agent-memory'), ('agent-memory', {'discovery_mode':'focused'}))

    def test_interleaving_identity_dedup_all_sources_and_partial_failures(self):
        plan={'queries':[{'id':'a','query':'a'},{'id':'b','query':'b'},{'id':'c','query':'c'}], 'per_direction':10,'merged':4}
        class FakeGitHub:
            def search_repositories(self, query, per_page):
                if query=='c': raise FetchError('fixture unavailable')
                names = ['Org/Same','org/a','org/a2'] if query=='a' else ['https://github.com/org/same.git','org/b','org/b2']
                return [{'full_name':name} for name in names]
        rows, info = collect_candidates(FakeGitHub(), plan)
        self.assertEqual([r['full_name'] for r in rows], ['org/same','org/a','org/b','org/a2'])
        self.assertEqual(len(rows[0]['discovery_sources']), 2)
        self.assertEqual(info['duplicates'], 1)
        self.assertFalse(info['all_failed']); self.assertIn('fixture', info['queries'][2]['error'])

    def test_broad_one_task_shared_budget_and_project_tags(self):
        task = self.broad(); self.work()
        row = self.queue.get(task['task_id'])
        self.assertEqual((row['status'],row['model_requests']), ('completed',2))
        record=json.loads((self.directory/'runs'/row['run_id']/'discovery.json').read_text(encoding='utf-8'))
        self.assertEqual((record['retrieved'],record['unique'],record['duplicates']), (9,3,6))
        self.assertEqual(record['screening_count'],3)
        with self.queue.store() as store:
            projects=project_catalog(store,history_records(store,self.settings.topics),tags=['fixture','工具调用'])
        self.assertEqual(len(projects),1)
        self.assertEqual({t['source'] for t in projects[0]['tags']}, {'github','assistant'})
        self.assertIn('fixture',projects[0]['description'])
        saved=projects[0]['reports'][0]
        self.assertEqual(saved['discovery_mode'],'broad')
        self.assertEqual({s['direction'] for s in saved['discovery_sources']},
                         {q['id'] for q in task['input']['discovery_plan']['queries']})

    def test_all_queries_failed_is_failed_zero_requests_and_no_old_candidates(self):
        task=self.broad()
        with mock.patch.object(DemoServices,'search_repositories',side_effect=FetchError('fixture failure')):
            self.work()
        row=self.queue.get(task['task_id'])
        self.assertEqual((row['status'],row['model_requests']), ('failed',0))
        self.assertIsNone(row['report'])
        with self.queue.store() as store:
            self.assertEqual(store.conn.execute('SELECT COUNT(*) FROM candidates').fetchone()[0],0)
        self.assertTrue(json.loads((self.directory/'runs'/row['run_id']/'discovery.json').read_text(encoding='utf-8'))['all_failed'])

    def test_empty_success_is_no_recommendation_and_partial_success_continues(self):
        task=self.broad()
        with mock.patch.object(DemoServices,'search_repositories',return_value=[]): self.work()
        row=self.queue.get(task['task_id'])
        self.assertEqual((row['status'],row['model_requests']), ('completed',0))
        self.assertEqual(row['run']['status'],'no-suitable-candidate')
        other=self.broad('fixture-partial-success')
        original=DemoServices.search_repositories
        def partial(service, query, per_page):
            if query==other['input']['discovery_plan']['queries'][0]['query']: raise FetchError('fixture gap')
            return original(service,query,per_page)
        with mock.patch.object(DemoServices,'search_repositories',partial): self.work()
        self.assertEqual(self.queue.get(other['task_id'])['model_requests'],2)

    def test_cooldown_is_project_based_across_discovery_modes(self):
        task=self.broad()
        service=DemoServices(self.settings,task['run_id'],delay=0)
        with self.queue.store() as store:
            store.start_run('old-focused',kind='fixture',started_at=now_iso(),snapshot={'topic':'agent-memory'})
            store.finish_run('old-focused',status='published',finished_at=now_iso())
            store.save_report('old-focused',report_id='old-focused',created_at=now_iso(),status='published',title='fixture',markdown_path='',html_path='',payload=service.report,validation={'ok':True})
        self.work()
        info=json.loads((self.directory/'runs'/task['run_id']/'discovery.json').read_text(encoding='utf-8'))
        self.assertEqual(info['cooldown_skipped'],1)
        self.assertEqual(info['screening_count'],2)

    def test_rejected_report_does_not_adopt_assistant_tags(self):
        task=self.broad(); self.work(scenario='rejected')
        with self.queue.store() as store:
            tags=store.conn.execute('SELECT source FROM project_tags').fetchall()
            self.assertEqual({r[0] for r in tags}, {'github'})
        self.assertEqual(self.queue.get(task['task_id'])['report']['status'],'rejected')

    def test_optional_invalid_tags_do_not_reject_body_or_remove_old_tags(self):
        service=DemoServices(self.settings,'fixture-optional-tags',delay=0)
        original=validate_report(service.report,evidence=service.evidence,target_repo=service.names[0],collected_repos=service.names)
        self.assertTrue(original.ok, original.errors)
        for invalid in ('999999 stars 92/100', {'score':92}, [None, {'label':'<script>', 'evidence_ids':['unknown']} ]):
            payload={**service.report,'archive_tags':invalid}
            check=validate_report(payload,evidence=service.evidence,target_repo=service.names[0],collected_repos=service.names)
            self.assertEqual(check.errors,original.errors)
        with self.queue.store() as store:
            for identifier,payload in [('first',service.report),('empty',{**service.report,'archive_tags':[]})]:
                store.save_evidence(identifier,service.evidence)
                store.save_report(identifier,report_id=identifier,created_at=identifier,status='published',title='fixture',markdown_path='',html_path='',payload=payload,validation={'ok':True})
            self.assertEqual(store.conn.execute('SELECT COUNT(*) FROM projects').fetchone()[0],1)
            self.assertEqual(store.conn.execute("SELECT COUNT(*) FROM project_tags WHERE source='assistant'").fetchone()[0],1)
            projects=project_catalog(store,history_records(store,self.settings.topics),keyword='target',tags=['工具调用'])
            self.assertEqual(len(projects[0]['reports']),2)

    def test_assistant_labels_reject_wrong_owner_quote_and_unsupported_term(self):
        service=DemoServices(self.settings,'fixture-tag-checks',delay=0)
        tag=service.report['archive_tags'][0]
        with self.queue.store() as store:
            for index, bad in enumerate([{**tag,'evidence_ids':['E-readme-fixture-a']}, {**tag,'quote':'this quote is fabricated'}, {**tag,'label':'GPU 加速'}]):
                run='bad-'+str(index); store.save_evidence(run,service.evidence)
                store.save_report(run,report_id=run,created_at=run,status='published',title='fixture',markdown_path='',html_path='',payload={**service.report,'archive_tags':[bad]},validation={'ok':True})
            self.assertEqual(store.conn.execute("SELECT COUNT(*) FROM project_tags WHERE source='assistant'").fetchone()[0],0)

    def test_old_database_import_does_not_reclassify_or_change_original_reports(self):
        task=self.broad(); self.work()
        with self.queue.store() as store:
            before=[tuple(row) for row in store.conn.execute('SELECT * FROM reports')]
            for table in ('project_tags','project_runs','projects'): store.conn.execute('DROP TABLE '+table)
            store.conn.commit()
        with self.queue.store() as store:
            self.assertEqual(before,[tuple(row) for row in store.conn.execute('SELECT * FROM reports')])
            self.assertEqual(store.conn.execute("SELECT COUNT(*) FROM project_tags WHERE source='assistant'").fetchone()[0],0)
            self.assertGreater(store.conn.execute("SELECT COUNT(*) FROM project_tags WHERE source='github'").fetchone()[0],0)
        self.assertEqual(self.queue.get(task['task_id'])['model_requests'],2)


class DiscoveryHttpTests(unittest.TestCase):
    setUp=processes.ProcessTests.setUp
    stop_all=processes.ProcessTests.stop_all
    start=processes.ProcessTests.start
    wait_until=processes.ProcessTests.wait_until
    get=processes.ProcessTests.get
    post=processes.ProcessTests.post
    start_web=processes.ProcessTests.start_web

    def test_http_default_submit_history_search_and_feedback(self):
        self.start_web()
        self.assertIn('帮我发现新项目', self.get('/'))
        status,location,_=self.post('/tasks',{'discovery_mode':'broad','submission_key':'fixture-http-broad-key'})
        self.assertEqual(status,303)
        task=self.queue.get(location.split('/')[-1])
        self.assertEqual(task['input']['discovery_mode'],'broad')
        self.start('worker','--once','--demo-delay','0').wait(timeout=15)
        row=self.queue.get(task['task_id']); self.assertEqual(row['status'],'completed')
        self.assertIn('工具调用',self.get('/history?'+urlencode({'tags':'fixture,工具调用'})))
        self.assertIn('没有匹配项目',self.get('/history?q=not-a-fixture-project'))
        self.assertIn('历次报告',self.get('/history?project='+row['report']['payload']['target']['repo']) if isinstance(row['report']['payload'],dict) else self.get('/history?project='+json.loads(row['report']['payload'])['target']['repo']))

    def test_default_cli_broad_and_legacy_weekly_scope_then_explicit_broad(self):
        import subprocess, sys
        from tests.test_task_processes import ROOT
        def command(*args):
            result=subprocess.run([sys.executable,'-m','learnradar.cli',*args,'--demo','--data-dir',str(self.directory),'--demo-delay','0'],cwd=ROOT,capture_output=True,timeout=30)
            self.assertEqual(result.returncode,0,result.stdout.decode('utf-8',errors='replace')+result.stderr.decode('utf-8',errors='replace'))
        command('run','--run-id','fixture-cli-broad')
        with self.queue.store() as store:
            first=json.loads(store.conn.execute("SELECT payload FROM tasks WHERE run_id='fixture-cli-broad'").fetchone()[0])
        self.assertEqual(first['discovery_mode'],'broad')
        command('weekly','--cycle','fixture-old-weekly')
        command('weekly','--cycle','fixture-new-weekly','--discovery-mode','broad')
        command('weekly','--cycle','fixture-new-weekly','--discovery-mode','broad')
        with self.queue.store() as store:
            rows=store.conn.execute('SELECT payload,budget_scope,model_requests FROM tasks').fetchall()
        self.assertEqual(len(rows),3)
        self.assertEqual([json.loads(row[0])['discovery_mode'] for row in rows],['broad','focused','broad'])
        self.assertEqual([row[1] for row in rows],['manual','weekly','weekly'])
        self.assertEqual([row[2] for row in rows],[2,2,2])
