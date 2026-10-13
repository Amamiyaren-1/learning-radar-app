"""Shared HTTP authentication and synchronizer-token gate, before business routes."""
import hmac
import html
import ipaddress
import logging
import re
from http.cookies import SimpleCookie, CookieError
from urllib.parse import parse_qs, urlencode, urlsplit, unquote

LOGGER = logging.getLogger('learnradar.web.auth')


def safe_next(value):
    if not isinstance(value, str) or len(value) > 2000:
        return '/'
    decoded = unquote(value)
    if not decoded.startswith('/') or decoded.startswith('//') or '\\' in decoded or any(ord(c) < 32 for c in decoded):
        return '/'
    parsed = urlsplit(decoded)
    return value if not parsed.scheme and not parsed.netloc else '/'


class HttpGate:
    def __init__(self, auth, page, *, demo=False):
        self.auth, self.page, self.demo = auth, page, demo

    def event(self, category, result):
        LOGGER.info('time=%d event=%s result=%s', self.auth.clock(), category, result)

    def reject(self, handler, status=403):
        self.event('request-protection', 'denied')
        handler._send(status, self.page('请求被拒绝', '<h1>请求被拒绝</h1><p>请从本站页面重新操作。</p>'))
        return False

    def request_allowed(self, handler, *, write=False):
        headers = handler.headers
        for name in ('Host', 'Origin', 'Sec-Fetch-Site', 'X-Forwarded-Proto', 'X-Forwarded-Host'):
            if len(headers.get_all(name, [])) > 1:
                return False
        config = self.auth.config
        host = headers.get('Host', '')
        peer = handler.client_address[0]
        if config.secure:
            expected = urlsplit(config.public_origin).netloc
            if peer not in config.trusted_proxies or host != expected or headers.get('X-Forwarded-Proto') != 'https' or headers.get('X-Forwarded-Host') != expected:
                return False
            origin = config.public_origin
            if headers.get('Forwarded'):
                return False
        else:
            if not ipaddress.ip_address(peer).is_loopback or any(name.lower().startswith('x-forwarded-') or name.lower() == 'forwarded' for name in headers.keys()):
                return False
            allowed = {f'127.0.0.1:{handler.server.server_port}', f'localhost:{handler.server.server_port}'}
            if host not in allowed:
                return False
            origin = 'http://' + host
        if write:
            if headers.get('Origin') != origin:
                return False
            if headers.get('Sec-Fetch-Site') not in (None, 'same-origin', 'none'):
                return False
        return True

    def read_session(self, handler):
        cookie_headers = handler.headers.get_all('Cookie', [])
        if len(cookie_headers) != 1:
            return None
        try:
            cookies = SimpleCookie()
            cookies.load(cookie_headers[0])
            value = cookies.get(self.auth.config.cookie_name)
            return self.auth.store.session(value.value, self.auth.clock(), self.auth.config) if value else None
        except (CookieError, ValueError, UnicodeError):
            return None

    def cookie(self, handler, token, *, clear=False):
        config = self.auth.config
        value = f'{config.cookie_name}={token}; Path=/; HttpOnly; SameSite=Strict'
        if config.secure:
            value += '; Secure'
        if clear:
            value += '; Max-Age=0'
        handler.pending_cookie = value

    def redirect(self, handler, location):
        handler.send_response(303)
        handler.send_header('Location', location)
        handler.send_header('Content-Length', '0')
        handler.end_headers()

    def login_page(self, session, next_path, error=False):
        esc = html.escape
        banner = '<p><strong>演示模式 · fixture</strong>：仅隔离示例账号，未实测。</p>' if self.demo else ''
        message = '<p>登录未成功，请稍后重试。</p>' if error else ''
        body = f'''<h1>登录 Learning Radar</h1>{banner}{message}
<form data-lr-action="login" method="post" action="/login">
<input type="hidden" name="csrf_token" value="{esc(session['csrf'], quote=True)}">
<input type="hidden" name="next" value="{esc(safe_next(next_path), quote=True)}">
<p><label>账号 <input name="username" autocomplete="username" required maxlength="128"></label></p>
<p><label>密码 <input name="password" type="password" autocomplete="current-password" required maxlength="128"></label></p>
<button type="submit">登录</button></form>'''
        return self.page('登录', body)

    def get(self, handler):
        if not self.request_allowed(handler):
            return self.reject(handler)
        path = urlsplit(handler.path).path
        if path == '/health':
            handler._send(200, 'ok')
            return False
        session = self.read_session(handler)
        handler.auth_session = session
        if path == '/login':
            next_path = safe_next((parse_qs(urlsplit(handler.path).query).get('next') or ['/'])[0])
            if session and session['authenticated']:
                self.redirect(handler, next_path)
            else:
                if not session:
                    created = self.auth.store.create_session(self.auth.clock(), self.auth.config)
                    if not created:
                        handler._send(503, self.page('登录暂不可用', '<h1>登录暂不可用</h1><p>请稍后重试。</p>'))
                        return False
                    token, session = created
                    self.cookie(handler, token)
                handler._send(200, self.login_page(session, next_path))
            return False
        if not session or not session['authenticated']:
            self.redirect(handler, '/login?' + urlencode({'next': safe_next(handler.path)}))
            return False
        if path == '/logout':
            handler._send(405, self.page('退出登录', '<p>请使用页面上的退出按钮。</p>'))
            return False
        return True

    def post(self, handler, fields):
        if not self.request_allowed(handler, write=True):
            return self.reject(handler)
        session = self.read_session(handler)
        handler.auth_session = session
        path = urlsplit(handler.path).path
        if not session or (path != '/login' and not session['authenticated']):
            return self.reject(handler, 401)
        supplied = fields.get('csrf_token', [])
        if len(supplied) != 1 or not hmac.compare_digest(supplied[0].encode('utf-8'), session['csrf'].encode('ascii')):
            return self.reject(handler)
        if path == '/login':
            if session['authenticated']:
                return self.reject(handler)
            username, password = fields.get('username', ['']), fields.get('password', [''])
            if len(username) != 1 or len(password) != 1 or len(username[0]) > 128:
                return self.reject(handler)
            created, status = self.auth.login(username[0], password[0], handler.client_address[0], session)
            self.event('login', 'accepted' if created else 'denied')
            if created:
                token, authenticated = created
                self.cookie(handler, token)
                self.redirect(handler, safe_next((fields.get('next') or ['/'])[0]))
            else:
                handler._send(status, self.login_page(session, (fields.get('next') or ['/'])[0], error=True))
            return False
        if path == '/logout':
            self.auth.store.revoke(session['digest'])
            self.cookie(handler, '', clear=True)
            handler.auth_session = None
            self.event('logout', 'accepted')
            self.redirect(handler, '/login')
            return False
        return True

    def decorate(self, handler, document):
        session = getattr(handler, 'auth_session', None)
        if not session or not session['authenticated']:
            return document
        token = html.escape(session['csrf'], quote=True)
        # Existing server-rendered POST forms keep their submission_key and semantics.
        document = re.sub(r'(<form\b[^>]*\bmethod="post"[^>]*>)', lambda m: m[0]+f'<input type="hidden" name="csrf_token" value="{token}">', document)
        logout = f'<form data-lr-action="logout" method="post" action="/logout"><input type="hidden" name="csrf_token" value="{token}"><button type="submit">退出登录</button></form>'
        return document.replace('</nav>', logout+'</nav>', 1)
