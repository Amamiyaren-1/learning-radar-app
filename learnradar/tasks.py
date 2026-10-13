"""2.1 持久手动队列；事务管占位与预算，系统文件锁管整个执行进程。"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

from .cycle import timezone_for
from .pipeline import classify_recommendation_result
from .store import Store

SCHEMA = """
CREATE TABLE IF NOT EXISTS task_environment (id INTEGER PRIMARY KEY CHECK(id=1), mode TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS discovery_cursor (id INTEGER PRIMARY KEY CHECK(id=1), cursor INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS tasks (
 task_id TEXT PRIMARY KEY, kind TEXT NOT NULL, topic TEXT NOT NULL,
 run_id TEXT NOT NULL UNIQUE, idempotency_key TEXT NOT NULL UNIQUE,
 status TEXT NOT NULL, phase TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 reserved_day TEXT, consumed_day TEXT, model_requests INTEGER NOT NULL DEFAULT 0,
 uncertain INTEGER NOT NULL DEFAULT 0, detail TEXT NOT NULL DEFAULT '',
 notify_pending INTEGER NOT NULL DEFAULT 0, worker_token TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS task_requests (
 task_id TEXT NOT NULL, attempt INTEGER NOT NULL, started_at TEXT NOT NULL,
 finished_at TEXT, status TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '',
 PRIMARY KEY(task_id,attempt)
);
CREATE TABLE IF NOT EXISTS task_events (
 id INTEGER PRIMARY KEY, task_id TEXT NOT NULL, ts TEXT NOT NULL, phase TEXT NOT NULL, detail TEXT NOT NULL
);
"""


class TaskError(RuntimeError):
    pass


class QuotaWait(TaskError):
    pass


def moment():
    return datetime.now(timezone_for("Asia/Shanghai"))


class WorkerLock:
    """内核释放的锁；不以 PID、文件年龄或超时推断旧进程已停止。"""
    def __init__(self, directory: Path):
        self.path = directory / "locks" / "manual-worker.lock"
        self.stream = None

    def acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = self.path.open("a+b")
        if self.path.stat().st_size == 0:
            self.stream.write(b"0")
            self.stream.flush()
        self.stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.stream.close()
            self.stream = None
            return False
        return True

    def release(self):
        if self.stream:
            self.stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_UN)
            self.stream.close()
            self.stream = None


class TaskQueue:
    def __init__(self, directory: Path, *, demo=False, daily_limit=3, clock=moment):
        self.directory = Path(directory).resolve()
        self.path = self.directory / "learning-radar.sqlite"
        self.clock = clock
        self.daily_limit = min(3, max(1, int(daily_limit)))
        self.demo = demo
        mode = "demo" if demo else "production"
        # 演示不能先迁移、再发现自己误用了真实库。
        if self.path.exists():
            connection = sqlite3.connect(f"{self.path.as_uri()}?mode=ro", uri=True)
            try:
                tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                saved = connection.execute("SELECT mode FROM task_environment WHERE id=1").fetchone() if "task_environment" in tables else None
                if (saved and saved[0] != mode) or (demo and tables and not saved):
                    raise TaskError("演示与正式数据目录不能混用；请选择新的隔离目录")
            finally:
                connection.close()
        with self.store() as store:
            store.conn.execute("BEGIN IMMEDIATE")
            store.conn.execute("INSERT OR IGNORE INTO task_environment VALUES(1,?)", (mode,))
            if store.conn.execute("SELECT mode FROM task_environment WHERE id=1").fetchone()[0] != mode:
                raise TaskError("演示与正式数据目录不能混用；请选择新的隔离目录")
            store.conn.commit()

    def store(self):
        return Store(self.path)

    def now(self):
        current = self.clock().astimezone(timezone_for("Asia/Shanghai"))
        return current.isoformat(timespec="seconds"), current.date().isoformat()

    def occupied(self, conn, day, excluding=""):
        return conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE budget_scope='manual' AND task_id<>? AND (consumed_day=? OR "
            "(consumed_day IS NULL AND reserved_day=? AND status IN ('queued','running','waiting_quota')))",
            (excluding, day, day),
        ).fetchone()[0]

    def _insert(self, conn, topic, key, *, options, kind, run_id, budget_scope, notify_mode, cycle_id, ts, day):
        payload=json.dumps(options,sort_keys=True,ensure_ascii=False)
        old=conn.execute("SELECT * FROM tasks WHERE idempotency_key=? OR run_id=?",(key,run_id)).fetchone()
        if old:
            saved_options = json.loads(old['payload'])
            if 'discovery_plan' not in options:
                saved_options.pop('discovery_plan', None)
            if (old["topic"],saved_options,old["kind"],old["budget_scope"],old["notify_mode"],old["cycle_id"]) != (topic,options,kind,budget_scope,notify_mode,cycle_id):
                raise TaskError("提交标识或运行编号已绑定不同输入，未覆盖原任务")
            return old["task_id"]
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,159}",run_id):
            raise TaskError("运行编号无效，只允许字母、数字、点、下划线和连字符")
        if budget_scope=="manual" and self.occupied(conn,day)>=self.daily_limit:
            raise QuotaWait("今天的手动任务额度已消费或预留完（最多 3 个）；可取消尚未执行的任务")
        if conn.execute("SELECT 1 FROM runs WHERE run_id=?",(run_id,)).fetchone() or conn.execute("SELECT 1 FROM reports WHERE run_id=?",(run_id,)).fetchone() or (self.directory/"runs"/run_id).exists():
            raise TaskError("运行编号已存在，未覆盖原运行或产物")
        identifier=uuid.uuid4().hex
        from .discovery import materialize_plan
        options = materialize_plan(conn, options)
        payload = json.dumps(options,sort_keys=True,ensure_ascii=False)
        conn.execute("INSERT INTO tasks(task_id,kind,topic,run_id,idempotency_key,status,phase,created_at,updated_at,reserved_day,payload,budget_scope,notify_mode,cycle_id) VALUES(?,?,?,?,?,'queued','queued',?,?,?,?,?,?,?)",
            (identifier,kind,topic,run_id,key,ts,ts,day if budget_scope=="manual" else None,payload,budget_scope,notify_mode,cycle_id))
        return identifier

    def submit(self, topic, key, *, options=None, kind="manual_recommendation",run_id=None,notify_mode="manual"):
        if not isinstance(key, str) or not 16 <= len(key) <= 128:
            raise TaskError("提交标识无效，请重新打开提交页面")
        ts, day = self.now()
        with self.store() as store:
            conn = store.conn
            conn.execute("BEGIN IMMEDIATE")
            identifier=self._insert(conn,topic,key,options=options or {},kind=kind,
                run_id=run_id or "manual-"+uuid.uuid4().hex,budget_scope="manual",notify_mode=notify_mode,cycle_id="",ts=ts,day=day)
            conn.commit()
        return self.get(identifier)

    def submit_weekly(self, topic, cycle_id, *, force_redo=False, options=None):
        ts,day=self.now()
        with self.store() as store:
            conn=store.conn
            conn.execute("BEGIN IMMEDIATE")
            cycle=conn.execute("SELECT * FROM cycles WHERE cycle_id=?",(cycle_id,)).fetchone()
            if cycle:
                if not force_redo:
                    conn.commit()
                    return self.get(cycle["task_id"]) if cycle["task_id"] else None
                if cycle["status"] not in {"failed","interrupted","unknown"}:
                    raise TaskError(f"拒绝强制重跑：周期状态 {cycle['status']}；只有 failed/interrupted/unknown 才允许")
                active=conn.execute("SELECT status FROM tasks WHERE task_id=?",(cycle["task_id"],)).fetchone()
                if active and active["status"] in {"queued","running","waiting_quota"}:
                    raise TaskError("周期绑定任务仍在处理，不能重复分析")
            run_id=f"{cycle_id}-{topic}"+(f"-redo-{uuid.uuid4().hex[:12]}" if cycle else "")
            identifier=self._insert(conn,topic,"weekly-"+uuid.uuid4().hex,options={**(options or {}),"cycle_id":cycle_id},
                kind="weekly_recommendation",run_id=run_id,budget_scope="weekly",notify_mode="weekly",cycle_id=cycle_id,ts=ts,day=day)
            conn.execute("INSERT INTO cycles(cycle_id,topic,run_id,status,started_at,task_id,notes) VALUES(?,?,?,'running',?,?,?) ON CONFLICT(cycle_id) DO UPDATE SET topic=excluded.topic,run_id=excluded.run_id,status='running',started_at=excluded.started_at,finished_at=NULL,task_id=excluded.task_id,notes=excluded.notes",
                (cycle_id,topic,run_id,ts,identifier,"人工 --force-redo 创建新任务，保留原任务与用量" if cycle else "统一队列已接单，尚未完成"))
            conn.commit()
        return self.get(identifier)

    def get(self, task_id):
        with self.store() as store:
            row = store.conn.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
            if not row:
                return None
            result = dict(row)
            result["input"] = json.loads(row["payload"])
            result["report"] = store.get_report(row["run_id"])
            run = store.conn.execute("SELECT status,notes FROM runs WHERE run_id=?", (row["run_id"],)).fetchone()
            result["run"] = dict(run) if run else None
            result["email"] = store.latest_email_for_run(row["run_id"])
            result["events"] = [dict(r) for r in store.conn.execute("SELECT * FROM task_events WHERE task_id=? ORDER BY id", (task_id,))]
            result["requests"] = [dict(r) for r in store.conn.execute("SELECT * FROM task_requests WHERE task_id=? ORDER BY attempt", (task_id,))]
            return result

    def list(self):
        with self.store() as store:
            return [dict(r) for r in store.conn.execute("SELECT * FROM tasks ORDER BY created_at DESC,rowid DESC LIMIT 50")]

    def claim(self, token):
        ts, day = self.now()
        with self.store() as store:
            conn = store.conn
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT 1 FROM tasks WHERE status='running'").fetchone():
                return None
            for row in conn.execute("SELECT * FROM tasks WHERE status IN ('queued','waiting_quota') ORDER BY created_at,rowid").fetchall():
                if row["budget_scope"]=="manual" and self.occupied(conn, day, row["task_id"]) >= self.daily_limit:
                    conn.execute("UPDATE tasks SET status='waiting_quota',reserved_day=NULL,detail=?,updated_at=? WHERE task_id=?",
                                 ("等待当天手动额度；旧预留已释放，没有请求模型", ts, row["task_id"]))
                    continue
                conn.execute("UPDATE tasks SET status='running',reserved_day=?,worker_token=?,detail='',updated_at=? WHERE task_id=?",
                             (day if row["budget_scope"]=="manual" else None, token, ts, row["task_id"]))
                conn.commit()
                return self.get(row["task_id"])
            conn.commit()
        return None

    def stage(self, task_id, phase):
        ts, _ = self.now()
        with self.store() as store:
            store.conn.execute("UPDATE tasks SET phase=?,updated_at=? WHERE task_id=?", (phase, ts, task_id))
            store.conn.execute("INSERT INTO task_events(task_id,ts,phase,detail) VALUES(?,?,?,'')", (task_id,ts,phase))
            store.conn.commit()

    def begin_request(self, task_id, limit=4):
        ts, day = self.now()
        with self.store() as store:
            conn = store.conn
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
            if not row or row["status"] != "running":
                raise TaskError("任务没有被执行进程领取，禁止请求模型")
            if row["model_requests"] >= min(4, limit):
                from .cli import ModelBudgetExceeded
                raise ModelBudgetExceeded("任务持久模型请求上限已用尽（最多 4 次，重启不重置）")
            if row["budget_scope"]=="manual" and not row["consumed_day"] and self.occupied(conn, day, task_id) >= self.daily_limit:
                conn.execute("UPDATE tasks SET status='waiting_quota',reserved_day=NULL,detail=?,updated_at=? WHERE task_id=?",
                             ("跨日后当天额度不足；等待新额度，尚未请求模型",ts,task_id))
                conn.commit()
                raise QuotaWait("跨日后当天额度不足")
            attempt = row["model_requests"] + 1
            conn.execute("UPDATE tasks SET consumed_day=COALESCE(consumed_day,?),reserved_day=NULL,model_requests=?,uncertain=1,updated_at=? WHERE task_id=?",
                         (day,attempt,ts,task_id))
            conn.execute("INSERT INTO task_requests(task_id,attempt,started_at,status) VALUES(?,?,?,'pending')", (task_id,attempt,ts))
            conn.commit()  # 请求发出前持久化；进程消失也保留此次占用。
            return attempt

    def end_request(self, task_id, attempt, status):
        ts, _ = self.now()
        with self.store() as store:
            store.conn.execute("UPDATE task_requests SET status=?,finished_at=? WHERE task_id=? AND attempt=?", (status,ts,task_id,attempt))
            store.conn.execute("UPDATE tasks SET uncertain=EXISTS(SELECT 1 FROM task_requests WHERE task_id=? AND status IN ('pending','unknown')) WHERE task_id=?", (task_id,task_id))
            store.conn.commit()

    def save_exit_code(self, task_id, code):
        with self.store() as store:
            store.conn.execute("UPDATE tasks SET exit_code=? WHERE task_id=?",(code,task_id))
            store.conn.commit()

    def finish(self, task_id, status, detail="", *, exit_code=None):
        ts, _ = self.now()
        with self.store() as store:
            if status in ("failed","interrupted"):
                store.conn.execute("UPDATE task_requests SET status='unknown' WHERE task_id=? AND status='pending'",(task_id,))
                store.conn.execute("UPDATE runs SET status=?,finished_at=?,notes=? WHERE run_id=(SELECT run_id FROM tasks WHERE task_id=?) AND status='running'",(status,ts,detail,task_id))
            store.conn.execute("UPDATE tasks SET status=?,reserved_day=NULL,detail=?,updated_at=?,exit_code=COALESCE(?,exit_code) WHERE task_id=?", (status,detail,ts,exit_code,task_id))
            store.conn.commit()

    def recover(self, lock):
        """只能在取得 WorkerLock 后调用：锁证实旧执行进程不再持有它。"""
        if not lock.stream or lock.path != self.directory/"locks"/"manual-worker.lock":
            raise TaskError("恢复必须先持有该数据目录的系统执行锁")
        ts, _ = self.now()
        with self.store() as store:
            store.conn.execute("BEGIN IMMEDIATE")
            for row in store.conn.execute("SELECT * FROM tasks WHERE status='running'").fetchall():
                report = store.get_report(row["run_id"])
                run = store.conn.execute("SELECT status FROM runs WHERE run_id=?", (row["run_id"],)).fetchone()
                processing = classify_recommendation_result(
                    run["status"] if run else "", report["status"] if report else ""
                ) or "interrupted"
                if report:
                    store.conn.execute("UPDATE runs SET status=?,finished_at=? WHERE run_id=? AND status='running'",(report["status"],ts,row["run_id"]))
                else:
                    store.conn.execute("UPDATE runs SET status='interrupted',finished_at=?,notes='网页队列执行进程已退出；未自动重跑' WHERE run_id=? AND status='running'",(ts,row["run_id"]))
                store.conn.execute("UPDATE task_requests SET status='unknown' WHERE task_id=? AND status='pending'", (row["task_id"],))
                store.conn.execute("UPDATE tasks SET status=?,reserved_day=NULL,detail=?,updated_at=? WHERE task_id=?",
                    (processing, "旧执行进程已退出；按保存结果恢复处理状态，保留阶段及请求次数，不重跑分析。通知结果不明时需人工核对。",ts,row["task_id"]))
                if row["cycle_id"]:
                    from .cycle import classify_cycle
                    state=classify_cycle(run["status"] if run else "",report["status"] if report else "","")[0] if processing!="interrupted" else "interrupted"
                    store.conn.execute("UPDATE cycles SET status=?,finished_at=?,notes='执行进程已退出；按保存结果恢复，不自动重跑或重发' WHERE cycle_id=? AND task_id=?",(state,ts,row["cycle_id"],row["task_id"]))
            store.conn.commit()

    def cancel(self, task_id):
        ts, _ = self.now()
        with self.store() as store:
            store.conn.execute("BEGIN IMMEDIATE")
            row=store.conn.execute("SELECT cycle_id,budget_scope FROM tasks WHERE task_id=?",(task_id,)).fetchone()
            if row and (row["cycle_id"] or row["budget_scope"]=="weekly"):
                raise TaskError("定时周报任务暂不支持取消；取消仅适用于手动任务")
            cursor=store.conn.execute("UPDATE tasks SET status='cancelled',reserved_day=NULL,detail='用户取消未执行任务',updated_at=? WHERE task_id=? AND status IN ('queued','waiting_quota') AND model_requests=0",(ts,task_id))
            store.conn.commit()
            if not cursor.rowcount:
                raise TaskError("只能取消尚未执行且没有模型请求的任务")

    def retry_email(self, task_id):
        with self.store() as store:
            store.conn.execute("BEGIN IMMEDIATE")
            row=store.conn.execute("SELECT * FROM tasks WHERE task_id=?",(task_id,)).fetchone()
            if not row or row["status"] not in ("completed","failed","interrupted"):
                raise TaskError("任务尚未结束")
            email=store.latest_email_for_run(row["run_id"])
            if email and email["status"] not in ("failed","queued"):
                raise TaskError("只允许重试明确失败的邮件；unknown 必须先人工核对，sent 不重发")
            if not email and not store.conn.execute("SELECT 1 FROM runs WHERE run_id=?",(row["run_id"],)).fetchone():
                raise TaskError("没有保存结果，不能猜测通知内容")
            store.conn.execute("UPDATE tasks SET notify_pending=1 WHERE task_id=?",(task_id,))
            store.conn.commit()

    def claim_email_retry(self):
        with self.store() as store:
            store.conn.execute("BEGIN IMMEDIATE")
            row=store.conn.execute("SELECT * FROM tasks WHERE notify_pending=1 ORDER BY updated_at LIMIT 1").fetchone()
            if row:
                store.conn.execute("UPDATE tasks SET notify_pending=0 WHERE task_id=?",(row["task_id"],))
            store.conn.commit()
            return self.get(row["task_id"]) if row else None


class TaskBudget:
    def __init__(self, queue, task_id, limit=4):
        self.queue, self.task_id, self.limit = queue, task_id, min(4, limit)

    @property
    def used(self):
        return self.queue.get(self.task_id)["model_requests"]

    @property
    def remaining(self):
        return max(0,self.limit-self.used)

    def consume(self):
        return self.queue.begin_request(self.task_id,self.limit)

    def finish_request(self, attempt, status):
        self.queue.end_request(self.task_id,attempt,status)

    def describe(self):
        return f"{self.used}/{self.limit}"
