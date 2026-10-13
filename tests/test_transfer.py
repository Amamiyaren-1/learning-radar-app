"""Isolated cross-platform restore: actual artifacts, no model/mail execution."""
import argparse
import contextlib
import dataclasses
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest
from unittest import mock

from learnradar.auth import AuthService
from learnradar.cloud import cmd_backup
from learnradar.config import ConfigError, load_settings
from learnradar.store import Store
from learnradar.tasks import TaskQueue, WorkerLock
from learnradar.transfer import inspect_data, relative_artifact, restore, main as transfer_main
from learnradar.bundle_manifest import verify_inventory
from learnradar.web import build_report_view
from learnradar.web_config import WebConfig, load_web_config


class TransferTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name); self.source=self.root/'source'
        self.queue=TaskQueue(self.source,demo=True)
        self.settings=dataclasses.replace(load_settings(local_overrides=False),app={'data_dir':str(self.source)})
        self.database=self.root/'saved.sqlite'; self.bundle=self.root/'bundle'
        (self.source/'reports').mkdir()
        (self.source/'runs'/'fixture').mkdir(parents=True)
        (self.source/'reports'/'fixture.md').write_text('# fixture saved published bytes',encoding='utf-8')
        (self.source/'reports'/'fixture.html').write_text('<h1>fixture</h1>',encoding='utf-8')
        (self.source/'runs'/'fixture'/'email-report.md').write_text('fixture public attachment',encoding='utf-8')
        (self.source/'runs'/'fixture'/'run-context.json').write_text(json.dumps({'topic':'agent-memory','target':{'repo':'example-offline/target'}}),encoding='utf-8')
        (self.source/'runs'/'fixture'/'evidence.json').write_text('["fixture evidence"]',encoding='utf-8')
        (self.source/'runs'/'fixture'/'usage.json').write_text('{"fixture":true,"calls":2}',encoding='utf-8')
        payload=json.loads((Path(__file__).parent/'fixtures/offline_analysis.json').read_text(encoding='utf-8'))
        with self.queue.store() as store:
            store.start_run('fixture',kind='fixture',started_at='fixture',snapshot={'topic':'agent-memory'})
            store.finish_run('fixture',status='published',finished_at='fixture')
            store.save_report('fixture',report_id='fixture',created_at='fixture',status='published',title='fixture',markdown_path=str(self.source/'reports'/'fixture.md'),html_path=str(self.source/'reports'/'fixture.html'),payload=payload,validation={'ok':True})
            store.save_feedback('fixture','example-offline/target',{'want_more':'no'},'fixture feedback',now='fixture')
            store.save_cycle('fixture-cycle',topic='agent-memory',status='completed',run_id='fixture',started_at='fixture')
            store.create_email('fixture-email',run_id='fixture',cycle_id='fixture-cycle',kind='report',recipient='fixture@example.invalid',subject='fixture',body='fixture',attachment_path=str(self.source/'runs'/'fixture'/'email-report.md'),status='unknown')
        auth=AuthService(self.queue.path,WebConfig('loopback'),demo=True)
        auth.store.create_session(auth.clock(),auth.config,authenticated=True)

    def backup(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cmd_backup(self.settings,argparse.Namespace(out=str(self.database),bundle_dir=str(self.bundle))),0)

    def assert_readable(self, destination):
        result=inspect_data(destination)
        self.assertEqual(result['missing_artifact_references'],0)
        self.assertEqual(result['web_sessions'],0)
        with Store(destination/'learning-radar.sqlite') as store:
            self.assertEqual(store.get_cycle('fixture-cycle')['status'],'completed')
            self.assertEqual(store.get_email('fixture-email')['status'],'unknown')
            self.assertEqual(store.get_feedback('fixture')['note'],'fixture feedback')
            self.assertEqual(build_report_view(store,'fixture')['document'],'# fixture saved published bytes')
            self.assertEqual(Path(store.get_email('fixture-email')['attachment_path']).read_text(encoding='utf-8'),'fixture public attachment')
            self.assertEqual(store.get_report('fixture')['status'],'published')

    def saved_bytes(self):
        return {str(path.relative_to(self.root)): path.read_bytes()
                for directory in (self.source, self.bundle) for path in directory.rglob('*') if path.is_file()} | {
                    'saved.sqlite': self.database.read_bytes()}

    def assert_restore_rejected(self, message):
        before = self.saved_bytes()
        destination = self.root/'restored'
        with self.assertRaisesRegex(ValueError, message):
            restore(self.database, self.bundle, destination)
        self.assertFalse(destination.exists())
        self.assertEqual(list(self.root.glob('.radar-restore-*')), [])
        self.assertEqual(self.saved_bytes(), before)

    def legacy_manifest(self):
        path = self.bundle/'MANIFEST.json'
        manifest = json.loads(path.read_text(encoding='utf-8'))
        manifest.pop('files')
        manifest.pop('file_manifest_version')
        path.write_text(json.dumps(manifest), encoding='utf-8')

    def test_new_backup_records_complete_relative_inventory(self):
        self.backup()
        manifest = json.loads((self.bundle/'MANIFEST.json').read_text(encoding='utf-8'))
        actual = {path.relative_to(self.source).as_posix(): path for name in ('runs', 'reports')
                  for path in (self.source/name).rglob('*') if path.is_file()}
        self.assertEqual(manifest['file_manifest_version'], 1)
        self.assertEqual({entry['path'] for entry in manifest['files']}, set(actual))
        for entry in manifest['files']:
            data = actual[entry['path']].read_bytes()
            self.assertEqual(entry['size_bytes'], len(data))
            self.assertEqual(entry['sha256'], hashlib.sha256(data).hexdigest())
        self.assertEqual(verify_inventory(self.bundle, manifest)['mode'], 'files_sha256')

    def test_missing_entire_runs_directory_is_rejected_before_database_references(self):
        # No directly referenced attachment: the directory must still be required.
        with self.queue.store() as store:
            store.conn.execute("UPDATE emails SET attachment_path='' ")
            store.conn.commit()
        self.backup()
        shutil.rmtree(self.bundle/'runs')
        self.assert_restore_rejected('缺少清单声明的目录')

    def test_missing_unreferenced_context_evidence_and_usage_are_rejected(self):
        self.backup()
        for name in ('evidence.json', 'run-context.json', 'usage.json'):
            with self.subTest(name=name):
                path = self.bundle/'runs'/'fixture'/name
                data = path.read_bytes()
                path.unlink()
                self.assert_restore_rejected('数量.*不匹配')
                path.write_bytes(data)

    def test_same_size_content_corruption_is_rejected(self):
        self.backup()
        for name in ('evidence.json', 'run-context.json', 'usage.json'):
            with self.subTest(name=name):
                path = self.bundle/'runs'/'fixture'/name
                data = path.read_bytes()
                path.write_bytes(b'x'+data[1:])
                self.assert_restore_rejected('完整性验证失败')
                path.write_bytes(data)

    def test_legitimate_old_run_without_evidence_or_usage_is_not_invented(self):
        for name in ('evidence.json', 'usage.json'):
            (self.source/'runs'/'fixture'/name).unlink()
        (self.source/'runs'/'legitimate-empty-old-run').mkdir()
        self.backup()
        destination = self.root/'restored'
        before = self.saved_bytes()
        result = restore(self.database, self.bundle, destination)
        self.assertEqual(result['backup_integrity']['mode'], 'files_sha256')
        for name in ('evidence.json', 'usage.json'):
            self.assertFalse((destination/'runs'/'fixture'/name).exists())
        self.assertEqual(before, self.saved_bytes())
        self.assert_readable(destination)

    def test_legacy_manifest_checks_directory_and_counts(self):
        self.backup()
        self.legacy_manifest()
        path = self.bundle/'runs'/'fixture'/'evidence.json'
        data = path.read_bytes()
        path.unlink()
        self.assert_restore_rejected('数量.*不匹配')
        path.write_bytes(data)
        shutil.rmtree(self.bundle/'runs')
        self.assert_restore_rejected('缺少清单声明的目录')

    def test_legacy_restore_warns_that_content_integrity_is_not_verified(self):
        self.backup()
        self.legacy_manifest()
        # Same-size corruption cannot be detected by a legacy count-only manifest.
        path = self.bundle/'runs'/'fixture'/'usage.json'
        path.write_bytes(b'x'+path.read_bytes()[1:])
        destination = self.root/'restored'
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = transfer_main(['restore', '--database', str(self.database), '--bundle-dir', str(self.bundle),
                                  '--data-dir', str(destination)])
        self.assertEqual(code, 0)
        self.assertIn('兼容限制', output.getvalue())
        self.assertIn('不能充分验证', output.getvalue())
        record = json.loads((destination/'RESTORE.json').read_text(encoding='utf-8'))
        self.assertEqual(record['backup_integrity']['mode'], 'legacy_counts_only')
        self.assert_readable(destination)

    def test_copied_restore_files_are_verified_before_publication(self):
        self.backup()
        copytree = shutil.copytree
        def damage_copy(source, target, *args, **kwargs):
            result = copytree(source, target, *args, **kwargs)
            if Path(source).name == 'runs':
                path = Path(target)/'fixture'/'usage.json'
                path.write_bytes(b'x'+path.read_bytes()[1:])
            return result
        with mock.patch('learnradar.transfer.shutil.copytree', side_effect=damage_copy):
            self.assert_restore_rejected('完整性验证失败')

    def test_backup_does_not_write_manifest_when_copied_file_is_damaged(self):
        copytree = shutil.copytree
        def damage_copy(source, target, *args, **kwargs):
            result = copytree(source, target, *args, **kwargs)
            if Path(source).name == 'runs':
                path = Path(target)/'fixture'/'usage.json'
                path.write_bytes(b'x'+path.read_bytes()[1:])
            return result
        with mock.patch('learnradar.cloud.shutil.copytree', side_effect=damage_copy), contextlib.redirect_stdout(io.StringIO()):
            code = cmd_backup(self.settings, argparse.Namespace(out=str(self.database), bundle_dir=str(self.bundle)))
        self.assertNotEqual(code, 0)
        self.assertFalse((self.bundle/'MANIFEST.json').exists())

    def test_new_manifest_cannot_silently_downgrade_and_rejects_unsafe_paths(self):
        self.backup()
        path = self.bundle/'MANIFEST.json'
        original = json.loads(path.read_text(encoding='utf-8'))
        mutations = []
        missing = dict(original); missing.pop('files'); mutations.append(missing)
        unsafe = json.loads(json.dumps(original)); unsafe['files'][0]['path'] = '../private'; mutations.append(unsafe)
        count = dict(original); count['included'] = {**original['included'], 'runs': 0}; mutations.append(count)
        for changed in mutations:
            with self.subTest(kind=list(changed)):
                path.write_text(json.dumps(changed), encoding='utf-8')
                self.assert_restore_rejected('无效|不安全|不匹配')

    def test_nonempty_backup_directory_is_not_overwritten(self):
        self.backup()
        before = self.saved_bytes()
        with contextlib.redirect_stdout(io.StringIO()):
            code = cmd_backup(self.settings, argparse.Namespace(out=str(self.database), bundle_dir=str(self.bundle)))
        self.assertNotEqual(code, 0)
        self.assertEqual(before, self.saved_bytes())

    def test_old_schema_restore_rebases_and_reads_after_original_removed(self):
        with contextlib.closing(sqlite3.connect(self.queue.path)) as conn, conn:
            for table in ('tasks','task_requests','task_events','task_environment','web_account','web_sessions'):
                conn.execute('DROP TABLE '+table)
            conn.execute('ALTER TABLE cycles DROP COLUMN task_id')
        self.backup()
        destination=self.root/'restored'
        restore(self.database,self.bundle,destination)
        shutil.rmtree(self.source)
        self.assert_readable(destination)
        with Store(destination/'learning-radar.sqlite') as store:
            self.assertEqual(store.conn.execute('SELECT COUNT(*) FROM tasks').fetchone()[0],0)

    def test_windows_paths_explicitly_rebased_preserving_usage_and_pending_tasks(self):
        task=self.queue.submit('agent-memory','fixture-transfer-request')
        self.queue.claim('fixture'); self.queue.begin_request(task['task_id'])
        windows=r'<repository>'
        with self.queue.store() as store:
            store.conn.execute('UPDATE reports SET markdown_path=?,html_path=?',(windows+r'\reports\fixture.md',windows+r'\reports\fixture.html'))
            store.conn.execute('UPDATE emails SET attachment_path=?',(windows+r'\runs\fixture\email-report.md',))
            store.conn.commit()
        self.backup()
        manifest=json.loads((self.bundle/'MANIFEST.json').read_text(encoding='utf-8'))
        manifest['source_data_dir']=windows
        (self.bundle/'MANIFEST.json').write_text(json.dumps(manifest),encoding='utf-8')
        destination=self.root/'restored'
        restore(self.database,self.bundle,destination)
        shutil.rmtree(self.source)
        self.assert_readable(destination)
        queue=TaskQueue(destination,demo=True)
        row=queue.get(task['task_id'])
        self.assertEqual(row['status'],'running')
        self.assertEqual((row['model_requests'],row['uncertain']),(1,1))
        self.assertEqual(row['requests'][0]['status'],'pending')
        lock=WorkerLock(destination); self.assertTrue(lock.acquire())
        try: queue.recover(lock)
        finally: lock.release()
        row=queue.get(task['task_id'])
        self.assertEqual(row['status'],'interrupted')
        self.assertEqual(row['model_requests'],1)
        self.assertEqual(row['requests'][0]['status'],'unknown')
        self.assertEqual(row['email'],None)

    def test_missing_artifact_hash_mismatch_and_existing_destination_stop(self):
        self.backup()
        destination=self.root/'restored'
        (self.bundle/'reports'/'fixture.md').unlink()
        with self.assertRaisesRegex(ValueError,'缺少'):restore(self.database,self.bundle,destination)
        self.assertFalse(destination.exists())
        with self.database.open('ab') as stream:stream.write(b'fixture-mismatch')
        with self.assertRaisesRegex(ValueError,'不匹配'):restore(self.database,self.bundle,destination)
        destination.mkdir()
        with self.assertRaisesRegex(ValueError,'不会覆盖'):restore(self.database,self.bundle,destination)

    def test_read_only_inspection_does_not_migrate_old_database(self):
        with contextlib.closing(sqlite3.connect(self.queue.path)) as conn, conn:
            conn.execute('DROP TABLE web_account');conn.execute('DROP TABLE web_sessions')
        before=self.queue.path.read_bytes()
        self.assertEqual(inspect_data(self.source)['web_account'],0)
        self.assertEqual(before,self.queue.path.read_bytes())

    def test_failed_final_inspection_does_not_publish_restore(self):
        task = self.queue.submit('agent-memory', 'fixture-malformed-input')
        with self.queue.store() as store:
            store.conn.execute('UPDATE tasks SET payload=? WHERE task_id=?', ('[]', task['task_id']))
            store.conn.commit()
        self.backup()
        destination = self.root / 'restored'
        with self.assertRaisesRegex(ValueError, '输入不完整'):
            restore(self.database, self.bundle, destination)
        self.assertFalse(destination.exists())
        self.assertEqual(list(self.root.glob('.radar-restore-*')), [])
        self.assertTrue(self.source.exists())

    def test_backup_refuses_live_execution_lock(self):
        lock=WorkerLock(self.source); self.assertTrue(lock.acquire())
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertNotEqual(cmd_backup(self.settings,argparse.Namespace(out=str(self.database),bundle_dir=str(self.bundle))),0)
        finally:lock.release()
        self.assertFalse(self.database.exists())

    def test_path_escape_and_unknown_source_are_rejected(self):
        for value in ('/other/report.md', '../reports/report.md','runs/../../secret','config/profile.toml'):
            with self.assertRaises(ValueError):relative_artifact(value,'/old/data')
        self.backup()
        manifest=json.loads((self.bundle/'MANIFEST.json').read_text(encoding='utf-8'))
        manifest.pop('source_data_dir')
        (self.bundle/'MANIFEST.json').write_text(json.dumps(manifest),encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'不能猜测'):restore(self.database,self.bundle,self.root/'restored')
        restore(self.database,self.bundle,self.root/'restored',source_directory=str(self.source))

    def test_external_configuration_and_demo_isolation(self):
        external=self.root/'settings.toml'; external.write_text('[app]\ndata_dir="/var/lib/learning-radar"',encoding='utf-8')
        web=self.root/'web.toml';web.write_text((Path(__file__).resolve().parents[1]/'config/web.example.toml').read_text(encoding='utf-8'),encoding='utf-8')
        profile=self.root/'profile.toml'
        with mock.patch.dict(os.environ,{'LEARNRADAR_SETTINGS_LOCAL':str(external),'LEARNRADAR_PROFILE':str(profile),'LEARNRADAR_WEB_CONFIG':str(web)}),mock.patch('learnradar.config.load_dotenv'):
            settings=load_settings();self.assertEqual(settings.app['data_dir'],'/var/lib/learning-radar')
            self.assertEqual(settings.profile_path,profile)
            self.assertTrue(load_web_config(settings).secure)
            self.assertFalse(load_web_config(load_settings(local_overrides=False),demo=True).secure)
            self.assertNotEqual(load_settings(local_overrides=False).profile_path,profile)
            external.unlink()
            with self.assertRaises(ConfigError):load_settings()
            web.unlink()
            with self.assertRaises(ConfigError):load_web_config(settings)


if __name__=='__main__':unittest.main()
