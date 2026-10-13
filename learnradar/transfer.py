"""Offline staged restore and read-only deployment inspection; never runs tasks/mail."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import shutil
import sqlite3
import tempfile
from contextlib import closing

from .store import Store
from .auth_store import AuthStore
from .bundle_manifest import verify_inventory

REFERENCES = {'reports': ('markdown_path', 'html_path'), 'emails': ('attachment_path',)}


def inspect_data(directory, *, published_directory=None):
    def artifact_exists(value):
        path = Path(value)
        if published_directory is not None:
            # Staged restore already records final paths. Validate against the
            # staged bytes before publishing, so failed inspection is atomic.
            path = Path(directory) / path.relative_to(published_directory)
        return path.is_file()

    database = Path(directory) / 'learning-radar.sqlite'
    with closing(sqlite3.connect(database.resolve().as_uri()+'?mode=ro', uri=True)) as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        result = {}
        for table in ('runs', 'reports', 'tasks', 'cycles', 'emails', 'task_requests'):
            if table in tables:
                result[table] = dict(conn.execute(f'SELECT status,COUNT(*) FROM {table} GROUP BY status'))
        for table in ('feedback', 'web_account', 'web_sessions'):
            result[table] = conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] if table in tables else 0
        result['missing_artifact_references'] = sum(
            1 for table, columns in REFERENCES.items() if table in tables
            for column in columns for (value,) in conn.execute(f'SELECT {column} FROM {table}')
            if value and not artifact_exists(value))
        # Saved input paths can refer to external screening/candidate files. Do
        # not guess a replacement or rewrite original input/context JSON.
        result['pending_input_paths_require_review'] = 0
        if 'tasks' in tables and 'payload' in {r[1] for r in conn.execute('PRAGMA table_info(tasks)')}:
            result['pending_notifications'] = conn.execute('SELECT COUNT(*) FROM tasks WHERE notify_pending=1').fetchone()[0]
            for (raw,) in conn.execute("SELECT payload FROM tasks WHERE status IN ('queued','waiting_quota','running')"):
                options = json.loads(raw)
                if not isinstance(options, dict):
                    raise ValueError('保存的任务输入不完整；停止检查或恢复')
                result['pending_input_paths_require_review'] += sum(bool(options.get(key)) for key in ('screening_from', 'candidates_from'))
        return result


def relative_artifact(value, source_directory):
    cls = PureWindowsPath if PureWindowsPath(source_directory).drive else PurePosixPath
    source, saved = cls(source_directory), cls(value)
    try:
        relative = saved.relative_to(source) if saved.is_absolute() else saved
    except ValueError:
        raise ValueError('产物路径不属于原数据目录；未猜测或修改引用') from None
    if not relative.parts or relative.parts[0] not in ('runs', 'reports') or any(p in ('..', '.') for p in relative.parts):
        raise ValueError('产物路径不安全或未包含在 runs/reports 文件包中')
    return Path(*relative.parts)


def restore(database, bundle, destination, *, source_directory=None):
    database, bundle, destination = Path(database), Path(bundle), Path(destination).resolve()
    if destination.exists():
        raise ValueError('恢复目标必须是不存在的新目录；不会覆盖现有数据')
    manifest = json.loads((bundle/'MANIFEST.json').read_text(encoding='utf-8'))
    with database.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    if digest != manifest['database_sha256']:
        raise ValueError('数据库与文件包清单不匹配，停止恢复')
    integrity = verify_inventory(bundle, manifest)
    saved_source = manifest.get('source_data_dir')
    if source_directory and saved_source and str(source_directory) != saved_source:
        raise ValueError('显式原目录与清单不匹配')
    source_directory = str(source_directory or saved_source or '')
    if not source_directory:
        raise ValueError('旧清单缺少 source_data_dir；请显式提供 --source-data-dir，不能猜测')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.radar-restore-', dir=destination.parent) as temporary:
        stage = Path(temporary)/'data'
        stage.mkdir(mode=0o700)
        shutil.copyfile(database, stage/'learning-radar.sqlite')
        for name in ('runs', 'reports'):
            source = bundle/name
            if source.exists():
                if source.is_symlink() or any(p.is_symlink() for p in source.rglob('*')):
                    raise ValueError('文件包不能含符号链接')
                shutil.copytree(source, stage/name, symlinks=True)
        # Also verify the copied bytes, before migrations or publication.
        verify_inventory(stage, manifest)
        changed = 0
        with Store(stage/'learning-radar.sqlite') as store:
            for table, columns in REFERENCES.items():
                for column in columns:
                    for row in store.conn.execute(f'SELECT rowid,{column} FROM {table}').fetchall():
                        value = row[1]
                        if not value:
                            continue
                        relative = relative_artifact(value, source_directory)
                        artifact = stage/relative
                        if not artifact.is_file():
                            raise ValueError('备份缺少被引用的报告或附件；停止恢复，不生成替代内容')
                        # Check readability of the actual restored bytes.
                        with artifact.open('rb') as stream:
                            stream.read(1)
                        store.conn.execute(f'UPDATE {table} SET {column}=? WHERE rowid=?', (str(destination/relative), row[0]))
                        changed += 1
            store.conn.commit()
        AuthStore(stage/'learning-radar.sqlite').revoke_all()
        (stage/'RESTORE.json').write_text(json.dumps({
            'source_data_dir': source_directory, 'rebased_references': changed,
            'sessions_revoked': True, 'analysis_started': False, 'mail_sent': False,
            'backup_integrity': integrity,
        }, ensure_ascii=False, indent=2), encoding='utf-8')
        if os.name != 'nt':
            for path in stage.rglob('*'):
                path.chmod(0o700 if path.is_dir() else 0o600)
            stage.chmod(0o700)
        result = inspect_data(stage, published_directory=destination)
        result['backup_integrity'] = integrity
        stage.rename(destination)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description='离线部署检查/显式恢复，不执行任务或通知')
    sub = parser.add_subparsers(dest='command', required=True)
    check = sub.add_parser('inspect')
    check.add_argument('--data-dir', type=Path, required=True)
    recovery = sub.add_parser('restore')
    recovery.add_argument('--database', type=Path, required=True)
    recovery.add_argument('--bundle-dir', type=Path, required=True)
    recovery.add_argument('--data-dir', type=Path, required=True)
    recovery.add_argument('--source-data-dir')
    args = parser.parse_args(argv)
    try:
        result = inspect_data(args.data_dir) if args.command == 'inspect' else restore(
            args.database, args.bundle_dir, args.data_dir, source_directory=args.source_data_dir)
    except ValueError as exc:
        print('检查/恢复失败：'+str(exc))
        return 1
    except (OSError, KeyError, sqlite3.Error):
        # Do not print SQL, account records, paths embedded in data or contents.
        print('检查/恢复失败：文件或清单不完整、路径不兼容或目标已存在；未启动分析或邮件。')
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result.get('backup_integrity', {}).get('mode') == 'legacy_counts_only':
        print('兼容限制：旧清单仅核对声明的目录与文件数量；没有逐文件大小/哈希，不能充分验证内容完整性。')
    print('只检查保存记录；queued/running、待重试通知及旧输入路径需人工复核后才启动 worker/timer。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
