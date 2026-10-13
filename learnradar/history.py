"""冷却期过滤：按已有运行记录判断“这个项目最近是不是刚处理过”。

只读现有记录（SQLite 的 `runs` / `reports`，以及 `data/runs/<run_id>/run-context.json`），
不新建“已分析项目名单”。规则与字段缺失时的处理见 `docs/RUNBOOK.md` 的“冷却期”一节。

要点：
- 只统计**作为目标**的运行；只被搜索到、只被初筛、只当对照的项目不参与；
- 成功（结构检查通过并发布）默认 30 天，失败（生成失败 / 结构拒绝 / 渲染失败）默认 7 天；
- 冷却期取两者的最晚值：成功还在冷却期内时，后续一次失败不会把它缩短成 7 天；
- 历史字段缺失时记为“无法判定”，并按可参与处理，不猜测成功或失败；
- 用户反馈（例如“不想进一步了解”）不参与这里的任何判断。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from .evidence import normalize_repo_name
from .store import Store

OUTCOME_SUCCESS = "success"
OUTCOME_FAILURE = "failure"
OUTCOME_UNKNOWN = "unknown"

STATE_SKIPPED = "skipped"
STATE_ALLOWED = "allowed"
STATE_UNKNOWN = "unknown"

SUCCESS_REPORT_STATUSES = {"published"}
FAILURE_REPORT_STATUSES = {"rejected", "render-failed"}
FAILURE_RUN_STATUSES = {"model-failed"}

REASON_SUCCESS = "近期已作为目标完成分析并通过结构检查（已发布）"
REASON_REJECTED = "近期作为目标分析但结构检查未通过（未发布）"
REASON_RENDER_FAILED = "近期作为目标分析但渲染失败"
REASON_MODEL_FAILED = "近期作为目标分析但生成失败"
REASON_UNKNOWN = "历史记录不完整，无法判定成功或失败"
REASON_NO_HISTORY = "没有作为目标的历史记录"
REASON_EXPIRED = "冷却期已过，可以正常参与"


@dataclass(frozen=True)
class HistoryRecord:
    repo: str
    run_id: str
    run_status: str
    report_status: str
    finished_at: str
    outcome: str
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "run_status": self.run_status,
            "report_status": self.report_status,
            "finished_at": self.finished_at,
            "outcome": self.outcome,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class CooldownConfig:
    success_days: int = 30
    failure_days: int = 7

    def as_dict(self) -> dict[str, int]:
        return {"success_days": self.success_days, "failure_days": self.failure_days}

    def window_days(self, outcome: str) -> int:
        if outcome == OUTCOME_SUCCESS:
            return max(0, self.success_days)
        if outcome == OUTCOME_FAILURE:
            return max(0, self.failure_days)
        return 0


@dataclass(frozen=True)
class CooldownDecision:
    repo: str
    full_name: str
    state: str
    reason: str
    until: str = ""
    days: int = 0
    record: HistoryRecord | None = None
    history: tuple[HistoryRecord, ...] = field(default_factory=tuple)

    @property
    def blocked(self) -> bool:
        return self.state == STATE_SKIPPED

    def as_dict(self) -> dict[str, Any]:
        return {
            "repo": self.repo,
            "full_name": self.full_name,
            "state": self.state,
            "reason": self.reason,
            "cooldown_until": self.until,
            "cooldown_days": self.days,
            "history_run_id": self.record.run_id if self.record else "",
            "history": [item.as_dict() for item in self.history],
        }


# report.created_at 用的是本地可读格式（例如“2026-09-23 14:07:58 中国标准时间”）；
# 认得出才解析，认不出就按“时间字段缺失”处理，不猜测。
_LOCAL_SUFFIXES = (("中国标准时间", 8), ("CST", 8), ("UTC", 0), ("GMT", 0))


def parse_time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    offset_hours: int | None = None
    for suffix, hours in _LOCAL_SUFFIXES:
        if text.endswith(suffix):
            text = text[: -len(suffix)].strip()
            offset_hours = hours
            break
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone(timedelta(hours=offset_hours or 0)))
    return parsed


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def classify(run_status: str, report_status: str) -> tuple[str, str]:
    """只按运行记录里已有的终态判断；看不懂的状态记为 unknown。"""
    report_status = str(report_status or "")
    run_status = str(run_status or "")
    if report_status in SUCCESS_REPORT_STATUSES:
        return OUTCOME_SUCCESS, REASON_SUCCESS
    if report_status == "rejected":
        return OUTCOME_FAILURE, REASON_REJECTED
    if report_status == "render-failed":
        return OUTCOME_FAILURE, REASON_RENDER_FAILED
    if run_status in FAILURE_RUN_STATUSES:
        return OUTCOME_FAILURE, REASON_MODEL_FAILED
    return OUTCOME_UNKNOWN, REASON_UNKNOWN


def cooldown_config(selection: dict[str, Any] | None) -> CooldownConfig:
    raw = selection or {}

    def _int(key: str, default: int) -> int:
        try:
            return int(raw.get(key, default))
        except (TypeError, ValueError):
            return default

    return CooldownConfig(
        success_days=_int("cooldown_success_days", 30),
        failure_days=_int("cooldown_failure_days", 7),
    )


def _repo_from_run_context(
    runs_dir: Path, run_id: str, warnings: list[str] | None = None
) -> str:
    path = runs_dir / run_id / "run-context.json"
    try:
        if not path.exists():
            return ""
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        # 目录权限、文件损坏等：不猜这个运行的目标，按“没有目标记录”处理，但要说出来。
        # 注意：这不能替代修权限——迁移后仍应让运行账号能遍历 runs/ 下的全部目录。
        if warnings is not None:
            warnings.append(
                f"{run_id} 的 run-context.json 读不到（{type(exc).__name__}）："
                "按“没有目标记录”处理，本次不影响其它项目"
            )
        return ""
    except (TypeError, json.JSONDecodeError):
        return ""
    if not isinstance(payload, dict):
        return ""
    return normalize_repo_name(payload.get("target"))


def target_history(
    store: Store, *, runs_dir: Path, warnings: list[str] | None = None
) -> dict[str, list[HistoryRecord]]:
    """按项目汇总“作为目标”的历史（同一项目跨主题、跨大小写都算同一个）。"""
    runs = {
        str(row["run_id"]): dict(row)
        for row in store.conn.execute(
            "SELECT run_id, status, started_at, finished_at FROM runs"
        )
    }
    reports = [
        dict(row)
        for row in store.conn.execute(
            "SELECT run_id, status, created_at, payload FROM reports"
        )
    ]
    # 用并集：有的运行只有 runs 行（如 model-failed），有的只有 reports 行（成功收尾的重放不写 runs）。
    pairs: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    seen: set[str] = set()
    for report in reports:
        run_id = str(report["run_id"])
        seen.add(run_id)
        pairs.append((run_id, runs.get(run_id, {}), report))
    for run_id, run in runs.items():
        if run_id not in seen:
            pairs.append((run_id, run, {}))
    pairs.sort(key=lambda item: item[0])

    history: dict[str, list[HistoryRecord]] = {}
    for run_id, run, report in pairs:
        payload: dict[str, Any] = {}
        if report.get("payload"):
            try:
                payload = json.loads(report["payload"])
            except (TypeError, json.JSONDecodeError):
                payload = {}
        target = payload.get("target") if isinstance(payload, dict) else None
        repo = normalize_repo_name(target.get("repo")) if isinstance(target, dict) else ""
        if not repo:
            repo = _repo_from_run_context(runs_dir, run_id, warnings)
        if not repo:
            continue  # 没有目标记录（搜索失败 / 只初筛 / 没选点）：不参与冷却
        finished = str(
            run.get("finished_at") or run.get("started_at") or report.get("created_at") or ""
        )
        run_status = str(run.get("status") or "")
        report_status = str(report.get("status") or "")
        outcome, reason = classify(run_status, report_status)
        history.setdefault(repo, []).append(
            HistoryRecord(
                repo=repo,
                run_id=run_id,
                run_status=run_status,
                report_status=report_status,
                finished_at=finished,
                outcome=outcome,
                reason=reason,
            )
        )
    return history


def decide(
    repo: str,
    records: Iterable[HistoryRecord],
    *,
    config: CooldownConfig,
    now: str,
    full_name: str = "",
) -> CooldownDecision:
    items = tuple(records)
    moment_now = parse_time(now) or datetime.now(timezone.utc)
    best_until: datetime | None = None
    best_days = 0
    best_record: HistoryRecord | None = None
    for record in items:
        moment = parse_time(record.finished_at)
        if moment is None:
            continue  # 时间字段缺失：不猜测，按可参与处理
        days = config.window_days(record.outcome)
        if days <= 0:
            continue
        until = moment + timedelta(days=days)
        if until > moment_now and (best_until is None or until > best_until):
            best_until, best_days, best_record = until, days, record
    if best_until is not None:
        return CooldownDecision(
            repo=repo,
            full_name=full_name or repo,
            state=STATE_SKIPPED,
            reason=str(best_record.reason) if best_record else REASON_SUCCESS,
            until=_iso(best_until),
            days=best_days,
            record=best_record,
            history=items,
        )
    if any(record.outcome == OUTCOME_UNKNOWN for record in items):
        return CooldownDecision(
            repo=repo,
            full_name=full_name or repo,
            state=STATE_UNKNOWN,
            reason=REASON_UNKNOWN + "（按规则允许参与）",
            history=items,
        )
    return CooldownDecision(
        repo=repo,
        full_name=full_name or repo,
        state=STATE_ALLOWED,
        reason=REASON_EXPIRED if items else REASON_NO_HISTORY,
        history=items,
    )


def filter_items(
    items: list[dict[str, Any]],
    history: dict[str, list[HistoryRecord]],
    *,
    config: CooldownConfig,
    now: str,
) -> list[CooldownDecision]:
    """按候选顺序逐个判定（返回顺序与 items 一致，便于和候选一一对应）。"""
    decisions: list[CooldownDecision] = []
    for item in items:
        full_name = str(item.get("full_name") or "")
        repo = normalize_repo_name(full_name)
        decisions.append(
            decide(
                repo,
                history.get(repo, []),
                config=config,
                now=now,
                full_name=full_name,
            )
        )
    return decisions


def split_items(
    items: list[dict[str, Any]], decisions: list[CooldownDecision]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    eligible = [item for item, decision in zip(items, decisions) if not decision.blocked]
    cooling = [item for item, decision in zip(items, decisions) if decision.blocked]
    return eligible, cooling


def summary_line(decisions: list[CooldownDecision]) -> str:
    skipped = [item for item in decisions if item.blocked]
    unknown = [item for item in decisions if item.state == STATE_UNKNOWN]
    text = (
        f"本次取得 {len(decisions)} 个候选：跳过 {len(skipped)} 个（冷却期内），"
        f"剩余 {len(decisions) - len(skipped)} 个进入初筛。"
    )
    if unknown:
        text += f" 另有 {len(unknown)} 个历史记录无法判定，按可参与处理。"
    return text


def cooldown_line(decision: CooldownDecision) -> str:
    run_ids = "、".join(item.run_id for item in decision.history if item.run_id)
    text = f"{decision.repo}：{decision.reason}"
    if decision.record and decision.record.finished_at:
        text += f"；上次运行 {decision.record.run_id}（{decision.record.finished_at}）"
    elif run_ids:
        text += f"；关联历史运行 {run_ids}"
    if decision.until:
        text += f"；自动再评估最早日期 {decision.until}（冷却 {decision.days} 天）"
    text += "；可通过手动指定项目提前重新分析"
    return text


def manual_notice(
    repo: str,
    records: Iterable[HistoryRecord],
    *,
    config: CooldownConfig,
    now: str,
) -> str:
    """手动指定目标或重放时的提示：只说明历史，不阻止。"""
    items = tuple(records)
    if not items:
        return ""
    decision = decide(repo, items, config=config, now=now, full_name=repo)
    last = max(items, key=lambda item: item.finished_at or "")
    text = f"提示：{repo} 有历史记录（最近一次：{last.reason}，运行 {last.run_id}，{last.finished_at}）"
    if decision.blocked:
        text += f"；按冷却规则会跳过到 {decision.until}，但手动指定/重放不受限制。"
    else:
        text += "；不在冷却期内。"
    return text


def to_context(decisions: list[CooldownDecision], *, config: CooldownConfig, now: str) -> dict[str, Any]:
    """写进运行记录的简短说明（复用 run-context.json，不新增表）。"""
    return {
        "checked_at": now,
        "config": config.as_dict(),
        "summary": summary_line(decisions),
        "skipped": [item.as_dict() for item in decisions if item.blocked],
        "unknown": [item.as_dict() for item in decisions if item.state == STATE_UNKNOWN],
    }
