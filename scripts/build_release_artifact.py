"""Build a review ZIP from a clean sanitized public candidate and same build."""
import argparse
import hashlib
import json
from pathlib import Path,PurePosixPath
import subprocess
import zipfile
ROOT=Path(__file__).resolve().parents[1]

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args();candidate=args.candidate.resolve();out=args.out.resolve()
    if out.exists() or not out.is_relative_to((ROOT/'data').resolve()):parser.error('use a new output file inside this workspace data/')
    if subprocess.check_output(['git','status','--porcelain'],cwd=candidate).strip():parser.error('candidate must be clean; preserve existing changes')
    sha=subprocess.check_output(['git','rev-parse','HEAD'],cwd=candidate).decode().strip()
    export=json.loads((candidate/'EXPORT-MANIFEST.json').read_text(encoding='utf-8'))
    resources=json.loads((candidate/'frontend/dist/manifest.json').read_text(encoding='utf-8'))
    rows=[]
    def add(name,expected=None):
        parts=PurePosixPath(name)
        if parts.is_absolute() or '..' in parts.parts or parts.parts[0] in ('.git','data','logs','.agents','node_modules'):
            raise ValueError('path outside review allowlist')
        if name in ('.env','config/profile.toml','config/email.toml','config/web.toml','config/settings.local.toml') or 'NovecentoWide-' in name:
            raise ValueError('private/optional commercial file refused: '+name)
        file=(candidate/name).resolve()
        if not file.is_relative_to(candidate) or file.is_symlink():raise ValueError('unsafe artifact path')
        data=file.read_bytes();row={'file':name,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}
        if expected and any(row[key]!=expected[key] for key in ('bytes','sha256')):raise ValueError('source changed after export: '+name)
        rows.append(row)
    for row in export['copied_files']:add(row['file'],row)
    add('EXPORT-MANIFEST.json')
    for name in resources['files']:add('frontend/dist/'+name)
    add('frontend/dist/manifest.json')
    info={'kind':'local-review-artifact','public_source_sha':sha,'private_source_ref':export['source_sha'],
          'frontend_manifest':resources,'files':rows,'commercial_fonts_included':False,
          'private_data_or_git_history_included':False,'deployed':False,
          'note':'Sanitized source plus actual same-version frontend build. Production config/data remain external; commercial fonts use fallback. No cloud/timer changes.'}
    out.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(out,'x',compression=zipfile.ZIP_DEFLATED) as archive:
        for row in rows:archive.write(candidate/row['file'],row['file'])
        archive.writestr('ARTIFACT-MANIFEST.json',json.dumps(info,indent=2)+'\n')
    result={'zip':str(out),'bytes':out.stat().st_size,'sha256':hashlib.sha256(out.read_bytes()).hexdigest(),'source_sha':sha,'resources':len(resources['files']),'deployed':False}
    out.with_suffix('.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result))

if __name__=='__main__':main()
