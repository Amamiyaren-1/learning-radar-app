"""Execute each cycle once, binding run_id with a single executor and no automatic rerun.

SQLite cycles records provide same-cycle deduplication; cooldown handles cross-cycle selection.
Preserve interrupted/unknown/failed states until explicit --force-redo.
SIGALRM enforces timeouts on POSIX; Windows needs an external supervisor."""

from __future__ import annotations

import json
import os
import re
import signal
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .evidence import Evidence
from .net import now_iso

CYCLE_RUNNING = "running"
CYCLE_COMPLETED = "completed"
CYCLE_FAILED = "failed"
CYCLE_INTERRUPTED = "interrupted"
CYCLE_UNKNOWN = "unknown"

# Completed cycles never rerun automatically, regardless of recommendation outcome.
CYCLE_TERMINAL = {CYCLE_COMPLETED, CYCLE_FAILED, CYCLE_INTERRUPTED, CYCLE_UNKNOWN}

_FALLBACK_OFFSETS = {"Asia/Shanghai": 8, "UTC": 0, "Etc/UTC": 0}


class TaskTimeout(RuntimeError):
    """Task-wide timeout."""


@dataclass(frozen=True)
class ScheduleConfig:
    timezone: str = "Asia/Shanghai"
    weekday: str = "Sun"
    at: str = "20:17"
    topic: str = ""
    task_timeout_seconds: int = 3600
    attach_markdown: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "timezone": self.timezone,
            "weekday": self.weekday,
            "at": self.at,
            "topic": self.topic,
            "task_timeout_seconds": self.task_timeout_seconds,
            "attach_markdown": self.attach_markdown,
        }


def timezone_for(name: str) -> timezone | Any:
    """Prefer system tzdata; fall back to a fixed offset if IANA data is unavailable."""
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name)
    except Exception:
        hours = _FALLBACK_OFFSETS.get(name)
        if hours is None:
            raise ValueError(
                f"无法加载时区 {name!r}：Linux 用系统 tzdata；Windows 需要 pip install tzdata"
            ) from None
        return timezone(timedelta(hours=hours))


def schedule_config(settings: Any, *, topic_id: str = "") -> ScheduleConfig:
    raw = getattr(settings, "schedule", None) or {}

    def _int(key: str, default: int) -> int:
        try:
            return int(raw.get(key, default))
        except (TypeError, ValueError):
            return default

    return ScheduleConfig(
        timezone=str(raw.get("timezone", "Asia/Shanghai")),
        weekday=str(raw.get("weekday", "Sun")),
        at=str(raw.get("at", "20:17")),
        topic=topic_id or str(raw.get("topic", "")),
        task_timeout_seconds=_int("task_timeout_seconds", 3600),
        attach_markdown=bool(raw.get("attach_markdown", True)),
    )


def cycle_id_for(moment: datetime) -> str:
    iso = moment.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def current_time(schedule: ScheduleConfig) -> datetime:
    return datetime.now(timezone_for(schedule.timezone))


_WEEKDAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


def weekday_index(name: str) -> int:
    key = str(name or "").strip().lower()[:3]
    if key not in _WEEKDAYS:
        raise ValueError(f"无法识别的 weekday {name!r}（用 Mon..Sun）")
    return _WEEKDAYS[key]


def parse_at(text: str) -> tuple[int, int]:
    parts = str(text or "").strip().split(":")
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        raise ValueError(f"无法识别的 at {text!r}（用 HH:MM）")
    hour, minute = int(parts[0]), int(parts[1])
    if not (0 <= hour < 24 and 0 <= minute < 60):
        raise ValueError(f"at 超出范围：{text!r}")
    return hour, minute


def scheduled_instant(schedule: ScheduleConfig, now: datetime) -> datetime:
    """Find the most recent scheduled instant, including now, for delayed cycle attribution."""
    hour, minute = parse_at(schedule.at)
    target = weekday_index(schedule.weekday)
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    candidate -= timedelta(days=(candidate.weekday() - target) % 7)
    if candidate > now:
        candidate -= timedelta(days=7)
    return candidate


