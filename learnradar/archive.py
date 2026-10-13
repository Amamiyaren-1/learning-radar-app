"""Project identity and additive tag provenance; never rewrites reports or contexts."""
import json
import re
from .evidence import normalize_repo_name, matchable

SCHEMA = '''
CREATE TABLE IF NOT EXISTS projects(repo TEXT PRIMARY KEY, description TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS project_runs(run_id TEXT PRIMARY KEY, repo TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS project_tags(
 repo TEXT NOT NULL, label TEXT NOT NULL, source TEXT NOT NULL, run_id TEXT NOT NULL,
 basis TEXT NOT NULL, evidence_ids TEXT NOT NULL, quote TEXT NOT NULL DEFAULT '',
 PRIMARY KEY(repo,label,source,run_id));
'''


def object_value(raw):
    try:
        value = json.loads(raw or '{}')
        return value if isinstance(value, dict) else {}
    except (ValueError, TypeError):
        return {}


def clean_label(value):
    if not isinstance(value, str):
        return ''
    value = ' '.join(value.split()).casefold()
    return value if re.fullmatch(r'[\w][\w +./-]{0,31}', value) else ''


def archive_report(conn, row, *, assistant=False):
    payload = object_value(row['payload'])
    target = payload.get('target')
    repo = normalize_repo_name(target.get('repo')) if isinstance(target, dict) else ''
    if not re.fullmatch(r'[a-z0-9_.-]+/[a-z0-9_.-]+', repo):
        return
    run = row['run_id']
    conn.execute('INSERT OR IGNORE INTO projects(repo) VALUES(?)', (repo,))
    summary = target.get('summary_one_line')
    if row['status'] == 'published' and isinstance(summary, str) and summary.strip():
        conn.execute("UPDATE projects SET description=? WHERE repo=? AND description=''", (summary[:1000],repo))
    conn.execute('INSERT OR IGNORE INTO project_runs VALUES(?,?)', (run, repo))
    evidence = {}
    metadata = []
    for raw in conn.execute('SELECT payload FROM evidence WHERE run_id=?', (run,)):
        item = object_value(raw[0])
        if normalize_repo_name(item.get('owner')) != repo:
            continue
        evidence[item.get('evidence_id')] = item
        if item.get('kind') == 'github_repo' and isinstance(item.get('extra'), dict):
            metadata.append((item['extra'], [item['evidence_id']]))
    for raw in conn.execute('SELECT full_name,raw FROM candidates WHERE run_id=?', (run,)):
        if normalize_repo_name(raw[0]) == repo:
            metadata.append((object_value(raw[1]), []))
    for facts, ids in metadata:
        description = facts.get('description')
        if isinstance(description, str) and description.strip():
            conn.execute('UPDATE projects SET description=? WHERE repo=?', (description[:1000], repo))
        tags = facts.get('topics')
        if not isinstance(tags, list):
            continue
        for raw in tags[:20]:
            label = clean_label(raw)
            if label:
                conn.execute('INSERT OR IGNORE INTO project_tags VALUES(?,?,?,?,?,?,?)',
                    (repo,label,'github',run,'GitHub topics：项目方元数据，未独立核验',json.dumps(ids),''))
    # Old outputs are never retrospectively promoted to assistant classification.
    if not assistant or row['status'] != 'published' or object_value(row['validation']).get('ok') is not True:
        return
    tags = payload.get('archive_tags')
    if not isinstance(tags, list):
        return
    adopted = set()
    for item in tags[:20]:
        if not isinstance(item, dict):
            continue
        label = clean_label(item.get('label'))
        basis, quote, ids = item.get('basis'), item.get('quote'), item.get('evidence_ids')
        if (not label or label in adopted or not isinstance(basis,str) or not 1 <= len(basis.strip()) <= 160
                or not isinstance(quote,str) or not 5 <= len(quote) <= 500
                or not isinstance(ids,list) or not 1 <= len(ids) <= 3
                or any(not isinstance(key,str) or key not in evidence for key in ids)):
            continue
        normalized = matchable(quote)
        if not normalized or matchable(label) not in normalized or not any(
                normalized in matchable(evidence[key].get('excerpt') or '') for key in ids):
            continue
        conn.execute('INSERT OR IGNORE INTO project_tags VALUES(?,?,?,?,?,?,?)',
            (repo,label,'assistant',run,basis.strip(),json.dumps(ids),quote))
        adopted.add(label)
        if len(adopted) >= 6:
            break


def import_old_reports(conn):
    rows = conn.execute('SELECT * FROM reports WHERE run_id NOT IN (SELECT run_id FROM project_runs)').fetchall()
    for row in rows:
        archive_report(conn, row)


def project_catalog(store, records, *, keyword='', tags=()):
    groups = {}
    for row in records:
        repo = row['project']
        groups.setdefault(repo, {'repo':repo, 'description':'', 'tags':[], 'reports':[]})['reports'].append(row)
    for row in store.conn.execute('SELECT * FROM projects'):
        if row['repo'] in groups:
            groups[row['repo']]['description'] = row['description']
    for row in store.conn.execute('SELECT * FROM project_tags ORDER BY label,source,run_id'):
        if row['repo'] in groups:
            groups[row['repo']]['tags'].append(dict(row))
    wanted = {clean_label(tag) for tag in tags}
    keyword = keyword.strip().casefold()
    return [project for project in groups.values()
            if (not keyword or keyword in (project['repo']+' '+project['description']).casefold())
            and wanted.issubset({tag['label'] for tag in project['tags']})]
