"""Read-only saved Chinese presentation, canonical identity and safe fallback."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from learnradar.archive import project_catalog, reports_with_project_display
from learnradar.archive_web import render_projects
from learnradar.browse import history_records
from learnradar.tasks import TaskQueue
from learnradar.ui_projection import project_summary, home_data
from learnradar.ui_shell import safe_json


class ProjectDisplayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.queue = TaskQueue(self.directory, demo=True)
        self.store = self.queue.store()
        self.base = json.loads((Path(__file__).parent/'fixtures/offline_analysis.json').read_text(encoding='utf-8'))

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def save(self, run, title, summary, *, status='published', date='2026-10-09', repo='FixtureCase/Project', corrupt=None):
        payload = copy.deepcopy(self.base)
        payload['title'] = title
        payload['target'].update(repo=repo, summary_one_line=summary)
        if corrupt is not None:
            payload.update(corrupt)
        self.store.start_run(run, kind='fixture', started_at=date, snapshot={'fixture':True})
        self.store.save_report(run, report_id=run, created_at=date, status=status, title=title,
            markdown_path='', html_path='', payload=payload, validation={'ok':status=='published'})
        self.store.save_candidates(run, [{'full_name':repo, 'description':'Original GitHub description', 'topics':['fixture','group-a']}], role='target')
        from learnradar.archive import archive_report
        archive_report(self.store.conn, self.store.conn.execute('SELECT * FROM reports WHERE run_id=?',(run,)).fetchone())
        self.store.conn.commit()
        folder = self.directory/'runs'/run
        folder.mkdir(parents=True, exist_ok=True)
        (folder/'run-context.json').write_text(json.dumps({'target':repo,'fixture':True}), encoding='utf-8')

    def catalog(self, **kwargs):
        return project_catalog(self.store, history_records(self.store, []), **kwargs)

    def test_published_priority_home_agreement_identity_and_order(self):
        self.save('fixture-old', 'fixture 已发布中文标题', 'fixture 已发布中文摘要')
        self.save('fixture-new', 'fixture 新拒绝中文标题', 'fixture 新拒绝中文摘要', status='rejected', date='2026-10-12')
        p = self.catalog()[0]
        self.assertEqual(p['display_title'],'fixture 已发布中文标题')
        self.assertEqual(p['display_summary'],'fixture 已发布中文摘要')
        self.assertEqual(p['repo'], 'fixturecase/project')
        self.assertEqual(p['repo_label'], 'FixtureCase/Project')
        self.assertEqual([r['run_id'] for r in p['reports']], ['fixture-new','fixture-old'])
        home = reports_with_project_display(self.store.list_reports(), [p])
        self.assertEqual(home[0]['run_id'], 'fixture-new')
        self.assertEqual(home[0]['status'], 'rejected')
        self.assertEqual(home[0]['title'], 'fixture 新拒绝中文标题')
        self.assertEqual(home_data([], home)['reports'][0]['display_title'], project_summary(p)['display_title'])

    def test_draft_only_is_explicit_and_original_metadata_survives(self):
        self.save('fixture-draft','fixture 拒绝中文标题','fixture 草稿中文摘要',status='rejected')
        p = self.catalog()[0]
        self.assertEqual(p['display_sources']['title']['kind'],'draft')
        self.assertEqual(p['display_sources']['summary']['report_status'],'rejected')
        self.assertIn('未发布拒绝草稿',p['display_note'])
        self.assertEqual(p['original_summary'],'Original GitHub description')
        self.assertEqual(p['original_summary_source']['kind'],'github')
        self.assertEqual(p['original_summary_source']['url'],'https://github.com/FixtureCase/Project')
        html = render_projects([p],{})
        self.assertIn('项目原文简介',html)
        self.assertIn('Original GitHub description',html)
        self.assertIn('未发布拒绝草稿',html)

    def test_missing_fields_fall_back_independently(self):
        self.save('fixture-missing','fixture 中文标题','')
        p = self.catalog()[0]
        self.assertEqual(p['display_title'],'fixture 中文标题')
        self.assertEqual(p['display_summary'],'Original GitHub description')
        self.assertEqual(p['display_missing'],['中文摘要'])
        self.save('fixture-original','Original English title','Original English summary',repo='FixtureCase/Original')
        original = next(p for p in self.catalog() if p['repo']=='fixturecase/original')
        self.assertEqual(original['display_title'],'FixtureCase/Original')
        self.assertEqual(original['display_missing'],['中文标题','中文摘要'])

    def test_broken_payload_and_render_failure_cannot_supply_chinese(self):
        self.save('fixture-good','fixture 有效标题','fixture 有效摘要')
        self.save('fixture-bad','fixture 损坏标题','fixture 损坏摘要',date='2026-10-12',corrupt={'highlight':'wrong type'})
        self.assertEqual(self.catalog()[0]['display_title'],'fixture 有效标题')
        self.store.conn.execute("UPDATE reports SET payload='not-json' WHERE run_id='fixture-bad'")
        self.store.conn.commit()
        p = self.catalog()[0]
        self.assertEqual(p['display_title'],'fixture 有效标题')
        self.assertEqual([r['run_id'] for r in p['reports']],['fixture-bad','fixture-good'])

    def test_payload_title_chinese_and_search_preserve_repo_and_tag_matching(self):
        self.save('fixture-search','English stored title','fixture 统一中文摘要')
        payload = copy.deepcopy(self.base)
        payload['title']='fixture 已有中文原题'
        payload['target'].update(repo='FixtureCase/Project',summary_one_line='fixture 统一中文摘要')
        self.store.conn.execute('UPDATE reports SET payload=? WHERE run_id=?',(json.dumps(payload,ensure_ascii=False),'fixture-search'))
        self.store.conn.commit()
        for keyword in ('已有中文原题','统一中文摘要','FIXTURECASE/PROJECT','Original GitHub'):
            self.assertEqual(len(self.catalog(keyword=keyword,tags=['fixture','group-a'])),1)
        self.assertEqual(self.catalog(tags=['fixture','missing']),[])
        self.assertEqual(self.catalog(keyword='无匹配'),[])

    def test_escape_projection_allowlist_and_no_business_mutation(self):
        self.save('fixture-escape','fixture 中文<script>alert(1)</script>','fixture 中文摘要<img src=x onerror=alert(1)>')
        before = list(self.store.conn.execute('SELECT repo,description FROM projects'))
        rows = list(self.store.conn.execute('SELECT run_id,status,payload FROM reports'))
        changes = self.store.conn.total_changes
        p = self.catalog()[0]
        native = render_projects([p],{'project':['fixturecase/project']})
        self.assertIn('&lt;script&gt;alert(1)&lt;/script&gt;',native)
        self.assertNotIn('<script>alert(1)</script>',native)
        raw = safe_json(project_summary(p))
        self.assertNotIn('<',raw)
        self.assertNotIn('_saved_display',raw)
        self.assertEqual(json.loads(raw)['display_title'],p['display_title'])
        self.assertEqual(self.store.conn.total_changes,changes)
        self.assertEqual(list(self.store.conn.execute('SELECT repo,description FROM projects')),before)
        self.assertEqual(list(self.store.conn.execute('SELECT run_id,status,payload FROM reports')),rows)


if __name__=='__main__':
    unittest.main()
