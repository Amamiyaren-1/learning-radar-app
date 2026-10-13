"""Server-owned native HTML shell, safe bootstrap and optional local UI resources."""
import html
import json
from pathlib import Path
from string import Template

from .ui_projection import envelope

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SHELL = '''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>$title</title><style>$style</style></head><body><main>
<div id="lr-app" data-lr-role="visual" hidden></div>
<section id="lr-business" data-lr-role="business">
<nav aria-label="主导航"><a href="/">首页</a><a href="/history">历史</a></nav>$business</section>
</main><script id="lr-bootstrap" type="application/json">$bootstrap</script><!--lr-ui-resources--></body></html>'''


def safe_json(data):
    # Raw-text script parsing must never encounter a closing tag from saved data.
    return json.dumps(data, ensure_ascii=False, separators=(',', ':')).replace('&', '\\u0026').replace('<', '\\u003c').replace('>', '\\u003e').replace('\u2028', '\\u2028').replace('\u2029', '\\u2029')


def shell(title, business, style, *, kind='message', data=None, fixture=False):
    template = DEFAULT_SHELL
    path = ROOT / 'frontend/templates/shell.html'
    try:
        if path.is_file() and path.stat().st_size <= 32768:
            candidate = path.read_text(encoding='utf-8')
            # A missing build/template must retain a complete native fallback.
            if all(marker in candidate for marker in ('id="lr-app"', 'id="lr-business"', 'id="lr-bootstrap"', '$business', '$bootstrap', '<!--lr-ui-resources-->')):
                template = candidate
    except (OSError, UnicodeError):
        pass
    values = dict(title=html.escape(title, quote=True), business=business, style=style,
                  bootstrap=safe_json(envelope(kind, fixture=fixture, **(data or {}))))
    try:
        return Template(template).substitute(values)
    except (KeyError, ValueError):
        return Template(DEFAULT_SHELL).substitute(values)


def finalize(document, *, status, fixture, resources):
    marker = '<script id="lr-bootstrap" type="application/json">'
    before, separator, rest = document.partition(marker)
    if not separator:
        return document
    raw, end, after = rest.partition('</script>')
    if not end:
        return document
    value = json.loads(raw)
    value['fixture'], value['http_status'] = bool(fixture), status
    value['ui_resources'] = {'available': resources.available}
    document = before + separator + safe_json(value) + end + after
    return document.replace('<!--lr-ui-resources-->', resources.entry_tags(), 1)
