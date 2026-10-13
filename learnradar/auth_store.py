"""Private incremental SQLite authentication records, separate from recommendation state."""
import hashlib
import secrets
import sqlite3
from contextlib import contextmanager, closing

SESSION_LIMIT = 256
ANONYMOUS_LIMIT = 64
AUTHENTICATED_LIMIT = SESSION_LIMIT - ANONYMOUS_LIMIT

SCHEMA = """
CREATE TABLE IF NOT EXISTS web_account (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
    username TEXT NOT NULL, verifier TEXT NOT NULL,
    generation INTEGER NOT NULL, fixture INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS web_sessions (
    digest TEXT PRIMARY KEY, csrf TEXT NOT NULL, authenticated INTEGER NOT NULL,
    generation INTEGER NOT NULL, created REAL NOT NULL, seen REAL NOT NULL, expires REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS web_sessions_expires ON web_sessions(expires);
"""


def token_digest(token):
    return hashlib.sha256(token.encode('ascii')).hexdigest()


class AuthStore:
    def __init__(self, path):
        self.path = path

    @contextmanager
    def transaction(self):
        with closing(sqlite3.connect(self.path, timeout=10)) as conn, conn:
            conn.row_factory = sqlite3.Row
            conn.execute("BEGIN IMMEDIATE")
            yield conn

    def account(self):
        with closing(sqlite3.connect(self.path)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM web_account WHERE singleton=1").fetchone()
            return dict(row) if row else None

    def set_account(self, username, verifier, *, fixture, reset=False):
        with self.transaction() as conn:
            old = conn.execute("SELECT * FROM web_account WHERE singleton=1").fetchone()
            if old and not reset:
                raise ValueError("账号已初始化；请使用 reset")
            if old and bool(old['fixture']) != fixture:
                raise ValueError("正式和 fixture 认证数据不能混用")
            generation = old['generation'] + 1 if old else 1
            conn.execute("INSERT OR REPLACE INTO web_account VALUES (1,?,?,?,?)", (username, verifier, generation, int(fixture)))
            conn.execute("DELETE FROM web_sessions")

    def revoke_all(self):
        with self.transaction() as conn:
            conn.execute("DELETE FROM web_sessions")
            conn.execute("UPDATE web_account SET generation=generation+1")

    def session(self, token, now, config):
        if not isinstance(token, str) or len(token) != 64 or any(c not in '0123456789abcdef' for c in token):
            return None
        with self.transaction() as conn:
            row = conn.execute("SELECT * FROM web_sessions WHERE digest=?", (token_digest(token),)).fetchone()
            account = conn.execute("SELECT generation FROM web_account WHERE singleton=1").fetchone()
            idle = config.idle_seconds if row and row['authenticated'] else config.prelogin_seconds
            if not row or not account or row['generation'] != account['generation'] or now >= row['expires'] or now - row['seen'] >= idle:
                if row:
                    conn.execute("DELETE FROM web_sessions WHERE digest=?", (row['digest'],))
                return None
            conn.execute("UPDATE web_sessions SET seen=? WHERE digest=?", (now, row['digest']))
            return dict(row)

    def create_session(self, now, config, *, authenticated=False, generation=None, old_digest=None):
        token, csrf = secrets.token_hex(32), secrets.token_hex(32)
        with self.transaction() as conn:
            account = conn.execute("SELECT generation FROM web_account WHERE singleton=1").fetchone()
            if not account or (generation is not None and generation != account['generation']):
                return None
            conn.execute("DELETE FROM web_sessions WHERE expires<=? OR seen<=?", (now, now-config.absolute_seconds))
            if old_digest:
                conn.execute("DELETE FROM web_sessions WHERE digest=?", (old_digest,))
            # Separate bounded pools: anonymous forms must never evict a login.
            # Legacy databases may already have 256 authenticated sessions;
            # refuse another anonymous form rather than invalidate those logins.
            if authenticated:
                self._trim_sessions(conn, False, ANONYMOUS_LIMIT)
                keep = AUTHENTICATED_LIMIT - 1
            else:
                count = conn.execute("SELECT COUNT(*) FROM web_sessions WHERE authenticated=1").fetchone()[0]
                capacity = min(ANONYMOUS_LIMIT, SESSION_LIMIT - count)
                if capacity <= 0:
                    return None
                keep = capacity - 1
            self._trim_sessions(conn, authenticated, keep)
            expires = now + (config.absolute_seconds if authenticated else config.prelogin_seconds)
            conn.execute("INSERT INTO web_sessions VALUES (?,?,?,?,?,?,?)", (token_digest(token), csrf, int(authenticated), account['generation'], now, now, expires))
        return token, {'digest': token_digest(token), 'csrf': csrf, 'authenticated': int(authenticated)}

    @staticmethod
    def _trim_sessions(conn, authenticated, keep):
        conn.execute("DELETE FROM web_sessions WHERE digest IN "
                     "(SELECT digest FROM web_sessions WHERE authenticated=? "
                     "ORDER BY seen DESC, created DESC, digest LIMIT -1 OFFSET ?)",
                     (int(authenticated), keep))

    def revoke(self, digest):
        with self.transaction() as conn:
            conn.execute("DELETE FROM web_sessions WHERE digest=?", (digest,))
