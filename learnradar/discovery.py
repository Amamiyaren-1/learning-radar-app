"""Bounded, reproducible discovery input and round-robin candidate coverage."""
from .config import ConfigError
from .evidence import normalize_repo_name
from .net import FetchError


def request_options(settings, *, mode=None, topic=None):
    mode = mode or ('focused' if topic else 'broad')
    if mode not in ('broad', 'focused'):
        raise ConfigError('发现模式必须是 broad 或 focused')
    if mode == 'focused':
        chosen = next((t for t in settings.topics if t['id'] == topic), None)
        if chosen is None:
            raise ConfigError('请选择已有方向')
        return topic, {'discovery_mode': mode}
    config = settings.discovery
    pool = [{'id': t['id'], 'label': t.get('label', t['id']), 'query': t['github_query']}
            for t in settings.topics if t.get('github_query')]
    if not pool:
        raise ConfigError('发现方向池为空，未执行')
    try:
        limits = {'directions': min(len(pool), max(1, min(5, int(config.get('directions_per_task', 3))))),
                  'per_direction': max(1, min(30, int(config.get('candidates_per_direction', 10)))),
                  'merged': max(1, min(settings.limit('discover_max_candidates', 30), int(config.get('merged_candidates', 30))))}
    except (TypeError, ValueError):
        raise ConfigError('发现数量配置无效') from None
    return 'broad', {'discovery_mode': mode, 'discovery_pool': pool, 'discovery_limits': limits}


def materialize_plan(conn, options):
    options = dict(options)
    if options.get('discovery_mode') != 'broad' or 'discovery_plan' in options:
        return options
    pool, limits = options['discovery_pool'], options['discovery_limits']
    row = conn.execute('SELECT cursor FROM discovery_cursor WHERE id=1').fetchone()
    cursor = row[0] if row else 0
    count = limits['directions']
    options['discovery_plan'] = {'cursor': cursor, 'queries': [pool[(cursor+i) % len(pool)] for i in range(count)],
                                 'per_direction': limits['per_direction'], 'merged': limits['merged'], 'pages': 1}
    conn.execute('INSERT INTO discovery_cursor VALUES(1,?) ON CONFLICT(id) DO UPDATE SET cursor=excluded.cursor',
                 (cursor+count,))
    return options


def collect_candidates(client, plan):
    batches, records = [], []
    for direction in plan['queries']:
        try:
            rows = client.search_repositories(direction['query'], per_page=plan['per_direction'])[:plan['per_direction']]
            records.append({**direction, 'hits': len(rows), 'error': ''})
        except FetchError as exc:
            rows = []
            records.append({**direction, 'hits': 0, 'error': str(exc)[:300]})
        batches.append(rows)
    identities = {}
    for direction, rows in zip(plan['queries'], batches):
        for row in rows:
            repo = normalize_repo_name(row.get('full_name'))
            if not repo:
                continue
            if repo not in identities:
                identities[repo] = {**row, 'full_name': repo, 'discovery_sources': []}
            source = {'direction': direction['id'], 'query': direction['query']}
            if source not in identities[repo]['discovery_sources']:
                identities[repo]['discovery_sources'].append(source)
    merged, seen = [], set()
    for index in range(max(map(len, batches), default=0)):
        for rows in batches:
            if index >= len(rows):
                continue
            repo = normalize_repo_name(rows[index].get('full_name'))
            if repo in identities and repo not in seen:
                seen.add(repo)
                merged.append(identities[repo])
    info = {'plan': plan, 'queries': records, 'retrieved': sum(len(b) for b in batches),
            'unique': len(identities), 'duplicates': sum(len(b) for b in batches)-len(identities),
            'merged': min(len(merged), plan['merged']), 'cooldown_skipped': 0, 'screening_count': 0,
            'all_failed': bool(records) and all(r['error'] for r in records)}
    return merged[:plan['merged']], info
