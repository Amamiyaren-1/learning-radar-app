"""Public build-only assets: explicit manifest, MIME and bounded file reads."""
import html
import json
from pathlib import Path, PurePosixPath
import re
from urllib.parse import unquote

PREFIX = '/static/ui/'
MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_TOTAL_BYTES = 128 * 1024 * 1024
MIME = {'.js': 'text/javascript', '.css': 'text/css', '.glb': 'model/gltf-binary',
        '.gltf': 'model/gltf+json', '.json': 'application/json', '.woff': 'font/woff',
        '.woff2': 'font/woff2', '.ttf': 'font/ttf', '.otf': 'font/otf',
        '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
        '.webp': 'image/webp', '.svg': 'image/svg+xml', '.ogg': 'audio/ogg',
        '.mp3': 'audio/mpeg', '.wav': 'audio/wav'}
CSP = ("default-src 'none'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; "
       "font-src 'self'; img-src 'self'; connect-src 'self'; media-src 'self'; "
       "object-src 'none'; worker-src 'none'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'")


def asset_name(name):
    return (isinstance(name, str) and len(name) <= 200 and re.fullmatch(r'[A-Za-z0-9_./-]+', name)
            and not name.startswith('/') and all(p not in ('', '.', '..') for p in name.split('/'))
            and PurePosixPath(name).suffix.lower() in MIME and name != 'manifest.json')


class AssetRegistry:
    def __init__(self, root):
        declared = Path(root).absolute()
        self.root = declared
        self.files, self.scripts, self.styles = set(), [], []
        self.available = False
        try:
            if any(p.is_symlink() for p in (declared, *declared.parents)):
                return
            self.root = declared.resolve()
            path = self.root / 'manifest.json'
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 65536:
                return
            manifest = json.loads(path.read_text(encoding='utf-8'))
            files = manifest['files']
            scripts, styles = manifest.get('scripts', []), manifest.get('styles', [])
            if (manifest.get('version') != 1 or not isinstance(files, list) or not 1 <= len(files) <= 256
                    or not all(asset_name(f) for f in files) or len(set(files)) != len(files)
                    or not isinstance(scripts, list) or len(scripts) > 1
                    or not isinstance(styles, list) or len(styles) > 4
                    or any(s not in files or not s.endswith('.js') for s in scripts)
                    or any(s not in files or not s.endswith('.css') for s in styles)):
                return
            total = 0
            for name in files:
                path = self.checked_path(name)
                if path is None:
                    return
                size = path.stat().st_size
                if size > MAX_FILE_BYTES:
                    return
                total += size
            if total > MAX_TOTAL_BYTES:
                return
            self.files, self.scripts, self.styles = set(files), scripts, styles
            self.available = True
        except (OSError, RuntimeError, ValueError, TypeError, KeyError):
            pass  # Build unavailable: keep the server's native HTML fully usable.

    def checked_path(self, name):
        if not asset_name(name):
            return None
        path = self.root
        for part in name.split('/'):
            path = path / part
            if path.is_symlink():
                return None
        return path if path.resolve().is_relative_to(self.root) and path.is_file() else None

    def read(self, encoded_name):
        name = unquote(encoded_name)
        if name not in self.files:
            return None
        try:
            path = self.checked_path(name)
            if path is None or path.stat().st_size > MAX_FILE_BYTES:
                return None
            with path.open('rb') as stream:
                payload = stream.read(MAX_FILE_BYTES + 1)
            if len(payload) > MAX_FILE_BYTES:
                return None
            return payload, MIME[path.suffix.lower()]
        except OSError:
            return None

    def entry_tags(self):
        return ''.join('<link rel="stylesheet" href="' + html.escape(PREFIX+s, quote=True) + '">' for s in self.styles) + ''.join(
            '<script type="module" src="' + html.escape(PREFIX+s, quote=True) + '"></script>' for s in self.scripts)