class TaskLock:
    """Allow one weekly instance using O_EXCL; remove the lock on normal exit."""

    def __init__(self, path: Path, *, stale_seconds: int = 0) -> None:
        self.path = path
        self.stale_seconds = stale_seconds
        self.acquired = False

    def acquire(self) -> tuple[bool, str]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            handle = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            detail = f"锁文件已存在：{self.path.name}"
            try:
                age = time.time() - self.path.stat().st_mtime
            except OSError:
                age = 0
            if self.stale_seconds and age > self.stale_seconds:
                detail += (
                    f"；看起来已过期（{int(age)} 秒 > {self.stale_seconds} 秒），"
                    "确认没有进程在跑后可手动删除它"
                )
            return False, detail
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(f"pid={os.getpid()} started_at={now_iso()}\n")
        self.acquired = True
        return True, ""

    def holder_pid(self) -> int:
        """Read the lock owner PID; return zero if unreadable."""
        try:
            first = self.path.read_text(encoding="utf-8").splitlines()[0]
        except (OSError, IndexError):
            return 0
        match = re.search(r"pid=(\d+)", first)
        return int(match.group(1)) if match else 0

    def holder_alive(self) -> bool:
        """Check whether a live process owns the lock; uncertainty is treated as alive."""
        pid = self.holder_pid()
        if pid <= 0:
            return self.path.exists()
        if os.name != "posix" or not hasattr(os, "kill"):
            # On Windows, os.kill(pid, 0) can terminate a process rather than probe it.
            # Conservatively treat non-POSIX owners as alive to prevent false recovery or termination.
            return True
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except (PermissionError, OSError):
            return True
        return True

    def release(self) -> None:
        if self.acquired:
            try:
                self.path.unlink()
            except OSError:
                pass
        self.acquired = False

    def __enter__(self) -> "TaskLock":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.release()


def install_task_timeout(seconds: int) -> str:
    """Return a log description; Windows lacks SIGALRM and needs an external timeout supervisor."""
    if seconds <= 0:
        return "未启用（配置为 0）"
    if not hasattr(signal, "SIGALRM"):
        return f"本平台不支持 SIGALRM；请靠 systemd TimeoutStartSec={seconds} 兜底"

    def _handler(signum: int, frame: Any) -> None:
        raise TaskTimeout(f"任务超过总超时 {seconds} 秒")

    signal.signal(signal.SIGALRM, _handler)
    signal.alarm(int(seconds))
    return f"已启用 {seconds} 秒（SIGALRM）"


def clear_task_timeout() -> None:
    if hasattr(signal, "SIGALRM"):
        signal.alarm(0)


def stale_seconds(schedule: ScheduleConfig) -> int:
    return max(60, int(schedule.task_timeout_seconds) + 600)


def is_stale(started_at: str, *, limit_seconds: int) -> bool:
    from .history import parse_time

    moment = parse_time(started_at)
    if moment is None:
        return False
    return (datetime.now(timezone.utc) - moment).total_seconds() > limit_seconds


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _json_field(raw: Any) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def collect_outcome(store: Any, settings: Any, run_id: str, *, cycle_id: str = "") -> dict[str, Any]:
    """Read-only aggregation of run fields needed for mail rendering."""
    row = store.conn.execute(
        "SELECT status, started_at, finished_at FROM runs WHERE run_id = ?", (run_id,)
    ).fetchone()
    report = store.get_report(run_id)
    run_dir = settings.data_dir / "runs" / run_id
    run_context = _read_json(run_dir / "run-context.json")
    usage = _read_json(run_dir / "usage.json")
    payload: dict[str, Any] = {}
    validation: dict[str, Any] = {}
    report_status = ""
    markdown_path = ""
    created_at = ""
    if report:
        report_status = str(report.get("status") or "")
        markdown_path = str(report.get("markdown_path") or "")
        created_at = str(report.get("created_at") or "")
        payload = _json_field(report.get("payload"))
        validation = _json_field(report.get("validation"))
    target = ""
    if isinstance(payload.get("target"), dict):
        target = str(payload["target"].get("repo") or "")
    if not target:
        target = str(run_context.get("target") or "")
    recommendation = payload.get("recommendation") if isinstance(payload.get("recommendation"), dict) else {}
    evidence: list[Evidence] = []
    evidence_path = run_dir / "evidence.json"
    if evidence_path.exists():
        try:
            evidence = [
                Evidence(**item)
                for item in json.loads(evidence_path.read_text(encoding="utf-8"))
            ]
        except (TypeError, ValueError, json.JSONDecodeError):
            evidence = []
    found = row is not None or report is not None or bool(run_context)
    return {
        "found": found,
        "run_id": run_id,
        "cycle_id": cycle_id or str(run_context.get("cycle") or ""),
        "topic": str(run_context.get("topic") or ""),
        "target": target,
        "run_status": str((row["status"] if row else "") or ""),
        "report_status": report_status,
        "decision": str((recommendation or {}).get("decision") or ""),
        "payload": payload,
        "validation": validation,
        "evidence": evidence,
        "run_context": run_context,
        "usage": usage,
        "markdown_path": markdown_path,
        "created_at": created_at,
        "started_at": str((row["started_at"] if row else "") or ""),
        "finished_at": str((row["finished_at"] if row else "") or ""),
    }


def classify_cycle(run_status: str, report_status: str, decision: str) -> tuple[str, str]:
    """Return cycle status and mail type; an observation-only result is normal completion."""
    if report_status == "published":
        kind = "report" if decision in {"recommend", "watch"} else "status"
        return CYCLE_COMPLETED, kind
    if report_status in {"rejected", "render-failed"}:
        return CYCLE_COMPLETED, "failure"
    if run_status in {"all-candidates-in-cooldown", "no-suitable-candidate"}:
        return CYCLE_COMPLETED, "status"
    return CYCLE_FAILED, "failure"
