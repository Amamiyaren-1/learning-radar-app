"""Write/check the reviewed source manifest; its own hash is intentionally external."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[1]
NAME='FINAL-SOURCE-MANIFEST.json'
BASE='fab8e6eb266ff34a3d0c50a5bf2733428b746176'

def source_files():
    return sorted(n for n in subprocess.check_output(['git','ls-files','-z'],cwd=ROOT).decode().split('\0') if n and n!=NAME)

def rows(names):
    out=[]
    for name in names:
        p=ROOT/name
        if p.is_symlink() or not p.resolve().is_relative_to(ROOT):raise ValueError('unsafe source path')
        data=p.read_bytes()
        out.append({'file':name,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()})
    return out

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write',action='store_true',help='Review/stage allowed files before writing')
    args=parser.parse_args(); current=rows(source_files())
    if args.write:
        baseline=json.loads((ROOT/'EXPORT-MANIFEST.json').read_text(encoding='utf-8'))
        old={r['file']:r['sha256'] for r in baseline['copied_files']}
        changed=[r['file'] for r in current if old.get(r['file'])!=r['sha256']]
        data={'kind':'reviewed-public-source','historical_public_baseline':BASE,
              'historical_private_export_source':baseline['source_sha'],
              'note':'EXPORT-MANIFEST is historical provenance, not equality with this source. This file excludes itself; the external artifact manifest binds its hash and the committed source SHA.',
              'allowed_change_categories':['English developer docs/comments and bilingual README','pending MIT proposal and third-party notices','original generic public geometry/loop and reachable-history resource exclusion','layered public optics and actual physical-material construction with fixture runtime audit','compatible Vite/esbuild security patches and dependency lock/notices','scoped fake-adapter test precheck fixes and offline runner','review screenshots/video preparation and source/package tooling','reviewed-byte checkout attributes'],
              'paths_different_from_export_baseline':changed,'files':current}
        (ROOT/NAME).write_bytes((json.dumps(data,ensure_ascii=False,indent=2)+'\n').encode())
    else:
        expected=json.loads((ROOT/NAME).read_text(encoding='utf-8'))['files']
        if current!=expected:raise ValueError('source bytes or tracked allowlist differ from reviewed manifest')
    print(json.dumps({'source_files':len(current),'manifest_matches':True,'self_hash_bound_by_artifact':True}))

if __name__=='__main__':main()
