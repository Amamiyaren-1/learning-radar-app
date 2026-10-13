"""UI contract v1: bounded, pure projections; no storage or business mutations."""
from urllib.parse import urlencode, quote

CONTRACT_VERSION = '1'
MAX_ITEMS = 40


def text(value, limit=300):
    return str(value or '')[:limit]


def link(prefix, identity):
    return prefix + quote(text(identity, 200), safe='')


def envelope(kind, *, fixture=False, **data):
    return {'contract_version': CONTRACT_VERSION, 'page_kind': kind,
            'fixture': bool(fixture), 'data': data}


def task_actions(task):
    weekly = bool(task.get('cycle_id')) or task.get('budget_scope') == 'weekly'
    status = task.get('status')
    email = task.get('email') or {}
    return {'cancel': not weekly and status in ('queued', 'waiting_quota') and not task.get('model_requests'),
            'retry_email': status in ('completed', 'failed', 'interrupted') and bool(task.get('run'))
                           and email.get('status') in (None, '', 'failed', 'queued')}


def task_summary(task):
    actions = task_actions(task)
    return {'task_id': text(task.get('task_id'), 200), 'run_id': text(task.get('run_id'), 200),
            'url': link('/task/', task.get('task_id')), 'status': text(task.get('status'), 80),
            'phase': text(task.get('phase'), 80), 'topic': text(task.get('topic'), 80),
            'discovery_mode': text((task.get('input') or {}).get('discovery_mode'), 20),
            'weekly': bool(task.get('cycle_id')) or task.get('budget_scope') == 'weekly',
            'model_requests': task.get('model_requests', 0), 'uncertain': bool(task.get('uncertain')),
            'report_status': text((task.get('report') or {}).get('status'), 80),
            'email_status': text((task.get('email') or {}).get('status'), 80),
            'actions': {name: {'available': bool(allowed), 'method': 'POST',
                              'url': link('/task/', task.get('task_id')) + '/' + name.replace('_', '-')}
                        for name, allowed in actions.items()}}


def report_summary(row):
    return {**display_projection(row), 'run_id': text(row.get('run_id'), 200), 'url': link('/report/', row.get('run_id')),
            'title': text(row.get('title')), 'status': text(row.get('status') or row.get('report_status'), 80),
            'date': text(row.get('created_at') or row.get('date'), 80)}


def display_projection(row):
    """Allowlist only presentation text/provenance, never saved payloads."""
    sources = row.get('display_sources') or {}
    original = row.get('original_summary_source') or {}
    return {'display_title': text(row.get('display_title'), 300),
            'display_summary': text(row.get('display_summary'), 500),
            'display_note': text(row.get('display_note'), 200),
            'display_missing': [text(x, 40) for x in (row.get('display_missing') or [])[:4]],
            'repo': text(row.get('repo'), 200), 'repo_label': text(row.get('repo_label'), 200),
            'display_sources': {field: {key: text((sources.get(field) or {}).get(key), 200)
                               for key in ('kind', 'run_id', 'report_status', 'url')}
                                for field in ('title', 'summary')},
            'original_summary': text(row.get('original_summary'), 1000),
            'original_summary_source': {key: text(original.get(key), 200)
                                        for key in ('kind', 'run_id', 'url', 'label')}}


def home_data(tasks, reports):
    return {'tasks': [task_summary(t) for t in tasks[:MAX_ITEMS]],
            'reports': [report_summary(r) for r in reports[:MAX_ITEMS]],
            'task_count': len(tasks), 'report_count': len(reports),
            'truncated': len(tasks) > MAX_ITEMS or len(reports) > MAX_ITEMS,
            'submit': {'available': True, 'method': 'POST', 'url': '/tasks', 'default_mode': 'broad'}}


def project_summary(project):
    reports = project['reports']
    return {**display_projection(project), 'repo': text(project['repo'], 200), 'url': '/history?' + urlencode({'project': project['repo']}),
            'description': text(project['description'], 500),
            'tags': [{'label': text(t['label'], 32), 'source': text(t['source'], 20)} for t in project['tags'][:MAX_ITEMS]],
            'reports': [report_summary(r) for r in reports[:MAX_ITEMS]], 'report_count': len(reports),
            'truncated': len(reports) > MAX_ITEMS or len(project['tags']) > MAX_ITEMS}


def projects_data(projects, selected=None):
    visible = projects if selected is None else [p for p in projects if p['repo'] == selected]
    return {'projects': [project_summary(p) for p in visible[:MAX_ITEMS]],
            'project_count': len(visible), 'truncated': len(visible) > MAX_ITEMS, 'tag_match': 'all'}


def report_data(view, *, saved=False):
    return {'report': {**report_summary(view['row']), 'repo': text(view['repo'], 200),
                       'document_kind': view['document_kind'],
                       'errors': [text(e, 500) for e in view['validation'].errors[:MAX_ITEMS]],
                       'warnings': [text(e, 500) for e in view['validation'].warnings[:MAX_ITEMS]]},
            'task_status': text(dict(view['task'])['status'], 80) if view.get('task') else '',
            'email_status': text((view.get('email') or {}).get('status'), 80),
            'feedback': {'available': bool(view['can_feedback']), 'saved': bool(saved),
                         'url': link('/feedback/', view['row']['run_id']), 'method': 'POST'}}
