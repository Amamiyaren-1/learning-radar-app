"""Project identity and additive tag provenance; never rewrites reports or contexts."""
import json
import re
from .evidence import normalize_repo_name, matchable

DISPLAY_FIELDS = ('display_title', 'display_summary', 'display_sources',
                  'display_note', 'display_missing', 'repo_label',
                  'original_summary', 'original_summary_source')


def saved_report_display(row, payload):
    """Extract only readable saved display fields; never publish or rewrite."""
    from .validate import REQUIRED_TOP_LEVEL, ValidationResult
    from .report import render_markdown
    target = payload.get('target')
    raw_repo = target.get('repo') if isinstance(target, dict) else ''
    label = raw_repo.strip() if isinstance(raw_repo, str) else ''
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', label):
        label = ''
    result = {'repo_label': label, 'readable': False, 'title': '', 'summary': ''}
    if (payload.get('error') or not set(REQUIRED_TOP_LEVEL).issubset(payload)
            or not isinstance(target, dict)):
        return result
    try:
        # Same saved-output formatter as the draft page, with no I/O/adapters.
        render_markdown(report=payload, evidence=[], meta={},
                        validation=ValidationResult(ok=False), stage_usage=[])
    except Exception:
        return result
    result['readable'] = True
    titles = [value for value in (row.get('title'), payload.get('title')) if isinstance(value, str)]
    title = next((value for value in titles if re.search(r'[\u3400-\u9fff]', value)), titles[0] if titles else '')
    summary = target.get('summary_one_line')
    result['title'] = title.strip() if isinstance(title, str) else ''
    result['summary'] = summary.strip() if isinstance(summary, str) else ''
    return result


def display_fields(project):
    """Published Chinese first, then readable rejected drafts, per field."""
    from urllib.parse import quote
    chinese = re.compile(r'[\u3400-\u9fff]')
    reports = project['reports']  # existing newest-first order stays unchanged
    repo = project['repo']
    label = next((r.get('_saved_display', {}).get('repo_label') for r in reports
                  if normalize_repo_name(r.get('_saved_display', {}).get('repo_label')) == repo), repo)
    title, summary = label or '未分类', project['description']
    sources = {}
    for field in ('title', 'summary'):
        chosen = None
        for status in ('published', 'rejected'):
            chosen = next((r for r in reports if r.get('report_status') == status
                           and r.get('_saved_display', {}).get('readable')
                           and chinese.search(r['_saved_display'].get(field, ''))), None)
            if chosen:
                break
        if chosen:
            value = chosen['_saved_display'][field]
            if field == 'title': title = value
            else: summary = value
            sources[field] = {'kind': 'published' if chosen['report_status'] == 'published' else 'draft',
                              'run_id': chosen['run_id'], 'report_status': chosen['report_status'],
                              'url': '/report/' + quote(chosen['run_id'], safe='')}
        else:
            sources[field] = {'kind': 'original', 'run_id': '', 'report_status': '', 'url': ''}
    missing = [name for field, name in (('title', '中文标题'), ('summary', '中文摘要'))
               if sources[field]['kind'] == 'original']
    kinds = {source['kind'] for source in sources.values()}
    note = ('中文展示含未发布拒绝草稿；内容未核验，拒绝状态保持。' if 'draft' in kinds else
            '中文展示来自已发布保存报告；不代表项目已安装实测。' if 'published' in kinds else
            '暂无可用的保存中文内容，展示原项目资料。')
    return {'display_title': title, 'display_summary': summary, 'display_sources': sources,
            'display_note': note, 'display_missing': missing, 'repo_label': label,
            'original_summary': project['description'],
            'original_summary_source': project.get('original_summary_source',
                {'kind': 'saved', 'run_id': '', 'url': '', 'label': '保存的项目简介（来源未记录）'})}


def reports_with_project_display(reports, projects):
    """Home records keep their own run/status/title, sharing project display."""
    by_run = {r['run_id']: p for p in projects for r in p['reports']}
    return [{**row, **({key: by_run[row['run_id']][key] for key in DISPLAY_FIELDS}
                      if row['run_id'] in by_run else {}),
             **({'repo': by_run[row['run_id']]['repo']} if row['run_id'] in by_run else {})}
            for row in reports]

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
    # Locate the persisted original metadata without fetching GitHub or
    # treating an assistant summary as a GitHub description.
    for row in store.conn.execute('SELECT run_id,full_name,description FROM candidates ORDER BY rowid DESC'):
        repo = normalize_repo_name(row['full_name'])
        project = groups.get(repo)
        if (project and 'original_summary_source' not in project
                and project['description'] and row['description'] == project['description']):
            project['original_summary_source'] = {'kind': 'github', 'run_id': row['run_id'],
                'url': 'https://github.com/' + (row['full_name'] if re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', row['full_name']) else repo),
                'label': 'GitHub 原始简介（保存的项目方元数据）'}
    for project in groups.values():
        project.update(display_fields(project))
    wanted = {clean_label(tag) for tag in tags}
    keyword = keyword.strip().casefold()
    return [project for project in groups.values()
            if (not keyword or keyword in ' '.join((project['repo'], project['repo_label'],
                project['description'], project['display_title'], project['display_summary'])).casefold())
            and wanted.issubset({tag['label'] for tag in project['tags']})]
