"""Create a fresh, local public-source candidate; never copy private Git history.

Existing destinations are refused. Commercial Webfonts and built bundles are
omitted; absent fonts retain the production swap/system fallback. No network.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
DIRECTORIES = ('learnradar/', 'prompts/', 'tests/', 'frontend/src/', 'frontend/tests/', 'frontend/scripts/', 'frontend/public/', 'frontend/licenses/', 'frontend/templates/')
FILES = {'.env.example', '.gitignore', 'README.md', 'config/settings.toml', 'config/topics.toml',
         'frontend/package.json', 'frontend/package-lock.json', 'frontend/tsconfig.json',
         'scripts/lr.ps1', 'scripts/demo.ps1', 'scripts/start.ps1', 'scripts/ui_manifest.py', 'scripts/ui_fixture.py',
         'scripts/ui_integration.py', 'scripts/release_audit.py', 'scripts/public_export.py',
         'scripts/public_smoke.py', 'scripts/offline_recheck.py', 'scripts/build_release_artifact.py',
         'docs/DEPLOY-ARTIFACT-PROPOSAL.md', 'docs/FEEDBACK.md', 'docs/PUBLIC-SOURCE-GUIDE.md', 'docs/VIDEO-OUTLINE.md'}
DENY = {'frontend/licenses/novecento-status.md', 'frontend/licenses/novecento-webfonts.json',
        'frontend/licenses/NOVECENTO-WEBFONT-NOTICE.txt', 'frontend/licenses/resource-inventory.json'}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--source-ref', default='HEAD')
    args = p.parse_args(); dest = args.out.resolve()
    if dest.exists(): p.error('destination must not exist; existing work is never overwritten')
    if not dest.is_relative_to(ROOT): p.error('local export must stay inside this workspace')
    source = subprocess.check_output(['git', 'rev-parse', args.source_ref], cwd=ROOT).decode().strip()
    tracked = subprocess.check_output(['git', 'ls-tree', '-r', '--name-only', '-z', source], cwd=ROOT).decode().split('\0')
    selected = []
    for name in tracked:
        if not name or name in DENY or 'NovecentoWide-' in name or name.startswith('.agents/'): continue
        if name.startswith(DIRECTORIES) or name in FILES or (name.startswith('config/') and '.example.' in name):
            selected.append(name)
    dest.mkdir(parents=True)
    rows = []
    for name in sorted(set(selected)):
        file = ROOT / name
        if file.is_symlink(): raise ValueError('symlink not allowed: ' + name)
        data = subprocess.check_output(['git', 'show', source + ':' + name], cwd=ROOT)
        if name == 'config/profile.example.toml':
            # The private project's old "example" mirrors personal details.
            # Replace it wholesale with artificial/unknown public fields.
            data = '''# Artificial public fixture/template. No personal profile information.
profile_id = "public-fixture-v1"
confirmed_by_user = false
updated_at = "2026-10-13"
source = "人工合成公开示例；未实测，不代表任何个人档案"
[identity]
role = "演示用户"
background = "未知/未提供"
assistance = "离线fixture演示，不代表实际安装或学习能力"
language_preference = "未知/未提供"
devices = "未知/未提供"
daily_time = "未知/未提供"
[goals]
current = "离线阅读项目报告示例"
focus = "Agent与软件工程示例"
values = "仅用于演示"
outcome_habit = "示例，不推断个人偏好"
deliverable_expectation = "fixture报告与来源示例"
[resources]
available_api = "未配置，不能假定存在任何凭据"
other_providers = "未配置"
cloud = "未知/未提供"
gpu = "未知/未提供"
os = "未知/未提供"
'''.encode('utf-8')
        # Sanitize historical machine-specific documentation/helper defaults;
        # substitutions affect the export only, never the accepted private UI.
        if b'\0' not in data and not name.endswith(('.woff2', '.glb', '.ogg', '.wav', '.pdf')):
            text = data.decode('utf-8')
            if name in ('frontend/src/theme.css', 'frontend/templates/shell.html'):
                text = re.sub(r'/\*\*\s*\* @license\s*\* MyFonts Webfont Build ID.*?\*/\s*', '', text, flags=re.S)
                text = text.replace('See licenses/NOVECENTO-WEBFONT-NOTICE.txt.', 'Optional licensed local fonts; see docs/PUBLIC-SOURCE-GUIDE.md.')
            text = re.sub(r'[Ee]:\\(?:\\)?Assistant(?:\\(?:\\)?data(?:\\(?:\\)?worktrees(?:\\(?:\\)?[\w-]+)?)?)?', '<repository>', text)
            text = text.replace('<repository>', '<repository>').replace('<repository>', '<repository>')
            text = re.sub(r'[Ee]:(?:\\+|/)python(?:\\+|/)python\.exe', 'python', text)
            if name in ('README.md', 'docs/PUBLIC-SOURCE-GUIDE.md'): text = text.replace('python', 'python')
            if name == 'scripts/lr.ps1':
                text = text.replace('    $tries.Add("python")\n', '')
                text = text.replace(' -> python', '')
            # This phrase in the old test/example context mirrors a personal
            # profile; public tests use an anonymous artificial learner.
            text = text.replace('演示用户', '演示用户')
            if name == 'config/settings.toml':
                # Remove a historical personal model preference, keeping the
                # actual configurable business defaults and offline adapter.
                text = re.sub(r'# --- 用户首选.*?(?=# --- 离线夹具)', '', text, flags=re.S)
                text = '\n'.join(line for line in text.splitlines() if not line.startswith('# 2026-')) + '\n'
            data = text.encode('utf-8')
        target = dest / name; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(data)
        rows.append({'file': name, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
    # A generic collaboration scope replaces private task/history instructions.
    (dest / 'AGENTS.md').write_text('# Public development scope\n\nUse explicit isolated fixture accounts/data and offline adapters for tests. Never run real model/GitHub/SMTP requests without user authorization. Keep native authentication/CSRF, durable budgets and quality gates. Preserve third-party notices.\n', encoding='utf-8')
    with (dest / '.gitignore').open('a', encoding='utf-8') as ignore:
        ignore.write('\n# Commercial fonts are private optional local assets, never public history.\nfrontend/public/fonts/NovecentoWide-*.woff2\n')
    # Hash the final transformed bytes, including generated generic AGENTS and
    # the appended ignore rules. Do not describe pre-transformation bytes.
    rows = [{'file': f.relative_to(dest).as_posix(), 'bytes': f.stat().st_size,
             'sha256': hashlib.sha256(f.read_bytes()).hexdigest()}
            for f in sorted(dest.rglob('*')) if f.is_file()]
    info = {'source_sha': source, 'local_worktree_changes_included': False, 'copied_files': rows,
            'excluded': ['private Git history', 'data/logs/reports/credentials', 'commercial Novecento binaries and old built bundles', '.agents private project skill/history'],
            'transforms': ['whole artificial profile.example.toml', 'generic AGENTS', 'machine path removal', 'commercial kit metadata removal', 'anonymous test identity', 'historical personal model preference removed', 'optional commercial font ignore rules'],
            'scope': 'Local review candidate only; not pushed, deployed or published. Every source file is read from the resolved committed SHA, not the dirty worktree.'}
    (dest / 'EXPORT-MANIFEST.json').write_text(json.dumps(info, indent=2) + '\n', encoding='utf-8')
    subprocess.run(['git', 'init', '-b', 'main', str(dest)], check=True, stdout=subprocess.DEVNULL)
    print(json.dumps({'export': str(dest), 'source_sha': source, 'files': len(rows), 'bytes': sum(r['bytes'] for r in rows)}))


if __name__ == '__main__': main()
