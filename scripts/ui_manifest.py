"""Offline build handoff: list approved public UI files; no application imports/config."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from learnradar.ui_assets import AssetRegistry, asset_name


def main(argv=None):
    parser = argparse.ArgumentParser(description='Generate a bounded UI resource manifest from a public build directory')
    parser.add_argument('--dist', type=Path, default=Path('frontend/dist'))
    parser.add_argument('--script', required=True, help='Relative local module entry (.js)')
    parser.add_argument('--style', action='append', default=[], help='Relative local stylesheet; repeat up to four')
    args = parser.parse_args(argv)
    declared = args.dist.absolute()
    if any(p.is_symlink() for p in (declared, *declared.parents)):
        parser.error('公开构建目录不存在或不允许；不读取业务数据')
    try:
        root = declared.resolve()
    except (OSError, RuntimeError):
        parser.error('公开构建目录无法读取；未修改清单')
    if not root.is_dir():
        parser.error('公开构建目录不存在；未修改清单')
    files = sorted(p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file() and p.name != 'manifest.json')
    if any(not asset_name(name) for name in files):
        parser.error('构建目录含未允许的扩展名/路径；请移除源码、source map 或其他非公开产物')
    target = root / 'manifest.json'
    # Validate before replacing a previous manifest; never follow manifest symlinks.
    if target.is_symlink():
        parser.error('清单路径不允许符号链接')
    temporary = root / 'manifest.json.tmp'
    if temporary.exists():
        parser.error('临时清单已存在，请先检查上次未完成的构建')
    manifest = {'version': 1, 'files': files, 'scripts': [args.script], 'styles': args.style}
    # Registry validation uses the final manifest name; validate all other bounds directly.
    from learnradar.ui_assets import MAX_FILE_BYTES, MAX_TOTAL_BYTES
    checker = AssetRegistry(root)
    if (not 1 <= len(files) <= 256 or args.script not in files or not args.script.endswith('.js')
            or len(args.style) > 4 or any(s not in files or not s.endswith('.css') for s in args.style)
            or any(root.joinpath(f).is_symlink() or root.joinpath(f).stat().st_size > MAX_FILE_BYTES for f in files)
            or any(checker.checked_path(f) is None for f in files)
            or sum(root.joinpath(f).stat().st_size for f in files) > MAX_TOTAL_BYTES):
        parser.error('构建资源超出清单边界或入口无效；未修改清单')
    content = json.dumps(manifest, ensure_ascii=False, indent=2)
    if len(content.encode('utf-8')) > 65536:
        parser.error('清单超过 64 KiB；未修改清单')
    with temporary.open('x', encoding='utf-8') as stream:
        stream.write(content + '\n')
    temporary.replace(target)
    print('UI public manifest ready; restart the isolated web service to load it.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
