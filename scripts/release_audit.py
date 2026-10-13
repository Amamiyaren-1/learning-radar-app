"""Local release audit. Report paths and types, never matching secret values.

Use --history to inspect private Git blobs before choosing a fresh export history.
No network, no deletion, no model/email adapters.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

PRIVATE_PATH = re.compile(r'(?:[A-Z]:[\\/](?:Users[\\/][^\s/\\]+|Assistant)|/Users/[^\s/]+|/home/[^\s/]+)', re.I)
EMAIL = re.compile(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}')
TOKEN = re.compile(r'\b(?:sk-[A-Za-z0-9_-]{16,}|ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b')
DENIED = ('.env', 'config/profile.toml', 'config/email.toml', 'config/web.toml', 'config/settings.local.toml')
PRIVATE_SUFFIXES = ('.sqlite', '.sqlite-wal', '.sqlite-shm', '.db', '.log', '.key', '.pem')


def findings(name, data):
    result = []
    if name in DENIED or name.startswith(('data/', 'logs/', 'backups/', 'out/')) or name.endswith(PRIVATE_SUFFIXES):
        result.append('private-file')
    if 'NovecentoWide-' in name and name.endswith(('.woff', '.woff2', '.otf')):
        result.append('commercial-font-binary')
    if len(data) < 3_000_000 and b'\0' not in data:
        text = data.decode('utf-8', errors='replace')
        if TOKEN.search(text): result.append('credential-token-candidate')
        if PRIVATE_PATH.search(text): result.append('private-machine-path')
        # Public upstream author/license addresses are retained, not private users.
        if EMAIL.search(text) and not name.endswith(('-license.txt', 'LICENSE', 'NOTICE.txt')):
            result.append('email-address-candidate')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path.cwd())
    parser.add_argument('--history', action='store_true')
    parser.add_argument('--include-dist', action='store_true', help='Inspect the actual newly built assets as well as source')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(); root = args.root.resolve()
    rows = []; inspected = 0
    if args.history:
        objects = subprocess.check_output(['git', 'rev-list', '--objects', '--all'], cwd=root).decode().splitlines()
        for line in objects:
            parts = line.split(' ', 1)
            if len(parts) != 2: continue
            oid, name = parts
            kind = subprocess.check_output(['git', 'cat-file', '-t', oid], cwd=root).strip()
            if kind != b'blob': continue
            data = subprocess.check_output(['git', 'cat-file', 'blob', oid], cwd=root); inspected += 1
            kinds = findings(name, data)
            if kinds: rows.append({'path': name, 'blob': oid, 'types': kinds})
    else:
        for file in sorted(root.rglob('*')):
            excluded = ('.git', 'node_modules', 'data', '__pycache__') + (() if args.include_dist else ('dist',))
            if not file.is_file() or any(p in excluded for p in file.relative_to(root).parts): continue
            name = file.relative_to(root).as_posix(); data = file.read_bytes(); inspected += 1
            kinds = findings(name, data)
            if kinds: rows.append({'path': name, 'sha256': hashlib.sha256(data).hexdigest(), 'types': kinds})
    result = {'mode': 'all-private-git-history' if args.history else 'export-current-files', 'inspected_blobs_or_files': inspected,
              'findings': rows, 'note': 'Heuristic findings require review. No matched values are printed. A clean scan does not prove absence of every secret.'}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'inspected': inspected, 'flagged': len(rows), 'types': sorted({t for r in rows for t in r['types']})}))


if __name__ == '__main__': main()
