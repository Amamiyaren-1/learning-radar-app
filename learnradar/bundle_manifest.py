"""Private runs/reports inventories, shared by backup and staged restore."""
import hashlib
import os
from pathlib import Path, PurePosixPath
import re

DIRECTORIES = ('runs', 'reports')
FILE_MANIFEST_VERSION = 1


def inventory(directory):
    directory = Path(directory)
    included, files = {}, []
    for name in DIRECTORIES:
        root = directory / name
        if root.is_symlink():
            raise ValueError('文件包不能含符号链接')
        if not root.exists():
            continue
        if not root.is_dir():
            raise ValueError('清单目录不是目录')
        included[name] = 0
        for path in sorted(root.rglob('*')):
            if path.is_symlink():
                raise ValueError('文件包不能含符号链接')
            if path.is_dir():
                continue
            if not path.is_file():
                raise ValueError('文件包只能包含普通文件和目录')
            with path.open('rb') as stream:
                size = os.fstat(stream.fileno()).st_size
                digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            files.append({'path': path.relative_to(directory).as_posix(),
                          'size_bytes': size, 'sha256': digest})
            included[name] += 1
    return {'file_manifest_version': FILE_MANIFEST_VERSION,
            'included': included, 'files': files}


def verify_inventory(directory, manifest):
    """Never echo private paths/hashes; legacy counts are explicitly weaker."""
    if not isinstance(manifest, dict):
        raise ValueError('文件包清单格式无效')
    included = manifest.get('included')
    if not isinstance(included, dict) or any(
        name not in DIRECTORIES or type(count) is not int or count < 0
        for name, count in included.items()
    ):
        raise ValueError('文件包清单缺少有效的目录与数量')
    directory = Path(directory)
    if any(not (directory / name).is_dir() for name in included):
        raise ValueError('文件包缺少清单声明的目录')
    actual = inventory(directory)
    if actual['included'] != included:
        raise ValueError('文件包目录或数量与清单不匹配；可能缺少文件')
    if 'files' not in manifest and 'file_manifest_version' not in manifest:
        return {'mode': 'legacy_counts_only', 'verified_files': sum(included.values())}
    if (type(manifest.get('file_manifest_version')) is not int
            or manifest['file_manifest_version'] != FILE_MANIFEST_VERSION
            or not isinstance(manifest.get('files'), list)):
        raise ValueError('文件包逐文件清单版本或格式无效；不会退回旧版检查')
    expected = {}
    counts = dict.fromkeys(included, 0)
    for entry in manifest['files']:
        if not isinstance(entry, dict):
            raise ValueError('文件包逐文件清单格式无效')
        value = entry.get('path')
        if not isinstance(value, str):
            raise ValueError('文件包清单路径无效')
        path = PurePosixPath(value)
        if (path.is_absolute() or len(path.parts) < 2 or path.parts[0] not in included
                or '..' in path.parts or '\\' in value or '\x00' in value
                or path.as_posix() != value or value in expected):
            raise ValueError('文件包清单路径不安全或重复')
        size, digest = entry.get('size_bytes'), entry.get('sha256')
        if type(size) is not int or size < 0 or not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest):
            raise ValueError('文件包完整性信息无效')
        expected[value] = (size, digest)
        counts[path.parts[0]] += 1
    if counts != included:
        raise ValueError('文件包逐文件清单与声明数量不匹配')
    observed = {entry['path']: (entry['size_bytes'], entry['sha256']) for entry in actual['files']}
    if expected.keys() != observed.keys():
        raise ValueError('文件包文件列表不匹配；存在缺少或未声明的文件')
    if expected != observed:
        raise ValueError('文件包内容或大小与清单不匹配；完整性验证失败')
    return {'mode': 'files_sha256', 'verified_files': len(expected)}
