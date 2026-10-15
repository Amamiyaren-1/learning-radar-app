"""SQLite persistence for runs, HTTP audit, candidates, evidence, reports, and usage."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any, Iterable

from .evidence import Evidence, normalize_repo_name

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    notes TEXT,
    config_snapshot TEXT
);
CREATE TABLE IF NOT EXISTS http_log (
    run_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    method TEXT,
    url TEXT,
    status INTEGER,
    ms INTEGER,
    bytes INTEGER,
    error TEXT
);
CREATE TABLE IF NOT EXISTS candidates (
    run_id TEXT NOT NULL,
    repo_id TEXT,
    full_name TEXT NOT NULL,
    html_url TEXT,
    stars INTEGER,
    pushed_at TEXT,
    role TEXT,
    description TEXT,
    raw TEXT
);
CREATE TABLE IF NOT EXISTS evidence (
    run_id TEXT NOT NULL,
    evidence_id TEXT NOT NULL,
    kind TEXT,
    url TEXT,
    title TEXT,
    retrieved_at TEXT,
    source_type TEXT,
    commit_sha TEXT,
    sha256 TEXT,
    locator TEXT,
    excerpt TEXT,
    payload TEXT,
    PRIMARY KEY (run_id, evidence_id)
);
CREATE TABLE IF NOT EXISTS reports (
    run_id TEXT NOT NULL,
    report_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL,
    title TEXT,
    markdown_path TEXT,
    html_path TEXT,
    payload TEXT,
    validation TEXT,
    PRIMARY KEY (run_id, report_id)
);
CREATE TABLE IF NOT EXISTS usage_log (
    run_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    stage TEXT,
    provider TEXT,
    model_id TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    reasoning_tokens INTEGER,
    calls INTEGER,
    cost_value REAL,
    cost_currency TEXT,
    pricing_known INTEGER,
    ok INTEGER,
    error TEXT
);
CREATE TABLE IF NOT EXISTS feedback (
    run_id TEXT PRIMARY KEY,
    repo TEXT NOT NULL,
    answered_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    understood_what TEXT,
    understood_highlight TEXT,
    want_more TEXT,
    note TEXT
);
CREATE TABLE IF NOT EXISTS cycles (
    cycle_id TEXT PRIMARY KEY,
    topic TEXT,
    run_id TEXT,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    notes TEXT
);
CREATE TABLE IF NOT EXISTS emails (
    email_id TEXT PRIMARY KEY,
    cycle_id TEXT,
    run_id TEXT,
    kind TEXT,
    recipient TEXT,
    subject TEXT,
    body TEXT,
    attachment_path TEXT,
    status TEXT NOT NULL,
    attempts INTEGER DEFAULT 0,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    sent_at TEXT
);
"""


def backup_database(source: Path, target: Path) -> None:
    """Use SQLite online backup for consistent snapshots instead of copying a live database file."""
    target.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(source)) as origin, closing(sqlite3.connect(target)) as out:
        origin.backup(out)


