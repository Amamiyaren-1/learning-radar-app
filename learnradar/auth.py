"""Standard-library scrypt password hashing, bounded verification and short login backoff."""
import base64
import hashlib
import hmac
import json
import secrets
import threading
import time
from collections import OrderedDict

from .auth_store import AuthStore
from .config import ConfigError

# OWASP Password Storage Cheat Sheet scrypt alternative: 32 MiB, parallelization 3.
SCRYPT = {'n': 32768, 'r': 8, 'p': 3, 'dklen': 32, 'maxmem': 64*1024*1024}
HASH_SLOT = threading.BoundedSemaphore(1)
FIXTURE_USERNAME = 'fixture'
FIXTURE_PASSWORD = 'fixture-local-only-2026'


def password_hash(password):
    if not 12 <= len(password) <= 128:
        raise ValueError("密码必须为 12 至 128 个字符")
    salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(password.encode('utf-8'), salt=salt, **SCRYPT)
    return json.dumps({'algorithm': 'scrypt', 'n': SCRYPT['n'], 'r': SCRYPT['r'], 'p': SCRYPT['p'], 'salt': base64.b64encode(salt).decode('ascii'), 'value': base64.b64encode(derived).decode('ascii')})


def verifier_valid(verifier):
    try:
        saved = json.loads(verifier)
        return (saved['algorithm'], saved['n'], saved['r'], saved['p']) == ('scrypt', SCRYPT['n'], SCRYPT['r'], SCRYPT['p']) and len(base64.b64decode(saved['salt'], validate=True)) == 16 and len(base64.b64decode(saved['value'], validate=True)) == 32
    except (ValueError, KeyError, TypeError):
        return False


def password_matches(password, verifier):
    if not isinstance(password, str) or len(password) > 128 or not verifier_valid(verifier):
        return False
    try:
        saved = json.loads(verifier)
        if (saved['algorithm'], saved['n'], saved['r'], saved['p']) != ('scrypt', SCRYPT['n'], SCRYPT['r'], SCRYPT['p']):
            return False
        salt, expected = (base64.b64decode(saved[key], validate=True) for key in ('salt', 'value'))
        if len(salt) != 16 or len(expected) != 32:
            return False
        return hmac.compare_digest(hashlib.scrypt(password.encode('utf-8'), salt=salt, **SCRYPT), expected)
    except (ValueError, KeyError, TypeError):
        return False


class LoginLimiter:
    def __init__(self, clock=time.monotonic):
        self.clock, self.entries, self.lock = clock, OrderedDict(), threading.Lock()

    def allowed(self, peer):
        with self.lock:
            now = self.clock()
            for key in list(self.entries):
                if now-self.entries[key][2] >= 300:
                    del self.entries[key]
            entry = self.entries.get(peer)
            return not entry or now >= entry[1]

    def failure(self, peer):
        with self.lock:
            old = self.entries.pop(peer, (0, 0, 0))
            count = min(old[0]+1, 8)
            now = self.clock()
            self.entries[peer] = (count, now+min(30, 2**max(0, count-2)), now)
            while len(self.entries) > 128:
                self.entries.popitem(last=False)

    def success(self, peer):
        with self.lock:
            self.entries.pop(peer, None)


class AuthService:
    def __init__(self, path, config, *, demo=False, clock=time.time):
        self.store, self.config, self.clock = AuthStore(path), config, clock
        self.limiter = LoginLimiter()
        account = self.store.account()
        if demo and not account:
            try:
                self.store.set_account(FIXTURE_USERNAME, password_hash(FIXTURE_PASSWORD), fixture=True)
            except ValueError:
                pass  # Another server initialized the same isolated account.
            account = self.store.account()
        if not account:
            raise ConfigError("网页账号未初始化；先运行 python -m learnradar.auth_cli init（不提供私人页面）")
        if bool(account['fixture']) != demo:
            raise ConfigError("正式与 fixture 认证数据不能混用")
        if not verifier_valid(account['verifier']):
            raise ConfigError("网页账号校验配置损坏；本机重置后再启动，不提供私人页面")

    def login(self, username, password, peer, prelogin):
        if not self.limiter.allowed(peer) or not HASH_SLOT.acquire(blocking=False):
            return None, 429
        try:
            account = self.store.account()
            correct = account and password_matches(password, account['verifier'])
            if not correct or not hmac.compare_digest(username.encode('utf-8'), account['username'].encode('utf-8')):
                self.limiter.failure(peer)
                return None, 401
            created = self.store.create_session(self.clock(), self.config, authenticated=True, generation=account['generation'], old_digest=prelogin['digest'])
            if not created:
                return None, 401
            self.limiter.success(peer)
            return created, 303
        finally:
            HASH_SLOT.release()