class Store:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.conn = sqlite3.connect(path, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        from .auth_store import SCHEMA as AUTH_SCHEMA
        self.conn.executescript(AUTH_SCHEMA)
        from .tasks import SCHEMA as TASK_SCHEMA
        self.conn.executescript(TASK_SCHEMA)
        from .archive import SCHEMA as ARCHIVE_SCHEMA
        self.conn.executescript(ARCHIVE_SCHEMA)
        self.conn.execute("BEGIN IMMEDIATE")
        self._migrate()
        from .archive import import_old_reports
        import_old_reports(self.conn)
        self.conn.commit()

    def _migrate(self) -> None:
        """Migrate older databases by adding usage_log ok/error failure fields."""
        columns = {row[1] for row in self.conn.execute("PRAGMA table_info(usage_log)")}
        for name, ddl in (
            ("ok", "ALTER TABLE usage_log ADD COLUMN ok INTEGER"),
            ("error", "ALTER TABLE usage_log ADD COLUMN error TEXT"),
        ):
            if name not in columns:
                self.conn.execute(ddl)
        for table, additions in {
            "tasks": {"payload":"TEXT NOT NULL DEFAULT '{}'", "budget_scope":"TEXT NOT NULL DEFAULT 'manual'",
                      "notify_mode":"TEXT NOT NULL DEFAULT 'manual'", "cycle_id":"TEXT NOT NULL DEFAULT ''",
                      "exit_code":"INTEGER"},
            "cycles": {"task_id":"TEXT"},
        }.items():
            columns={row[1] for row in self.conn.execute(f"PRAGMA table_info({table})")}
            for name,definition in additions.items():
                if name not in columns:
                    self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")

    def close(self) -> None:
        self.conn.close()

    def __del__(self) -> None:  # pragma: no cover - fallback
        """Release SQLite files if a caller forgot close()."""
        try:
            self.conn.close()
        except Exception:
            pass

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    def start_run(self, run_id: str, *, kind: str, started_at: str, snapshot: dict[str, Any]) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO runs(run_id, kind, started_at, status, config_snapshot) VALUES (?,?,?,?,?)",
            (run_id, kind, started_at, "running", json.dumps(snapshot, ensure_ascii=False)),
        )
        self.conn.commit()

    def finish_run(self, run_id: str, *, status: str, finished_at: str, notes: str = "") -> None:
        self.conn.execute(
            "UPDATE runs SET status=?, finished_at=?, notes=? WHERE run_id=?",
            (status, finished_at, notes, run_id),
        )
        self.conn.commit()

    def log_http(self, run_id: str, entries: Iterable[dict[str, Any]]) -> None:
        rows = [
            (
                run_id,
                item.get("ts"),
                item.get("method"),
                item.get("url"),
                item.get("status"),
                item.get("ms"),
                item.get("bytes"),
                item.get("error"),
            )
            for item in entries
        ]
        if rows:
            self.conn.executemany(
                "INSERT INTO http_log(run_id, ts, method, url, status, ms, bytes, error) VALUES (?,?,?,?,?,?,?,?)",
                rows,
            )
            self.conn.commit()

    def save_candidates(self, run_id: str, items: Iterable[dict[str, Any]], *, role: str) -> None:
        rows = [
            (
                run_id,
                str(item.get("id", "")),
                item.get("full_name"),
                item.get("html_url"),
                item.get("stargazers_count"),
                item.get("pushed_at"),
                role,
                item.get("description"),
                json.dumps(item, ensure_ascii=False)[:20000],
            )
            for item in items
        ]
        if rows:
            self.conn.executemany(
                "INSERT INTO candidates(run_id, repo_id, full_name, html_url, stars, pushed_at, role, description, raw)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                rows,
            )
            self.conn.commit()

    def save_evidence(self, run_id: str, items: Iterable[Evidence]) -> None:
        rows = [
            (
                run_id,
                item.evidence_id,
                item.kind,
                item.url,
                item.title,
                item.retrieved_at,
                item.source_type,
                item.commit_sha,
                item.sha256,
                item.locator,
                item.excerpt,
                json.dumps(item.to_dict(), ensure_ascii=False),
            )
            for item in items
        ]
        if rows:
            self.conn.executemany(
                "INSERT OR REPLACE INTO evidence(run_id, evidence_id, kind, url, title, retrieved_at,"
                " source_type, commit_sha, sha256, locator, excerpt, payload)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                rows,
            )
            self.conn.commit()

    def save_report(
        self,
        run_id: str,
        *,
        report_id: str,
        created_at: str,
        status: str,
        title: str,
        markdown_path: str,
        html_path: str,
        payload: dict[str, Any],
        validation: dict[str, Any],
    ) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO reports(run_id, report_id, created_at, status, title, markdown_path,"
            " html_path, payload, validation) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                run_id,
                report_id,
                created_at,
                status,
                title,
                markdown_path,
                html_path,
                json.dumps(payload, ensure_ascii=False),
                json.dumps(validation, ensure_ascii=False),
            ),
        )
        from .archive import archive_report
        row = self.conn.execute('SELECT * FROM reports WHERE run_id=? AND report_id=?', (run_id,report_id)).fetchone()
        archive_report(self.conn, row, assistant=True)
        self.conn.commit()

    def save_usage(self, run_id: str, entries: Iterable[dict[str, Any]]) -> None:
        rows = [
            (
                run_id,
                item.get("ts"),
                item.get("stage"),
                item.get("provider"),
                item.get("model_id"),
                item.get("input_tokens"),
                item.get("output_tokens"),
                item.get("reasoning_tokens"),
                item.get("calls"),
                item.get("cost_value"),
                item.get("cost_currency"),
                1 if item.get("pricing_known") else 0,
                1 if item.get("ok", True) else 0,
                item.get("error"),
            )
            for item in entries
        ]
        if rows:
            self.conn.executemany(
                "INSERT INTO usage_log(run_id, ts, stage, provider, model_id, input_tokens, output_tokens,"
                " reasoning_tokens, calls, cost_value, cost_currency, pricing_known, ok, error)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                rows,
            )
            self.conn.commit()

    def list_reports(self, *, limit: int = 20) -> list[dict[str, Any]]:
        cursor = self.conn.execute(
            "SELECT r.run_id, r.report_id, r.created_at, r.status, r.title, r.markdown_path, r.html_path,"
            " r.validation,"
            " (SELECT model_id FROM usage_log u WHERE u.run_id = r.run_id ORDER BY u.rowid LIMIT 1) AS model_id,"
            " (SELECT SUM(input_tokens) FROM usage_log u WHERE u.run_id = r.run_id) AS input_tokens,"
            " (SELECT SUM(output_tokens) FROM usage_log u WHERE u.run_id = r.run_id) AS output_tokens,"
            " (SELECT SUM(cost_value) FROM usage_log u WHERE u.run_id = r.run_id) AS cost_value,"
            " (SELECT cost_currency FROM usage_log u WHERE u.run_id = r.run_id ORDER BY u.rowid LIMIT 1)"
            "   AS cost_currency"
            " FROM reports r"
            " ORDER BY r.created_at DESC LIMIT ?",
            (limit,),
        )
        return [dict(row) for row in cursor.fetchall()]

    def get_report(self, run_id: str) -> dict[str, Any] | None:
        cursor = self.conn.execute("SELECT * FROM reports WHERE run_id = ?", (run_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

    def list_candidates(self, run_id: str) -> list[dict[str, Any]]:
        """Retrieve saved candidates without searching again."""
        cursor = self.conn.execute(
            "SELECT repo_id, full_name, html_url, stars, pushed_at, role, description, raw"
            " FROM candidates WHERE run_id = ? ORDER BY rowid",
            (run_id,),
        )
        return [dict(row) for row in cursor.fetchall()]

    def get_evidence(self, run_id: str) -> list[dict[str, Any]]:
        cursor = self.conn.execute(
            "SELECT * FROM evidence WHERE run_id = ? ORDER BY evidence_id", (run_id,)
        )
        return [dict(row) for row in cursor.fetchall()]

    def usage_for_run(self, run_id: str) -> list[dict[str, Any]]:
        cursor = self.conn.execute("SELECT * FROM usage_log WHERE run_id = ?", (run_id,))
        return [dict(row) for row in cursor.fetchall()]

    def save_feedback(
        self,
        run_id: str,
        repo: str,
        answers: dict[str, Any],
        note: str,
        *,
        now: str,
    ) -> dict[str, Any]:
        """Update repeated report feedback without new rows; retain the original answered_at."""
        existing = self.get_feedback(run_id)
        answered_at = str(existing["answered_at"]) if existing else now
        self.conn.execute(
            "INSERT INTO feedback(run_id, repo, answered_at, updated_at, understood_what,"
            " understood_highlight, want_more, note) VALUES (?,?,?,?,?,?,?,?)"
            " ON CONFLICT(run_id) DO UPDATE SET repo=excluded.repo, updated_at=excluded.updated_at,"
            " understood_what=excluded.understood_what,"
            " understood_highlight=excluded.understood_highlight,"
            " want_more=excluded.want_more, note=excluded.note",
            (
                run_id,
                normalize_repo_name(repo),
                answered_at,
                now,
                str(answers.get("understood_what") or ""),
                str(answers.get("understood_highlight") or ""),
                str(answers.get("want_more") or ""),
                str(note or ""),
            ),
        )
        self.conn.commit()
        return self.get_feedback(run_id) or {}

    def get_feedback(self, run_id: str) -> dict[str, Any] | None:
        cursor = self.conn.execute("SELECT * FROM feedback WHERE run_id = ?", (run_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

    def feedback_for_repos(self, repos: Iterable[str]) -> list[dict[str, Any]]:
        """Get latest feedback and historical count for each project across its runs."""
        wanted: list[str] = []
        for name in repos:
            normalized = normalize_repo_name(name)
            if normalized and normalized not in wanted:
                wanted.append(normalized)
        rows: list[dict[str, Any]] = []
        for repo in wanted:
            cursor = self.conn.execute(
                "SELECT * FROM feedback WHERE repo = ? ORDER BY updated_at DESC, rowid DESC LIMIT 1",
                (repo,),
            )
            row = cursor.fetchone()
            if not row:
                continue
            item = dict(row)
            count = self.conn.execute(
                "SELECT COUNT(*) AS c FROM feedback WHERE repo = ?", (repo,)
            ).fetchone()
            item["history"] = int(count["c"]) if count else 1
            rows.append(item)
        return rows

    # ---- Cycle records: execute each cycle once and bind run_id ----
    def claim_cycle(self, cycle_id: str, *, topic: str, started_at: str) -> bool:
        """Atomically reserve a running cycle for one executor."""
        cursor = self.conn.execute(
            "INSERT INTO cycles(cycle_id, topic, run_id, status, started_at, finished_at, notes)"
            " VALUES (?,?,?,?,?,?,?) ON CONFLICT(cycle_id) DO NOTHING",
            (cycle_id, topic, "", "running", started_at, "", ""),
        )
        self.conn.commit()
        return cursor.rowcount == 1

    def get_cycle(self, cycle_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM cycles WHERE cycle_id = ?", (cycle_id,)).fetchone()
        return dict(row) if row else None

    def save_cycle(
        self,
        cycle_id: str,
        *,
        status: str,
        topic: str = "",
        run_id: str = "",
        started_at: str = "",
        finished_at: str = "",
        notes: str = "",
    ) -> None:
        """Update existing status/notes without replacing the original started_at."""
        existing = self.get_cycle(cycle_id)
        if existing is None:
            self.conn.execute(
                "INSERT INTO cycles(cycle_id, topic, run_id, status, started_at, finished_at, notes)"
                " VALUES (?,?,?,?,?,?,?)",
                (cycle_id, topic, run_id, status, started_at, finished_at, notes),
            )
        else:
            self.conn.execute(
                "UPDATE cycles SET topic = ?, run_id = ?, status = ?, finished_at = ?, notes = ?"
                " WHERE cycle_id = ?",
                (
                    topic or existing.get("topic") or "",
                    run_id or existing.get("run_id") or "",
                    status,
                    finished_at,
                    notes,
                    cycle_id,
                ),
            )
        self.conn.commit()

    def list_cycles(self, *, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM cycles ORDER BY started_at DESC, rowid DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(row) for row in rows]

    # ---- Mail records: persist before sending; retry saved content only ----
    def create_email(
        self,
        email_id: str,
        *,
        cycle_id: str,
        run_id: str,
        kind: str,
        recipient: str,
        subject: str,
        body: str,
        attachment_path: str = "",
        status: str = "queued",
        now: str = "",
        replace: bool = False,
    ) -> None:
        """Save each mail once; replace=True updates content while retaining status and retry count."""
        conflict = (
            " DO UPDATE SET kind=excluded.kind, subject=excluded.subject, body=excluded.body,"
            " attachment_path=excluded.attachment_path, cycle_id=excluded.cycle_id,"
            " updated_at=excluded.updated_at"
            if replace
            else " DO NOTHING"
        )
        self.conn.execute(
            "INSERT INTO emails(email_id, cycle_id, run_id, kind, recipient, subject, body,"
            " attachment_path, status, attempts, error, created_at, updated_at, sent_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(email_id)" + conflict,
            (
                email_id,
                cycle_id,
                run_id,
                kind,
                recipient,
                subject,
                body,
                attachment_path,
                status,
                0,
                "",
                now,
                now,
                "",
            ),
        )
        self.conn.commit()

    def get_email(self, email_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM emails WHERE email_id = ?", (email_id,)).fetchone()
        return dict(row) if row else None

    def latest_email_for_run(self, run_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM emails WHERE run_id = ? ORDER BY updated_at DESC, rowid DESC LIMIT 1",
            (run_id,),
        ).fetchone()
        return dict(row) if row else None

    def latest_email_for_cycle(self, cycle_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM emails WHERE cycle_id = ? ORDER BY updated_at DESC, rowid DESC LIMIT 1",
            (cycle_id,),
        ).fetchone()
        return dict(row) if row else None

    def update_email(
        self,
        email_id: str,
        *,
        status: str,
        error: str = "",
        now: str = "",
        sent_at: str = "",
        counted_attempt: bool = True,
    ) -> None:
        self.conn.execute(
            "UPDATE emails SET status = ?, error = ?, updated_at = ?, sent_at = ?,"
            " attempts = COALESCE(attempts, 0) + ? WHERE email_id = ?",
            (status, error, now, sent_at, 1 if counted_attempt else 0, email_id),
        )
        self.conn.commit()

    def list_emails(self, *, limit: int = 10) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM emails ORDER BY updated_at DESC, rowid DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(row) for row in rows]
