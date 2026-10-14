"""命令行入口。

命令：
  verify-models  核验配置里的模型候选是否真的可用（如实记录成功/失败）
  discover       用配置里的主题查询真实 GitHub 搜索，列出候选
  run            证据收集 -> 模型分析 -> 质量门槛 -> 渲染报告（增量 A 主流程）
  list / show    查看已生成报告
  serve          启动本机报告页面（127.0.0.1）
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import INCREMENT, __version__
from .analysis import PROMPT_VERSION, AnalysisError, run_analysis
from .cloud import (
    cmd_backup,
    cmd_cycle_recover,
    cmd_cycles,
    cmd_email_preview,
    cmd_email_retry,
    cmd_email_send,
    cmd_email_test,
    cmd_weekly,
)
from .collector import collect_repository
from .config import ConfigError, CandidateModel, Settings, load_profile, load_settings
from .evidence import Evidence, normalize_repo_name
from .feedback import prompt_text_for_repos, report_rows
from .history import (
    cooldown_config,
    cooldown_line,
    filter_items,
    manual_notice,
    split_items,
    summary_line,
    target_history,
    to_context,
)
from .models import ModelError, build_adapter, verify_candidate
from .net import FetchError, audit_summary, now_iso
from .report import meta_block, render_markdown, render_rejection
from .screening import ScreeningOutcome, run_screening
from .sources_github import GitHubClient
from .store import Store
from .validate import RULES_VERSION, ValidationResult, validate_report
from .web import serve

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_GATE_FAILED = 2
EXIT_MODEL_UNAVAILABLE = 3


def _print(message: str = "") -> None:
    print(message, flush=True)


def _local_time() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


def _run_id(topic: str) -> str:
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in (topic or "run"))
    return f"{stamp}-{safe}"


def _find_topic(settings: Settings, topic_id: str | None) -> dict[str, Any]:
    if not settings.topics:
        raise ConfigError("config/topics.toml 里没有主题")
    if topic_id is None:
        return settings.topics[0]
    for topic in settings.topics:
        if topic.get("id") == topic_id:
            return topic
    raise ConfigError(f"未知主题 {topic_id!r}；可选：{', '.join(str(t.get('id')) for t in settings.topics)}")


def _client(settings: Settings, audit: list[dict[str, Any]]) -> GitHubClient:
    client = GitHubClient(
        token=os.environ.get("GITHUB_TOKEN", "").strip(),
        timeout=float(settings.http.get("timeout_seconds", 30)),
        user_agent=settings.user_agent(),
        audit=audit,
        max_bytes=int(settings.http.get("max_response_bytes", 2_000_000)),
        retries=int(settings.http.get("max_retries", 2)),
    )
    # 先看限额，决定用完整 REST 还是 public 兜底入口（两者都是 GitHub 官方地址）
    preflight = client.preflight()
    client.notes.insert(
        0,
        "GitHub 证据模式：{}（core 限额剩余 {}/{}）".format(
            preflight.get("mode"),
            preflight.get("core_remaining", "未知"),
            preflight.get("core_limit", "未知"),
        ),
    )
    return client


def _parse_repo(value: str) -> str:
    text = value.strip()
    if text.startswith("repo:"):
        text = text[len("repo:") :]
    text = text.replace("https://github.com/", "").strip("/")
    if text.count("/") != 1 or not all(text.split("/")):
        raise ConfigError(f"仓库标识必须形如 owner/name：{value!r}")
    return text


def cmd_verify_models(settings: Settings, args: argparse.Namespace) -> int:
    names = [args.candidate] if args.candidate else list(settings.models)
    results: list[dict[str, Any]] = []
    _print(f"# 模型接入核验（{_local_time()}）")
    failed: list[str] = []
    for name in names:
        candidate = settings.models.get(name)
        if candidate is None:
            _print(f"- 未知候选 {name}")
            continue
        probe = None if args.probe is None else args.probe
        result = verify_candidate(candidate, settings, probe=probe)
        result["is_selected_analysis_model"] = name == settings.roles.get("analysis")
        results.append(result)
        mark = "OK  " if result["ok"] else "FAIL"
        _print(f"[{mark}] {candidate.describe()}")
        _print(f"        密钥：{'已配置' if result['api_key_present'] else '缺失'} | base_url：{candidate.base_url or '(无)'}")
        if result.get("model_ids_seen"):
            shown = ", ".join(result["model_ids_seen"][:8])
            _print(f"        /models 可见：{shown}{' …' if len(result['model_ids_seen']) > 8 else ''}")
        if result.get("model_id_in_list") is not None:
            _print(f"        model_id 在列表中：{'是' if result['model_id_in_list'] else '否'}")
        if result.get("probe_ok") is not None:
            _print(f"        最小生成调用：{'成功' if result['probe_ok'] else '失败'} {result.get('probe_detail', '')}")
        if not result["ok"]:
            failed.append(f"{candidate.name}: {result['detail']}")
            _print(f"        原因：{result['detail']}")

    out_dir = settings.data_dir / "runs"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"verify-models-{datetime.now().strftime('%Y%m%dT%H%M%S')}.json"
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    _print("")
    _print(f"核验记录：{out_path}")
    if failed:
        _print("未能核验通过的候选：")
        for item in failed:
            _print(f"- {item}")
    return EXIT_OK if any(item["ok"] for item in results) else EXIT_MODEL_UNAVAILABLE


def cmd_discover(settings: Settings, args: argparse.Namespace) -> int:
    audit: list[dict[str, Any]] = []
    client = _client(settings, audit)
    topic = _find_topic(settings, args.topic)
    query = str(topic.get("github_query", ""))
    _print(f"# 真实 GitHub 搜索（{_local_time()}）")
    _print(f"主题：{topic.get('label')} ({topic.get('id')})")
    _print(f"查询：{query}")
    try:
        items = client.search_repositories(query, per_page=args.per_page)
    except FetchError as exc:
        _print(f"搜索失败：{exc}")
        return EXIT_USAGE
    for item in items[: args.limit]:
        _print(
            f"- {item.get('full_name')} ★{item.get('stargazers_count')} "
            f"push={item.get('pushed_at')} fork={item.get('fork')} archived={item.get('archived')}"
        )
    _print("")
    _print(f"共取回 {len(items)} 条；HTTP 请求 {audit_summary(audit)}")
    return EXIT_OK


def _decision_map(screening: Any) -> dict[str, str]:
    """初筛结论：仓库名（小写）-> keep / uncertain / exclude。"""
    mapping: dict[str, str] = {}
    for item in getattr(screening, "entries", []) or []:
        mapping[str(item.get("repo", "")).lower()] = str(item.get("decision", "uncertain"))
    return mapping


def _fit_map(screening: Any) -> dict[str, str]:
    """初筛给出的适配等级：仓库名（小写）-> high / medium / low / unknown。"""
    mapping: dict[str, str] = {}
    for item in getattr(screening, "entries", []) or []:
        mapping[str(item.get("repo", "")).lower()] = str(item.get("fit", "unknown"))
    return mapping


def _reason_map(screening: Any) -> dict[str, str]:
    """初筛理由：仓库名（小写）-> 理由原文（写进选点说明，便于复核）。"""
    mapping: dict[str, str] = {}
    for item in getattr(screening, "entries", []) or []:
        mapping[str(item.get("repo", "")).lower()] = str(item.get("reason", ""))
    return mapping


def _select_auto_target(
    items: list[dict[str, Any]],
    *,
    decisions: dict[str, str],
    fits: dict[str, str],
    reasons: dict[str, str],
    selection: dict[str, Any],
) -> tuple[dict[str, Any] | None, str]:
    """按初筛结论与理由选点（语言只作环境信息，不参与排序）。

    固定顺序：
    1) 初筛 keep 优先，uncertain 作后备（exclude 不参与）；
    2) 同档内按初筛给出的 fit 等级 high > medium > low > unknown（由初筛理由得出）；
    3) 只有前两项都相同时，才比较最近推送时间（可用配置关掉）。
    没有任何可用候选时返回 None —— 本轮不推荐，而不是硬凑一个。
    """
    fit_order = {"high": 0, "medium": 1, "low": 2, "unknown": 3}
    tiebreak_recent = bool(selection.get("tiebreak_recent", True))
    eligible: list[tuple[int, int, dict[str, Any], str, str]] = []
    for item in items:
        name = str(item.get("full_name") or "")
        if item.get("fork") or item.get("archived") or not item.get("description"):
            continue
        key = name.lower()
        decision = decisions.get(key, "uncertain")
        if decision == "exclude":
            continue
        fit = fits.get(key, "unknown")
        decision_rank = 0 if decision == "keep" else 1
        eligible.append((decision_rank, fit_order.get(fit, 3), item, decision, fit))

    if not eligible:
        return None, "初筛保留集合里没有可用候选（被排除、fork、已归档或缺描述）"

    best_rank = min((row[0], row[1]) for row in eligible)
    best_group = [row for row in eligible if (row[0], row[1]) == best_rank]
    if tiebreak_recent and len(best_group) > 1:
        chosen = max(best_group, key=lambda row: str(row[2].get("pushed_at") or ""))
    else:
        chosen = best_group[0]
    _, _, item, decision, fit = chosen
    name = str(item.get("full_name"))
    reason = reasons.get(name.lower(), "")
    language = str(item.get("language") or "未知")
    note = (
        f"按初筛结论与理由选点：decision={decision}，fit={fit}；"
        f"初筛理由：{reason[:80] or '（未给理由）'}；"
        f"同档候选 {len(best_group)} 个；语言（{language}）仅作环境信息，不参与排序"
    )
    return item, note

def _auto_comparators(items: list[dict[str, Any]], target: str, count: int) -> list[dict[str, Any]]:
    usable = [
        item
        for item in items
        if str(item.get("full_name", "")).lower() != target.lower() and item.get("description")
    ]
    ranked = sorted(usable, key=lambda item: int(item.get("stargazers_count") or 0), reverse=True)
    return ranked[:count]


class ModelBudgetExceeded(RuntimeError):
    """模型请求预算用尽：停止调用，不换模型、不静默继续。"""


class ModelBudget:
    """按"真实发出的模型请求次数"计数，失败与底层重试都算一次。

    底层 post_json 对模型请求固定 retries=0，因此一次 complete() 恰好对应一次 HTTP 请求；
    失败（网络错误、HTTP 4xx/5xx、响应不可用）同样消耗一次预算。
    """

    def __init__(self, limit: int) -> None:
        self.limit = max(1, int(limit))
        self.used = 0

    def consume(self) -> None:
        if self.used >= self.limit:
            raise ModelBudgetExceeded(f"模型请求预算已用尽（上限 {self.limit} 次）")
        self.used += 1

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used)

    def describe(self) -> str:
        return f"{self.used}/{self.limit}"


class BudgetedAdapter:
    """只加一层请求计数，不改模型、不做任何切换。"""

    def __init__(self, inner: Any, budget: ModelBudget) -> None:
        self.inner = inner
        self.budget = budget
        self.candidate = getattr(inner, "candidate", None)
        self.at_request_boundary = hasattr(inner, "bind_budget")
        if self.at_request_boundary:
            inner.bind_budget(budget)

    def complete(self, *, system: str, user: str, max_output_tokens: int | None = None) -> Any:
        if self.at_request_boundary:
            return self.inner.complete(system=system, user=user, max_output_tokens=max_output_tokens)
        attempt = self.budget.consume()
        try:
            response = self.inner.complete(system=system, user=user, max_output_tokens=max_output_tokens)
        except Exception:
            if hasattr(self.budget, "finish_request"):
                self.budget.finish_request(attempt, "failed")
            raise
        if hasattr(self.budget, "finish_request"):
            self.budget.finish_request(attempt, "response_received")
        return response

def _failure_usage_rows(
    prior: list[dict[str, Any]],
    responses_seen: list[Any],
    candidate: CandidateModel,
    error: str,
    *,
    unrecorded_calls: int,
    stage: str = "analysis",
) -> list[dict[str, Any]]:
    """失败路径的用量：先保留此前阶段的记录，再补本阶段的调用与失败行。

    不能让后面阶段的失败把前面阶段（例如初筛）的用量抹掉。
    """
    rows = list(prior)
    rows.extend(
        _usage_entry(stage, item, candidate, role=stage, ok=False) for item in responses_seen
    )
    rows.append(
        _failure_entry(stage, candidate, error, role=stage, calls=max(0, unrecorded_calls))
    )
    return rows

def _load_evidence_for_run(
    settings: Settings, run_id: str, run_dir: Path
) -> tuple[list[Evidence], str]:
    """优先读运行目录的 evidence.json；没有就从 SQLite 的 evidence 表回读。"""
    path = run_dir / "evidence.json"
    if path.exists():
        payload = json.loads(path.read_text(encoding="utf-8"))
        return [Evidence(**item) for item in payload], "evidence.json"
    store_path = settings.data_dir / "learning-radar.sqlite"
    if not store_path.exists():
        return [], ""
    with Store(store_path) as store:
        rows = store.get_evidence(run_id)
    items: list[Evidence] = []
    for row in rows:
        try:
            items.append(Evidence(**json.loads(row.get("payload") or "{}")))
        except (TypeError, json.JSONDecodeError, ValueError):
            continue
    return items, ("sqlite:evidence" if items else "")

def _repo_facts_from_evidence(evidence: list[Evidence], repo: str) -> dict[str, Any]:
    """从已保存的证据里取回该仓库的元数据（重放时不再联网）。"""
    wanted = normalize_repo_name(repo)
    for item in evidence:
        if item.kind == "github_repo" and normalize_repo_name(item.owner) == wanted:
            return dict(item.extra or {})
    return {}


def _has_readme(evidence: list[Evidence], repo: str) -> bool:
    wanted = normalize_repo_name(repo)
    return any(
        item.kind == "github_readme" and normalize_repo_name(item.owner) == wanted for item in evidence
    )


def _learning_entry_from_evidence(evidence: list[Evidence], repo: str) -> dict[str, Any]:
    """重放时从保存的证据还原学习入口状态（含示例信号）。"""
    wanted = normalize_repo_name(repo)
    from .readme import analyze_readme_entry
    for item in evidence:
        if item.kind == "github_readme" and normalize_repo_name(item.owner) == wanted:
            analysis = analyze_readme_entry(item.excerpt, wanted)
            if item.url and analysis.get("example_signal"):
                return {"url": item.url, "verified": True, "example_signal": True,
                        "signals": analysis.get("signals") or [], "evidence_id": item.evidence_id,
                        "source": "retained_readme", "error": "", "example_files": []}
    for item in evidence:
        if item.kind == "web_page" and normalize_repo_name(item.owner) == wanted:
            signal = "检测到示例信号" in (item.notes or "")
            return {
                "url": item.url,
                "verified": True,
                "example_signal": signal,
                "signals": [],
                "evidence_id": item.evidence_id,
                "error": "" if signal else "重放：保存的证据显示该入口没有检测到示例信号",
            }
    return {"url": "", "verified": False, "example_signal": False, "signals": [], "error": "重放：没有保存的学习入口证据"}


def _repos_in_evidence(evidence: list[Evidence]) -> list[str]:
    seen: list[str] = []
    for item in evidence:
        owner = normalize_repo_name(item.owner)
        if owner and owner not in seen:
            seen.append(owner)
    return seen


def cmd_replay_analysis(settings: Settings, args: argparse.Namespace) -> int:
    from .submission import submit_cli
    return submit_cli(settings,args,replay=True)


def run_replay_analysis(settings: Settings, args: argparse.Namespace, *, hooks=None) -> int:
    """用已保存的证据单独重跑分析阶段：不重新搜索、不重新初筛。"""
    from .pipeline import RunHooks
    hooks=hooks or RunHooks()
    run_dir = settings.data_dir / "runs" / args.run
    evidence, evidence_source = _load_evidence_for_run(settings, args.run, run_dir)
    if not evidence:
        _print(f"找不到该运行的证据：{run_dir / 'evidence.json'} 与 SQLite 里都没有 {args.run} 的证据")
        return EXIT_USAGE
    _print(f"证据来源：{evidence_source}（{len(evidence)} 条）")
    context_path = run_dir / "run-context.json"
    context: dict[str, Any] = {}
    if context_path.exists():
        context = json.loads(context_path.read_text(encoding="utf-8"))

    repos = _repos_in_evidence(evidence)
    target_name = args.target or context.get("target") or ""
    if not target_name:
        entry_owner = next((item.owner for item in evidence if item.kind == "web_page" and item.owner), "")
        target_name = normalize_repo_name(entry_owner) or (repos[0] if repos else "")
    if not target_name:
        _print("无法确定目标项目，请用 --target 指定")
        return EXIT_USAGE
    comparator_names = [_parse_repo(item) for item in (args.compare or [])]
    if not comparator_names:
        comparator_names = [str(item) for item in (context.get("comparators") or [])]
    if not comparator_names:
        comparator_names = [repo for repo in repos if repo != normalize_repo_name(target_name)]

    profile, profile_placeholder, profile_path = (hooks.profile_loader or load_profile)(settings)
    profile_confirmed = bool(profile.get("confirmed_by_user")) and not profile_placeholder
    try:
        analysis_model = (
            settings.models[args.model] if args.model else settings.model_for("analysis")
        )
    except (ConfigError, KeyError):
        _print(f"未知或未配置的分析模型：{args.model or 'analysis 角色'}")
        return EXIT_USAGE

    new_run_id = args.run_id or f"{args.run}-replay-{datetime.now().strftime('%Y%m%dT%H%M%S')}"
    runs_dir = settings.data_dir / "runs" / new_run_id
    audit: list[dict[str, Any]] = []
    _print(f"# 重放分析（仅分析阶段）{new_run_id}（{_local_time()}）")
    _print(f"沿用运行 {args.run} 的 {len(evidence)} 条证据；不重新搜索、不重新初筛")
    _print(f"分析模型：{analysis_model.describe()}（reasoning_effort={analysis_model.reasoning_effort or '未设置'}，max_output_tokens={analysis_model.max_output_tokens}）")

    _print_history_notice(settings, target_name)
    learning_entry = _learning_entry_from_evidence(evidence, target_name)
    target_payload = {
        "repo": target_name,
        "role": "target",
        "facts": _repo_facts_from_evidence(evidence, target_name),
        "readme_available": _has_readme(evidence, target_name),
        "learning_entry": learning_entry,
        "screening_note": (context.get("screening") or {}).get("note")
        or "本次重放没有保存的初筛结论（原运行未留存运行上下文）。",
        "collection_errors": [],
    }
    comparator_payload = [
        {
            "repo": name,
            "role": "comparator",
            "facts": _repo_facts_from_evidence(evidence, name),
            "readme_available": _has_readme(evidence, name),
            "collection_errors": [],
        }
        for name in comparator_names
    ]

    # 保存本次实际分析输入，而非原运行上下文的副本（命令行可能覆盖目标/对照）。
    # 在模型请求之前落盘，使失败与成功路径都能追溯；不修改原运行目录。
    runs_dir.mkdir(parents=True, exist_ok=True)
    (runs_dir / "run-context.json").write_text(
        json.dumps(
            {
                "run_id": new_run_id,
                "replay_of": args.run,
                "evidence_source": evidence_source,
                "target": target_name,
                "comparators": comparator_names,
                "learning_entry": learning_entry,
                "topic": context.get("topic") or "replay",
                "query": str(context.get("query") or ""),
                "evidence_ids": [item.evidence_id for item in evidence],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    with Store(settings.data_dir/"learning-radar.sqlite") as store:
        store.start_run(new_run_id,kind="replay-analysis",started_at=now_iso(),snapshot={"replay_of":args.run})
    budget = hooks.budget or ModelBudget(settings.limit("max_model_calls_per_run", 4))
    _print(f"模型请求预算：最多 {budget.limit} 次（本次只跑分析阶段）")
    usage_entries: list[dict[str, Any]] = []
    responses_seen: list[Any] = []
    try:
        hooks.stage("analysis")
        adapter = BudgetedAdapter((hooks.adapter_factory or build_adapter)(analysis_model, audit=audit), budget)
        outcome = run_analysis(
            adapter,
            settings,
            profile=profile,
            profile_confirmed=profile_confirmed,
            target=target_payload,
            comparators=comparator_payload,
            evidence=evidence,
            feedback_text=_feedback_prompt_text(settings, [target_name, *comparator_names]),
            max_calls=max(1, min(settings.limit("analysis_max_calls", 2), budget.remaining)),
            sink=responses_seen,
            report_validator=lambda draft: _validate_analysis_candidate(
                settings, draft, evidence=evidence, target_name=target_name,
                comparator_names=comparator_names, require_quotes=True,
                require_verified_entry=bool(learning_entry.get("verified")),
                verified_entry=learning_entry or None,
            ).errors,
        )
    except (ModelError, ModelBudgetExceeded, AnalysisError, FetchError) as exc:
        failed_usage = _failure_usage_rows(
            usage_entries,
            responses_seen,
            analysis_model,
            str(exc),
            unrecorded_calls=max(0, budget.used - len(responses_seen)),
        )
        (settings.data_dir / "runs" / new_run_id).mkdir(parents=True, exist_ok=True)
        (settings.data_dir / "runs" / new_run_id / "usage.json").write_text(
            json.dumps(
                {
                    "status": "model-failed",
                    "kind": getattr(exc, "kind", ""),
                    "error": str(exc)[:800],
                    "usage": failed_usage,
                    "totals": _aggregate_usage(failed_usage),
                    "budget": {"limit": budget.limit, "used": budget.used},
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        if responses_seen:
            (settings.data_dir / "runs" / new_run_id / "raw_model_output.txt").write_text(
                "\n\n=====\n\n".join(item.text for item in responses_seen), encoding="utf-8"
            )
        store = Store(settings.data_dir / "learning-radar.sqlite")
        store.save_usage(new_run_id, failed_usage)
        store.start_run(new_run_id, kind="replay-analysis", started_at=now_iso(), snapshot={"replay_of": args.run})
        store.finish_run(new_run_id, status="model-failed", finished_at=now_iso(), notes=str(exc)[:500])
        store.close()
        _print(f"分析失败（kind={getattr(exc, 'kind', '-')}）：{exc}")
        _print(f"  已记录 {len(responses_seen)} 次真实调用与原始输出：{settings.data_dir / 'runs' / new_run_id}")
        _print("按规则停止，不再消耗预算。")
        return EXIT_MODEL_UNAVAILABLE

    for response in outcome.responses:
        usage_entries.append(_usage_entry("analysis", response, analysis_model, role="analysis"))
    for error in outcome.failed_requests:
        usage_entries.append(_failure_entry("analysis", analysis_model, error, role="analysis", calls=1))
    _save_analysis_checks(runs_dir, outcome)
    (runs_dir / "raw_model_output.txt").write_text(
        "\n\n=====\n\n".join(response.text for response in outcome.responses), encoding="utf-8"
    )
    _snapshot_usage(
        runs_dir, usage_entries, status="in-progress-analysis", budget=budget, audit=audit
    )
    for entry in usage_entries:
        _print(f"  [{entry['stage']}] {entry['model_id']}：{entry['calls']} 次，{entry['latency_ms']} ms，输入 {entry['input_tokens']} / 输出 {entry['output_tokens']} token，finish_reason={entry.get('finish_reason')}，请求上限 {entry.get('requested_max_tokens')}")

    status, validation, md_path, meta = finalize_run(
        settings,
        run_id=new_run_id,
        report=outcome.report,
        evidence=evidence,
        target_name=target_name,
        comparator_names=comparator_names,
        candidate=analysis_model,
        profile=profile,
        profile_confirmed=profile_confirmed,
        topic={"id": context.get("topic") or "replay", "label": "重放（同一份证据）"},
        query=str(context.get("query") or ""),
        selection_note=f"重放：沿用运行 {args.run} 的候选与证据（不重新搜索、不重新初筛）",
        discovered_count=len(evidence),
        client_mode="replay",
        routes_text="replay:evidence.json",
        usage_entries=usage_entries,
        audit=audit,
        fixture=outcome.fixture,
        require_quotes=True,
        require_verified_entry=bool(learning_entry.get("verified")),
        verified_entry=learning_entry or None,
        roles_text=_roles_text(settings),
        screening_text="重放：未重新初筛（沿用原运行候选）",
        budget_info={"limit": budget.limit, "used": budget.used, "screening_requests": 0, "analysis_requests": budget.used},
        on_stage=hooks.stage,
    )
    _print(f"\n结构检查：{validation.structure_label}；内容复核：未完成（需要人工）")
    _print(f"  Markdown：{md_path or '（渲染失败）'}")
    _print(f"  运行数据：{settings.data_dir / 'runs' / new_run_id}")
    if status != "published":
        _print(f"运行终态：{status}（未发布；用量与诊断已落盘）")
        return EXIT_GATE_FAILED
    for key, value in meta.items():
        _print(f"  {key}: {value}")
    return EXIT_OK

def _load_candidates_from_run(settings: Settings, run_id: str) -> list[dict[str, Any]]:
    """复用某次运行保存的候选；不重新搜索、不改动原运行。"""
    store_path = settings.data_dir / "learning-radar.sqlite"
    if not store_path.exists():
        return []
    with Store(store_path) as store:
        rows = store.list_candidates(run_id)
    items: list[dict[str, Any]] = []
    for row in rows:
        try:
            item = json.loads(row.get("raw") or "{}")
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(item, dict) and item.get("full_name"):
            items.append(item)
    return items


def _load_screening_from_run(settings: Settings, run_id: str) -> ScreeningOutcome:
    """复用某次运行保存的初筛结论；没有就明确报错，不退化成按新鲜度排序。"""
    path = settings.data_dir / "runs" / run_id / "run-context.json"
    if not path.exists():
        raise ConfigError(f"运行 {run_id} 没有保存 run-context.json，无法复用初筛结论")
    context = json.loads(path.read_text(encoding="utf-8"))
    block = context.get("screening") or {}
    entries = block.get("entries") or []
    if not entries:
        raise ConfigError(f"运行 {run_id} 没有保存可复用的初筛条目")
    return ScreeningOutcome(
        entries=list(entries),
        failed=bool(block.get("failed")),
        error=str(block.get("error") or ""),
    )

def _persist_pages(runs_dir: Path, pages: list[dict[str, Any]]) -> None:
    # 把读过的候选页面与获取记录落盘：正文写文件，清单写 pages.json（失败也留记录）
    if not pages:
        return
    pages_dir = runs_dir / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)
    manifest: list[dict[str, Any]] = []
    for index, page in enumerate(pages, start=1):
        record = {key: value for key, value in page.items() if key != "text"}
        text = str(page.get("text") or "")
        if text:
            digest = str(page.get("sha256") or f"page-{index}")
            path = pages_dir / f"{index:02d}-{digest[:12]}.txt"
            path.write_text(text, encoding="utf-8")
            record["saved_text"] = str(path)
        manifest.append(record)
    (runs_dir / "pages.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _model_snapshot(candidate: CandidateModel) -> dict[str, Any]:
    return {
        "name": candidate.name,
        "label": candidate.label,
        "provider": candidate.provider,
        "model_id": candidate.model_id,
        "base_url": candidate.base_url,
        "reasoning_effort": candidate.reasoning_effort,
        "max_output_tokens": candidate.max_output_tokens,
        "temperature": candidate.temperature,
        "api_key_env": candidate.api_key_env,
        "api_key_present": bool(candidate.api_key()),
    }


def _roles_text(settings: Settings) -> str:
    """把角色 -> 候选 -> 实际 model_id 写清楚，便于事后核对用了哪个模型。"""
    parts: list[str] = []
    for role in sorted(settings.roles):
        try:
            candidate = settings.model_for(role)
        except ConfigError:
            parts.append(f"{role}=?（配置有误）")
            continue
        parts.append(f"{role}={candidate.name}({candidate.model_id})")
    return "；".join(parts)


def _usage_entry(
    stage: str,
    response: Any,
    candidate: CandidateModel,
    *,
    role: str = "",
    ok: bool = True,
) -> dict[str, Any]:
    summary = response.usage_summary(candidate)
    reported = summary.get("model_id")
    summary["reported_model_id"] = reported
    summary["reasoning_effort"] = candidate.reasoning_effort or ""
    output = summary.get("output_tokens")
    reasoning = summary.get("reasoning_tokens")
    summary["body_tokens"] = (
        max(0, int(output) - int(reasoning))
        if isinstance(output, int) and isinstance(reasoning, int)
        else None
    )
    summary["model_id"] = candidate.model_id
    summary["model_mismatch"] = bool(reported and reported != candidate.model_id)
    return {
        "ts": now_iso(),
        "stage": stage,
        "role": role,
        "calls": 1,
        "ok": ok,
        "error": None,
        "latency_ms": summary["latency_ms"],
        **summary,
    }


def _failure_entry(
    stage: str,
    candidate: CandidateModel,
    error: str,
    *,
    role: str = "",
    calls: int = 0,
) -> dict[str, Any]:
    """记录一次"没有产生结果"的失败；calls 表示这次失败实际消耗的请求数。"""
    return {
        "ts": now_iso(),
        "stage": stage,
        "role": role,
        "calls": int(calls),
        "ok": False,
        "error": str(error)[:300],
        "provider": candidate.provider,
        "model_id": candidate.model_id,
        "fixture": candidate.is_fixture,
        "input_tokens": None,
        "output_tokens": None,
        "reasoning_tokens": None,
        "latency_ms": None,
        "cost_value": None,
        "cost_currency": None,
        "pricing_known": candidate.pricing_known(),
    }


def _stage_usage_rows(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按阶段汇总用量与失败原因，写进报告附录。"""
    rows: dict[str, dict[str, Any]] = {}
    for entry in entries:
        stage = str(entry.get("stage") or "unknown")
        row = rows.setdefault(
            stage,
            {
                "stage": stage,
                "model_id": entry.get("model_id") or "",
                "calls": 0,
                "failed": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "reasoning_tokens": 0,
                "latency_ms": 0,
                "error": "",
            },
        )
        row["calls"] += int(entry.get("calls") or 0)
        if entry.get("ok", True) is False:
            row["failed"] += 1
            if entry.get("error"):
                row["error"] = str(entry["error"])[:200]
        for key in ("input_tokens", "output_tokens", "reasoning_tokens", "latency_ms"):
            value = entry.get(key)
            if isinstance(value, int):
                row[key] += value
    return [rows[key] for key in sorted(rows)]


def _run_screening_stage(
    settings: Settings,
    *,
    audit: list[dict[str, Any]],
    profile: dict[str, Any],
    profile_confirmed: bool,
    candidates: list[dict[str, Any]],
    topic: dict[str, Any],
    model: CandidateModel,
    usage_entries: list[dict[str, Any]],
    budget: "ModelBudget | None" = None,
    adapter_factory: Any = None,
):
    """初筛阶段：只调用 screening 角色的模型；失败就如实记录并保留全部候选。"""
    _print(f"\n[1b/5] 候选初筛（角色 screening → {model.describe()}）")
    responses: list[Any] = []
    budget = budget or ModelBudget(1)
    before = budget.used
    try:
        adapter = BudgetedAdapter((adapter_factory or build_adapter)(model, audit=audit), budget)
    except ModelError as exc:
        outcome = ScreeningOutcome(
            entries=[
                {
                    "repo": str(item.get("full_name")),
                    "decision": "uncertain",
                    "reason": f"初筛无法启动（{exc}），按不确定保留",
                }
                for item in candidates
            ],
            failed=True,
            error=str(exc)[:300],
        )
        usage_entries.append(
            _failure_entry("screening", model, str(exc), role="screening", calls=0)
        )
        _print(f"  - 初筛无法启动：{exc}；全部候选按不确定保留（不换模型）")
        return outcome
    outcome = run_screening(
        adapter,
        profile=profile,
        profile_confirmed=profile_confirmed,
        candidates=candidates,
        topic=topic,
        max_keep=settings.limit("screening_max_keep", 8),
        max_calls=max(
            1,
            min(
                settings.limit("screening_max_calls", 2),
                settings.limit("max_model_calls_per_run", 6),
            ),
        ),
        sink=responses,
    )
    screening_requests = budget.used - before
    outcome.requests = screening_requests
    for response in responses:
        entry = _usage_entry("screening", response, model, role="screening")
        usage_entries.append(entry)
        _print(
            f"    调用：输出 {entry.get('output_tokens')} token（推理 {entry.get('reasoning_tokens')}，"
            f"正文 {entry.get('body_tokens')}），请求上限 {entry.get('requested_max_tokens')}，"
            f"finish_reason={entry.get('finish_reason')}，reasoning_effort={entry.get('reasoning_effort') or '未发送'}"
        )
        if entry.get("model_mismatch"):
            _print(
                f"  - 注意：配置的模型是 {model.model_id}，接口回报的是 "
                f"{entry.get('reported_model_id')}；已同时记录，请核对是否被替换"
            )
    if outcome.failed:
        unrecorded = max(0, screening_requests - len(responses))
        usage_entries.append(
            _failure_entry("screening", model, outcome.error, role="screening", calls=unrecorded)
        )
    for warning in outcome.warnings:
        _print(f"  - 初筛警告：{warning}")
    if outcome.failed:
        _print(f"  - 初筛失败（未换模型）：{outcome.error}；全部候选按不确定保留")
    _print(f"  {outcome.summary()}")
    for row in outcome.ranking():
        _print(
            f"    · {row.get('repo')}｜{row.get('decision')}｜fit={row.get('fit')}｜"
            f"{row.get('reason') or '（无理由）'}"
            + (f"｜门槛：{row.get('gate')}" if row.get("gate") else "")
        )
    for item in outcome.excluded:
        _print(f"    · 排除 {item['repo']}：{item.get('reason')}")
    return outcome



def _snapshot_usage(
    runs_dir: Path,
    usage_entries: list[dict[str, Any]],
    *,
    status: str,
    budget: Any,
    audit: list[dict[str, Any]],
) -> None:
    """把“到此刻为止”的模型用量立刻落盘：后面即使异常退出，已发生的调用也不会丢。

    只是尽力而为：失败不影响主流程，终态写入仍是权威记录。
    """
    try:
        screening_calls = sum(
            int(item.get("calls") or 0) for item in usage_entries if item.get("stage") == "screening"
        )
        analysis_calls = sum(
            int(item.get("calls") or 0) for item in usage_entries if item.get("stage") == "analysis"
        )
        runs_dir.mkdir(parents=True, exist_ok=True)
        (runs_dir / "usage.json").write_text(
            json.dumps(
                {
                    "status": status,
                    "usage": usage_entries,
                    "totals": _aggregate_usage(usage_entries),
                    "http": audit_summary(audit),
                    "budget": {
                        "limit": int(getattr(budget, "limit", 0)),
                        "used": int(getattr(budget, "used", 0)),
                        "screening_requests": screening_calls,
                        "analysis_requests": analysis_calls,
                    },
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except OSError:
        pass

def _cooldown_history(
    settings: Settings, warnings: list[str] | None = None
) -> dict[str, list[Any]]:
    """只读已有运行记录，用于冷却期判断（不新建“已分析项目名单”）。"""
    path = settings.data_dir / "learning-radar.sqlite"
    if not path.exists():
        return {}
    with Store(path) as store:
        return target_history(store, runs_dir=settings.data_dir / "runs", warnings=warnings)


def _print_history_notice(settings: Settings, repo: str) -> None:
    """手动指定目标或重放时的提示：把已有历史说清楚，但不阻止运行。"""
    if not repo:
        return
    records = _cooldown_history(settings).get(normalize_repo_name(repo), [])
    notice = manual_notice(
        repo, records, config=cooldown_config(settings.selection), now=now_iso()
    )
    if notice:
        _print(f"  - {notice}")

def _feedback_prompt_text(settings: Settings, repos: list[str]) -> str:
    """只读取反馈用于提示词；用短连接，不与其它 Store 生命周期纠缠。"""
    path = settings.data_dir / "learning-radar.sqlite"
    if not path.exists():
        return ""
    with Store(path) as store:
        return prompt_text_for_repos(store, repos)

def _generated_verification(evidence: list[Evidence]) -> dict[str, Any]:
    """查阅状态由运行记录生成，不由模型自报。"""
    docs = any(item.kind in ("github_readme", "web_page") for item in evidence)
    release = any(item.kind == "github_release" for item in evidence)
    fixture = any("fixture://" in item.locator or "测试夹具" in item.title for item in evidence)
    return {
        "docs_reviewed": bool(docs),
        "release_reviewed": bool(release),
        "source_reviewed": False,
        "actually_ran": False,
        "note": "fixture：查阅状态为离线测试模拟，未真实查阅外部材料，未实测或运行项目代码。" if fixture else (
            "由运行记录生成：本次只读取公开文档、发布记录与元数据；"
            "没有读取项目源码文件，也没有运行任何项目代码。"
        ),
    }


def _safe_title(report: Any, run_id: str) -> str:
    if isinstance(report, dict):
        title = report.get("title")
        if isinstance(title, str) and title.strip():
            return title.strip()
    return f"[结构异常] {run_id}"


def _validate_analysis_candidate(
    settings: Settings, report: Any, *, evidence: list[Evidence], target_name: str,
    comparator_names: list[str], require_quotes: bool = False,
    require_verified_entry: bool = False, verified_entry: dict[str, Any] | None = None,
) -> ValidationResult:
    """纯预检与最终发布共用同一门槛；运行状态由程序生成，草稿不变。"""
    from copy import deepcopy

    draft = deepcopy(report)
    verification = _generated_verification(evidence)
    if isinstance(draft, dict):
        draft["verification"] = verification
    return validate_report(
        draft, evidence=evidence, target_repo=target_name,
        min_comparators=settings.limit("min_comparators", 2),
        collected_repos=[target_name, *comparator_names], expected_verification=verification,
        require_quotes=require_quotes, require_verified_entry=require_verified_entry,
        verified_entry=verified_entry,
    )


def _save_analysis_checks(runs_dir: Path, outcome: Any) -> None:
    """每次候选的检查与最终采用尝试单独落盘；不覆盖原始输出。"""
    if not outcome.validation_checks:
        return
    (runs_dir / "analysis-validation.json").write_text(
        json.dumps({"prompt_version": PROMPT_VERSION, "rules_version": RULES_VERSION,
                    "checks": outcome.validation_checks, "selected_attempt": outcome.selected_attempt,
                    "retry_kind": outcome.retry_kind, "retry_error": outcome.retry_error},
                   ensure_ascii=False, indent=2), encoding="utf-8",
    )


def finalize_run(
    settings: Settings,
    *,
    run_id: str,
    report: Any,
    evidence: list[Evidence],
    target_name: str,
    comparator_names: list[str],
    candidate: CandidateModel,
    profile: dict[str, Any],
    profile_confirmed: bool,
    topic: dict[str, Any],
    query: str,
    selection_note: str,
    discovered_count: int,
    client_mode: str,
    routes_text: str,
    usage_entries: list[dict[str, Any]],
    audit: list[dict[str, Any]],
    fixture: bool,
    require_quotes: bool = False,
    require_verified_entry: bool = False,
    verified_entry: dict[str, Any] | None = None,
    roles_text: str = "",
    screening_text: str = "",
    budget_info: dict[str, Any] | None = None,
    on_stage: Any = None,
) -> tuple[str, ValidationResult, Path, dict[str, Any]]:
    """校验 -> 先落盘用量与审计 -> 再渲染。

    结构失败或渲染异常都不会丢掉用量、证据与运行终态（A.1 评审第 5 条）。
    """
    runs_dir = settings.data_dir / "runs" / run_id
    runs_dir.mkdir(parents=True, exist_ok=True)
    reports_dir = settings.data_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    md_path = reports_dir / f"{run_id}.md"

    if on_stage:
        on_stage("check")
    verification = _generated_verification(evidence)
    if isinstance(report, dict):
        claimed = report.get("verification")
        if isinstance(claimed, dict):
            report["model_claimed_verification"] = claimed
        report["verification"] = verification

    validation = _validate_analysis_candidate(
        settings, report, evidence=evidence, target_name=target_name,
        comparator_names=comparator_names,
        require_quotes=require_quotes,
        require_verified_entry=require_verified_entry,
        verified_entry=verified_entry,
    )
    claimed = report.get("model_claimed_verification") if isinstance(report, dict) else None
    if isinstance(claimed, dict):
        diff = [
            f"{key}={claimed.get(key)!r}"
            for key, value in verification.items()
            if key in claimed and claimed.get(key) != value
        ]
        if diff:
            validation.warnings.append(
                "模型自报的查阅状态与运行记录不一致（已按运行记录覆盖）：" + "、".join(diff)
            )

    created_at = _local_time()
    usage_totals = _aggregate_usage(usage_entries)
    http_summary = audit_summary(audit)
    discovery = {
        "summary": (
            f"主题 {topic.get('id')} 查询 {query!r}；取回 {discovered_count} 条候选；"
            f"{selection_note or '目标由命令行指定'}；证据模式={client_mode}；{routes_text}"
        )
    }
    stage_usage = _stage_usage_rows(usage_entries)
    meta = meta_block(
        validation=validation,
        roles_text=roles_text,
        screening_text=screening_text,
        budget_text=(
            "{} / {} 次（screening {}，analysis {}，含失败）".format(
                budget_info.get("used"),
                budget_info.get("limit"),
                budget_info.get("screening_requests"),
                budget_info.get("analysis_requests"),
            )
            if budget_info
            else ""
        ),
        run_id=run_id,
        created_at=created_at,
        model_label=candidate.label,
        model_id=candidate.model_id,
        prompt_version=PROMPT_VERSION,
        rules_version=RULES_VERSION,
        profile_label=(
            f"{profile.get('profile_id')}（{'已确认' if profile_confirmed else '草稿，未确认'}）"
        ),
        discovery=discovery,
        usage=usage_totals,
        http_summary=http_summary,
        fixture=fixture,
        comparators=comparator_names,
    )

    if on_stage:
        on_stage("archive")
    # 1) 先落盘：即使渲染失败，用量、审计、证据与校验结果也已经保存
    (runs_dir / "usage.json").write_text(
        json.dumps(
            {
                "usage": usage_entries,
                "totals": usage_totals,
                "http": http_summary,
                "budget": budget_info or {},
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (runs_dir / "audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (runs_dir / "evidence.json").write_text(
        json.dumps([item.to_dict() for item in evidence], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (runs_dir / "validation.json").write_text(
        json.dumps(validation.summary(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    try:
        (runs_dir / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )
    except Exception as exc:  # pragma: no cover - 兜底
        (runs_dir / "report.json").write_text(
            json.dumps({"error": f"{type(exc).__name__}: {exc}"}), encoding="utf-8"
        )

    store = Store(settings.data_dir / "learning-radar.sqlite")
    store.save_usage(run_id, usage_entries)
    store.log_http(run_id, audit)
    feedback_rows = report_rows(store, [target_name, *comparator_names])

    # 2) 结构失败不进入正常渲染：走独立的、类型安全的展示
    status = "published"
    try:
        if validation.ok:
            markdown = render_markdown(
                report=report,
                evidence=evidence,
                meta=meta,
                validation=validation,
                stage_usage=stage_usage,
                feedback_rows=feedback_rows,
            )
        else:
            status = "rejected"
            markdown = render_rejection(
                report=report, validation=validation, meta=meta, stage_usage=stage_usage
            )
        md_path.write_text(markdown, encoding="utf-8")
    except Exception as exc:
        status = "render-failed"
        (runs_dir / "render-error.txt").write_text(
            f"{type(exc).__name__}: {exc}\n\n{traceback.format_exc()}", encoding="utf-8"
        )
        md_path = Path("")

    store.save_report(
        run_id,
        report_id=run_id,
        created_at=created_at,
        status=status,
        title=_safe_title(report, run_id),
        markdown_path=str(md_path),
        html_path="",
        payload=report if isinstance(report, dict) else {},
        validation=validation.summary(),
    )
    notes = {
        "published": "结构检查通过；内容复核未完成",
        "rejected": f"结构检查未通过：{len(validation.errors)} 项，未发布",
        "render-failed": "渲染失败，已保留用量与原始输出",
    }.get(status, status)
    store.finish_run(run_id, status=status, finished_at=now_iso(), notes=notes)
    store.close()
    return status, validation, md_path, meta

def cmd_run(settings: Settings, args: argparse.Namespace) -> int:
    from .submission import submit_cli
    return submit_cli(settings,args)


def run_recommendation(settings: Settings, args: argparse.Namespace, *, hooks=None) -> int:
    from .pipeline import RunHooks
    hooks = hooks or RunHooks()
    model_factory = hooks.adapter_factory or build_adapter
    collect = hooks.collector or collect_repository
    broad = getattr(args, 'discovery_mode', None) == 'broad'
    topic = {'id': 'broad', 'label': '广泛发现', 'github_query': ''} if broad else _find_topic(settings, args.topic)
    try:
        screening_model = settings.model_for("screening")
        if args.model:
            if args.model not in settings.models:
                _print(f"未知模型候选：{args.model}")
                return EXIT_USAGE
            candidate = settings.models[args.model]
        else:
            candidate = settings.model_for("analysis")
    except ConfigError as exc:
        _print(f"模型角色配置有问题：{exc}")
        return EXIT_USAGE

    profile, profile_placeholder, profile_path = (hooks.profile_loader or load_profile)(settings)
    profile_confirmed = bool(profile.get("confirmed_by_user")) and not profile_placeholder

    run_id = getattr(args, "run_id", "") or _run_id(str(topic.get("id")))
    runs_dir = settings.data_dir / "runs" / run_id
    runs_dir.mkdir(parents=True, exist_ok=True)
    audit: list[dict[str, Any]] = []
    store_path = settings.data_dir / "learning-radar.sqlite"
    store = Store(store_path)

    snapshot = {
        "increment": INCREMENT,
        "version": __version__,
        "topic": topic.get("id"),
        "discovery_mode": 'broad' if broad else 'focused',
        "discovery_plan": getattr(args, 'discovery_plan', None),
        "roles": dict(settings.roles),
        "models": {
            "screening": _model_snapshot(screening_model),
            "analysis": _model_snapshot(candidate),
        },
        "limits": settings.limits,
        "profile": {"path": str(profile_path), "id": profile.get("profile_id"), "confirmed": profile_confirmed},
        "prompt_version": PROMPT_VERSION,
        "rules_version": RULES_VERSION,
    }
    store.start_run(run_id, kind="increment-a-analysis", started_at=now_iso(), snapshot=snapshot)
    _print(f"# learning-radar 增量 A 运行 {run_id}（{_local_time()}）")
    _print(f"模型角色：{_roles_text(settings)}")
    _print(
        f"  screening → {screening_model.describe()}"
        f"；analysis → {candidate.describe()}（推理参数来自配置：{candidate.reasoning_effort or '未设置'}）"
    )
    if not profile_confirmed:
        source = (
            "示例占位档案 config/profile.example.toml"
            if profile_placeholder
            else f"草稿 {profile_path.name}（内容未由你确认）"
        )
        _print(f"注意：学习档案尚未确认，当前使用{source}，报告中会如实标注。")

    hooks.stage("search")
    client = (hooks.client_factory or _client)(settings, audit)
    query = str(topic.get("github_query", ""))
    discovery_info = None
    if args.candidates_from:
        items = _load_candidates_from_run(settings, args.candidates_from)
        if not items:
            store.log_http(run_id, audit)
            store.finish_run(run_id, status="failed", finished_at=now_iso(), notes="没有可复用的候选")
            store.close()
            _print(f"运行 {args.candidates_from} 里没有可复用的候选（需要该运行保存过候选）")
            return EXIT_USAGE
        _print(f"\n[1/5] 复用运行 {args.candidates_from} 的候选：{len(items)} 个（不重新搜索）")
    else:
        _print(f"\n[1/5] {'fixture 离线候选（未真实搜索）' if client.mode == 'fixture' else '真实 GitHub 搜索'}：{query}")
        try:
            if broad:
                from .discovery import collect_candidates
                items, discovery_info = collect_candidates(client, args.discovery_plan)
                (runs_dir/'discovery.json').write_text(json.dumps(discovery_info, ensure_ascii=False, indent=2), encoding='utf-8')
                if discovery_info['all_failed']:
                    raise FetchError('本次所有发现查询失败；未复用旧候选')
            else:
                items = client.search_repositories(query, per_page=max(args.per_page, args.limit))
        except FetchError as exc:
            store.log_http(run_id, audit)
            store.finish_run(run_id, status="failed", finished_at=now_iso(), notes=f"搜索失败：{exc}")
            store.close()
            _print(f"搜索失败：{exc}")
            return EXIT_USAGE
    store.save_candidates(run_id, items, role="discovered")

    usage_entries: list[dict[str, Any]] = []
    model_budget = hooks.budget or ModelBudget(settings.limit("max_model_calls_per_run", 4))
    _print(
        f"模型请求预算：最多 {model_budget.limit} 次（screening 与 analysis 共享，失败也计入）"
    )
    cooldown = cooldown_config(settings.selection)
    cooldown_now = now_iso()
    cooldown_warnings: list[str] = []
    cooldown_decisions = filter_items(
        items,
        _cooldown_history(settings, warnings=cooldown_warnings),
        config=cooldown,
        now=cooldown_now,
    )
    for note in cooldown_warnings[:5]:
        _print(f"  - 冷却检查提示：{note}")
    if len(cooldown_warnings) > 5:
        _print(f"  - 冷却检查提示：另有 {len(cooldown_warnings) - 5} 条同类提示")
    eligible_items, cooling_items = split_items(items, cooldown_decisions)
    cooling_decisions = [item for item in cooldown_decisions if item.blocked]
    if discovery_info is not None:
        discovery_info['cooldown_skipped'] = len(cooling_items)
        (runs_dir/'discovery.json').write_text(json.dumps(discovery_info, ensure_ascii=False, indent=2), encoding='utf-8')
    _print(f"\n[1c/5] 冷却期检查：{summary_line(cooldown_decisions)}")
    for decision in cooling_decisions:
        _print(f"  - {cooldown_line(decision)}")
    if args.candidate:
        _print_history_notice(settings, args.candidate)
    if cooling_items:
        store.save_candidates(run_id, cooling_items, role="skipped-cooldown")
    if not eligible_items and not args.candidate:
        status = "all-candidates-in-cooldown" if items else "no-suitable-candidate"
        (runs_dir / "usage.json").write_text(
            json.dumps(
                {
                    "status": status,
                    "usage": usage_entries,
                    "totals": _aggregate_usage(usage_entries),
                    "http": audit_summary(audit),
                    "budget": {
                        "limit": model_budget.limit,
                        "used": model_budget.used,
                        "screening_requests": 0,
                        "analysis_requests": 0,
                    },
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        (runs_dir / "audit.json").write_text(
            json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (runs_dir / "run-context.json").write_text(
            json.dumps(
                {
                    "run_id": run_id,
                    "stage": "cooldown-skipped",
                    "target": "",
                    "comparators": [],
                    "topic": topic.get("id"),
                    "query": query,
                    "selection_note": "全部候选都在冷却期内：未选点、未调用模型" if items else "本次查询无候选：未选点、未调用模型",
                    "cooldown": to_context(cooldown_decisions, config=cooldown, now=cooldown_now),
                    "candidates": [str(item.get("full_name") or "") for item in items],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        store.save_usage(run_id, usage_entries)
        store.log_http(run_id, audit)
        store.finish_run(
            run_id,
            status=status,
            finished_at=now_iso(),
            notes=f"本次候选均在冷却期内：跳过 {len(cooling_items)} 个；模型调用 0 次" if items else "本次查询未取得候选；模型调用 0 次",
        )
        store.close()
        _print("本次候选均在冷却期内：没有候选进入初筛，模型调用 0 次。" if items else "本次查询未取得候选：没有候选进入初筛，模型调用 0 次。")
        _print("  这不等于网上没有合适项目：冷却到期后会自然重新参与。")
        _print(f"  运行记录：{runs_dir}")
        return EXIT_GATE_FAILED

    screening_cap = settings.limit("screening_max_candidates", 5)
    screening_input = (items if args.candidate else eligible_items)[:screening_cap]
    if discovery_info is not None:
        discovery_info.update(cooldown_skipped=len(cooling_items), screening_count=len(screening_input))
        (runs_dir/'discovery.json').write_text(json.dumps(discovery_info, ensure_ascii=False, indent=2), encoding='utf-8')
    _print(f"  送筛候选：{len(screening_input)} 个（上限 {screening_cap} 个）")
    hooks.stage("screening")
    if args.screening_from:
        try:
            screening = _load_screening_from_run(settings, args.screening_from)
        except ConfigError as exc:
            _print(f"无法复用初筛结论：{exc}")
            return EXIT_USAGE
        _print(f"  复用运行 {args.screening_from} 的初筛结论（本次不调用初筛模型）")
        _print(f"  {screening.summary()}")
    else:
        screening = _run_screening_stage(
            settings,
            audit=audit,
            profile=profile,
            profile_confirmed=profile_confirmed,
            candidates=screening_input,
            topic=topic,
            model=screening_model,
            usage_entries=usage_entries,
            budget=model_budget,
            adapter_factory=model_factory,
        )
    _snapshot_usage(
        runs_dir, usage_entries, status="in-progress-screening", budget=model_budget, audit=audit
    )
    if screening.failed and not args.candidate:
        status = "screening-failed"
        _print("  - 初筛失败：自动发现模式下停止，不调用分析模型（候选与诊断已保存）")
        supported = not args.screening_only
        (runs_dir / "usage.json").write_text(
            json.dumps(
                {
                    "status": status,
                    "usage": usage_entries,
                    "totals": _aggregate_usage(usage_entries),
                    "http": audit_summary(audit),
                    "budget": {
                        "limit": model_budget.limit,
                        "used": model_budget.used,
                        "screening_requests": screening.requests,
                        "analysis_requests": 0,
                    },
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        (runs_dir / "audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
        (runs_dir / "run-context.json").write_text(
            json.dumps(
                {
                    "run_id": run_id,
                    "stage": "screening-only" if args.screening_only else "screening-failed",
                    "target": "",
                    "comparators": [],
                    "topic": topic.get("id"),
                    "query": query,
                    "selection_note": "初筛失败：未进行选点",
                    "screening": {
                        "entries": screening.entries,
                        "failed": screening.failed,
                        "error": screening.error,
                        "summary": screening.summary(),
                        "note": screening.prompt_note(),
                    },
                    "candidates": [str(item.get("full_name") or "") for item in items],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        store.save_usage(run_id, usage_entries)
        store.log_http(run_id, audit)
        store.finish_run(
            run_id,
            status=status,
            finished_at=now_iso(),
            notes=f"初筛失败：{screening.error}"[:500],
        )
        store.close()
        for note in client.notes:
            _print(f"  - 采集说明：{note}")
        _print("没有调用分析模型；候选、初筛诊断与用量都已保存。")
        return EXIT_GATE_FAILED

    if args.screening_only:
        status = "screening-ok" if not screening.failed else "screening-failed"
        (runs_dir / "usage.json").write_text(
            json.dumps(
                {
                    "status": status,
                    "usage": usage_entries,
                    "totals": _aggregate_usage(usage_entries),
                    "http": audit_summary(audit),
                    "budget": {
                        "limit": model_budget.limit,
                        "used": model_budget.used,
                        "screening_requests": screening.requests,
                        "analysis_requests": 0,
                    },
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        (runs_dir / "run-context.json").write_text(
            json.dumps(
                {
                    "run_id": run_id,
                    "stage": "screening-only",
                    "target": "",
                    "comparators": [],
                    "topic": topic.get("id"),
                    "query": query,
                    "selection_note": "",
                    "screening": {
                        "entries": screening.entries,
                        "failed": screening.failed,
                        "error": screening.error,
                        "summary": screening.summary(),
                        "note": screening.prompt_note(),
                    },
                    "candidates": [str(item.get("full_name") or "") for item in items],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        store.save_usage(run_id, usage_entries)
        store.log_http(run_id, audit)
        store.finish_run(
            run_id,
            status=status,
            finished_at=now_iso(),
            notes="只验证初筛，未取证、未调用分析模型",
        )
        store.close()
        _print(f"仅验证初筛：{status}（未取证、未调用分析模型）")
        _print(f"  运行数据：{runs_dir}")
        _print(f"  初筛结论可用 --screening-from {run_id} 复用")
        return EXIT_OK if not screening.failed else EXIT_GATE_FAILED

    # 手动指定目标时初筛失败不阻塞（初筛结论只作参考）
    if screening.failed and args.candidate:
        _print("  - 初筛失败，但目标由命令行指定：继续分析（不依赖初筛结论）")
    retained_names = set(screening.retained_names)
    retained_items = [
        item for item in screening_input if str(item.get("full_name")) in retained_names
    ] or screening_input

    target_name = ""
    target_repo: dict[str, Any] = {}
    selection_note = ""
    if args.candidate:
        target_name = _parse_repo(args.candidate)
        try:
            target_repo = client.get_repository(target_name)
            decision = screening.decision_for(target_name)
            selection_note = f"由命令行显式指定目标项目（初筛结论：{decision}）"
        except FetchError as exc:
            store.log_http(run_id, audit)
            store.finish_run(run_id, status="failed", finished_at=now_iso(), notes=f"读取目标失败：{exc}")
            store.close()
            _print(f"读取目标项目失败：{exc}")
            return EXIT_USAGE
    else:
        picked, pick_note = _select_auto_target(
            retained_items,
            decisions=_decision_map(screening),
            fits=_fit_map(screening),
            reasons=_reason_map(screening),
            selection=settings.selection,
        )
        if picked is None:
            store.log_http(run_id, audit)
            store.finish_run(
                run_id,
                status="no-suitable-candidate",
                finished_at=now_iso(),
                notes=pick_note,
            )
            store.close()
            _print(f"本轮没有合适候选：{pick_note}；按规则不推荐、不生成报告。")
            return EXIT_GATE_FAILED
        target_repo = picked
        target_name = str(picked.get("full_name"))
        selection_note = f"自动选择（{pick_note}）"
    _print(f"目标项目：{target_name}（{selection_note}）")
    if args.candidate:
        _print_history_notice(settings, target_name)

    comparator_names = [_parse_repo(item) for item in (args.compare or [])]
    if not comparator_names:
        auto = _auto_comparators(items, target_name, settings.limit("min_comparators", 2))
        comparator_names = [str(item.get("full_name")) for item in auto]
    comparator_names = [name for name in comparator_names if name.lower() != target_name.lower()]
    if len(comparator_names) < settings.limit("min_comparators", 2):
        _print(f"对照项目不足（需要至少 {settings.limit('min_comparators', 2)} 个），本次不发布报告。")
        store.log_http(run_id, audit)
        store.finish_run(run_id, status="insufficient-comparators", finished_at=now_iso())
        store.close()
        return EXIT_GATE_FAILED
    _print(f"对照项目：{', '.join(comparator_names)}")

    hooks.stage("evidence")
    _print("\n[2/5] 读取公开证据")
    max_sources = settings.limit("max_sources_per_candidate", 8)
    readme_options = {
        "head_chars": settings.limit("readme_head_chars", 500),
        "per_section": settings.limit("readme_section_chars", 900),
        "max_sections": settings.limit("readme_max_sections", 4),
        "budget": settings.limit("readme_budget_chars", 3200),
    }
    collected_target = collect(
        client,
        target_name,
        role="target",
        max_sources=max_sources,
        readme_options=readme_options,
        fetch_learning_entry=bool(settings.limits.get("fetch_learning_entry", True)),
        learning_entry_max_attempts=settings.limit("learning_entry_max_attempts", 3),
        example_file_max=settings.limit("example_file_max", 2),
        docs_followup_max=settings.limit("docs_followup_max", 1),
        entry_evidence_max=settings.limit("entry_evidence_max", 2),
    )
    _persist_pages(runs_dir, collected_target.pages)
    collected_comparators = [
        collect(
            client,
            name,
            role="comparator",
            max_sources=max_sources,
            readme_options=readme_options,
        )
        for name in comparator_names
    ]
    all_evidence: list[Evidence] = list(collected_target.evidence)
    for collected in collected_comparators:
        all_evidence.extend(collected.evidence)
        if collected.errors:
            _print(f"  - {collected.full_name} 部分读取失败：{collected.errors}")
    _print(f"  证据条目 {len(all_evidence)} 条；HTTP 请求 {audit_summary(audit)}")
    for note in client.notes:
        _print(f"  - 采集说明：{note}")
    if collected_target.errors:
        _print(f"  - 目标项目部分读取失败：{collected_target.errors}")
    entry = collected_target.learning_entry or {}
    if entry.get("verified") and entry.get("example_signal"):
        entry_state = "（已实际读取，检测到示例信号；本次未实际运行）"
    elif entry.get("verified"):
        entry_state = "（已读取，但没有检测到示例信号：学习动作须标为暂不可执行）"
    else:
        entry_state = "（未核验：学习动作必须标为暂不可执行）"
    _print(f"  学习入口：{entry.get('url') or '未取到'}{entry_state}")
    if not collected_target.evidence:
        store.log_http(run_id, audit)
        store.finish_run(run_id, status="no-evidence", finished_at=now_iso())
        store.close()
        _print("目标项目没有取到任何证据，停止（不伪造结论）。")
        return EXIT_USAGE
    store.save_evidence(run_id, all_evidence)

    run_context = {
        "run_id": run_id,
        "discovery_mode": 'broad' if broad else 'focused',
        "discovery": discovery_info,
        "target": target_name,
        "comparators": list(comparator_names),
        "topic": topic.get("id"),
        "query": query,
        "selection_note": selection_note,
        "cycle": getattr(args, "cycle_id", ""),
        "screening": {
            "entries": screening.entries,
            "failed": screening.failed,
            "error": screening.error,
            "summary": screening.summary(),
            "note": screening.prompt_note(),
        },
        "cooldown": to_context(cooldown_decisions, config=cooldown, now=cooldown_now),
        "learning_entry": collected_target.learning_entry,
        "profile": {"id": profile.get("profile_id"), "confirmed": profile_confirmed},
        "roles": dict(settings.roles),
        "models": {
            "screening": _model_snapshot(screening_model),
            "analysis": _model_snapshot(candidate),
        },
        "evidence_ids": [item.evidence_id for item in all_evidence],
    }
    (runs_dir / "run-context.json").write_text(
        json.dumps(run_context, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    target_payload = {
        "repo": target_name,
        "role": "target",
        "facts": collected_target.facts,
        "readme_available": collected_target.readme_available,
        "readme_sections": collected_target.sections,
        "learning_entry": collected_target.learning_entry,
        "screening_note": screening.prompt_note(),
        "collection_errors": collected_target.errors,
    }
    comparator_payload = [
        {
            "repo": collected.full_name,
            "role": "comparator",
            "facts": collected.facts,
            "readme_available": collected.readme_available,
            "collection_errors": collected.errors,
        }
        for collected in collected_comparators
    ]

    hooks.stage("analysis")
    _print(f"\n[3/5] 调用分析模型（角色 analysis → {candidate.describe()}）")
    budget_before_analysis = model_budget.used
    max_calls = max(1, min(settings.limit("analysis_max_calls", 2), model_budget.remaining))
    responses_seen: list[Any] = []
    try:
        adapter = BudgetedAdapter(model_factory(candidate, audit=audit), model_budget)
        outcome = run_analysis(
            adapter,
            settings,
            profile=profile,
            profile_confirmed=profile_confirmed,
            target=target_payload,
            comparators=comparator_payload,
            evidence=all_evidence,
            feedback_text=_feedback_prompt_text(settings, [target_name, *comparator_names]),
            max_calls=max_calls,
            sink=responses_seen,
            report_validator=lambda draft: _validate_analysis_candidate(
                settings, draft, evidence=all_evidence, target_name=target_name,
                comparator_names=comparator_names, require_quotes=True,
                require_verified_entry=bool((collected_target.learning_entry or {}).get("verified")),
                verified_entry=collected_target.learning_entry or None,
            ).errors,
        )
    except (ModelError, ModelBudgetExceeded, AnalysisError, FetchError) as exc:
        # 失败也要把已经发生的调用、消耗和原始输出留下，便于核对与修复。
        analysis_requests = model_budget.used - budget_before_analysis
        failed_usage = _failure_usage_rows(
            usage_entries,
            responses_seen,
            candidate,
            str(exc),
            unrecorded_calls=analysis_requests - len(responses_seen),
        )
        (runs_dir / "usage.json").write_text(
            json.dumps(
                {
                    "status": "model-failed",
                    "error": str(exc)[:800],
                    "usage": failed_usage,
                    "totals": _aggregate_usage(failed_usage),
                    "http": audit_summary(audit),
                    "budget": {
                        "limit": model_budget.limit,
                        "used": model_budget.used,
                        "screening_requests": screening.requests,
                        "analysis_requests": model_budget.used - budget_before_analysis,
                    },
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        if responses_seen:
            (runs_dir / "raw_model_output.txt").write_text(
                "\n\n=====\n\n".join(item.text for item in responses_seen), encoding="utf-8"
            )
        store.save_usage(run_id, failed_usage)
        store.log_http(run_id, audit)
        store.finish_run(run_id, status="model-failed", finished_at=now_iso(), notes=str(exc)[:500])
        (runs_dir / "evidence.json").write_text(
            json.dumps([item.to_dict() for item in all_evidence], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        store.close()
        _print(f"模型阶段失败（kind={getattr(exc, 'kind', '-')}）：{exc}")
        _print(f"  已记录 {len(responses_seen)} 次真实调用与原始输出：{runs_dir}")
        _print("已保留证据与 HTTP 记录，未生成报告（不降级、不用假数据顶替）。")
        return EXIT_MODEL_UNAVAILABLE

    for response in outcome.responses:
        entry = _usage_entry("analysis", response, candidate, role="analysis")
        usage_entries.append(entry)
        if entry.get("model_mismatch"):
            _print(
                f"  - 注意：配置的模型是 {candidate.model_id}，接口回报的是 "
                f"{entry.get('reported_model_id')}；已同时记录，请核对是否被替换"
            )
    for error in outcome.failed_requests:
        usage_entries.append(_failure_entry("analysis", candidate, error, role="analysis", calls=1))
    _save_analysis_checks(runs_dir, outcome)
    (runs_dir / "raw_model_output.txt").write_text(
        "\n\n=====\n\n".join(response.text for response in outcome.responses), encoding="utf-8"
    )
    _snapshot_usage(
        runs_dir, usage_entries, status="in-progress-analysis", budget=model_budget, audit=audit
    )
    analysis_requests = model_budget.used - budget_before_analysis
    for entry in usage_entries:
        cost = "未知" if entry.get("cost_value") is None else entry.get("cost_value")
        state = "" if entry.get("ok", True) else f"，失败：{entry.get('error')}"
        _print(
            f"  [{entry.get('stage')}/{entry.get('role') or '-'}] {entry.get('model_id')}："
            f"{entry.get('calls')} 次调用，{entry.get('latency_ms')} ms，"
            f"输入 {entry.get('input_tokens')} / 输出 {entry.get('output_tokens')} token，费用 {cost}{state}"
        )

    report = outcome.report
    report.setdefault("target", {})
    if isinstance(report["target"], dict):
        report["target"].setdefault("repo", target_name)

    routes_text = "；".join(
        "{} 元数据入口={}".format(item.full_name, item.routes.get("repo") or "未知")
        for item in [collected_target, *collected_comparators]
    )

    _print("\n[4/5] 结构检查与落盘")
    status, validation, md_path, meta = finalize_run(
        settings,
        run_id=run_id,
        report=report,
        evidence=all_evidence,
        target_name=target_name,
        comparator_names=comparator_names,
        candidate=candidate,
        profile=profile,
        profile_confirmed=profile_confirmed,
        topic=topic,
        query=query,
        selection_note=selection_note,
        discovered_count=len(items),
        client_mode=client.mode,
        routes_text=routes_text,
        usage_entries=usage_entries,
        audit=audit,
        fixture=outcome.fixture or candidate.is_fixture,
        require_verified_entry=bool((collected_target.learning_entry or {}).get("verified")),
        verified_entry=collected_target.learning_entry or None,
        require_quotes=True,
        roles_text=_roles_text(settings),
        screening_text=screening.summary(),
        budget_info={
            "limit": model_budget.limit,
            "used": model_budget.used,
            "screening_requests": screening.requests,
            "analysis_requests": analysis_requests,
        },
        on_stage=hooks.stage,
    )
    store.close()
    for warning in validation.warnings:
        _print(f"  - 警告：{warning}")
    for error in validation.errors:
        _print(f"  - 错误：{error}")
    _print(f"  结构检查：{validation.structure_label}；内容复核：未完成（需要人工）")

    _print("\n[5/5] 报告渲染")
    _print(f"  Markdown：{md_path or '（渲染失败，见运行目录）'}")
    _print(f"  运行数据：{runs_dir}")
    if status != "published":
        _print(f"运行终态：{status}（未发布；原始输出、用量与诊断已落盘）")
        return EXIT_GATE_FAILED

    _print("")
    _print(
        f"模型请求：{model_budget.describe()}（screening {screening.requests}，analysis {analysis_requests}，含失败）"
    )
    _print("消耗汇总：")
    for key, value in meta.items():
        _print(f"  {key}: {value}")
    _print("")
    _print(
        f"查看报告：python -m learnradar.cli serve --port 8765"
        f"  ->  http://127.0.0.1:8765/report/{run_id}"
    )
    return EXIT_OK


def _aggregate_usage(entries: list[dict[str, Any]]) -> dict[str, Any]:
    def total(key: str) -> int | None:
        values = [entry.get(key) for entry in entries if entry.get(key) is not None]
        return sum(values) if values else None

    costs = [entry.get("cost_value") for entry in entries]
    known = [value for value in costs if value is not None]
    return {
        "calls": len(entries),
        "input_tokens": total("input_tokens"),
        "output_tokens": total("output_tokens"),
        "reasoning_tokens": total("reasoning_tokens"),
        "latency_ms": sum(int(entry.get("latency_ms") or 0) for entry in entries) or None,
        "cost_value": round(sum(known), 6) if known else None,
        "cost_currency": entries[0].get("cost_currency") if entries else None,
        "pricing_known": bool(known),
    }


def cmd_list(settings: Settings, args: argparse.Namespace) -> int:
    with Store(settings.data_dir / "learning-radar.sqlite") as store:
        rows = store.list_reports(limit=args.limit)
    if not rows:
        _print("还没有报告。")
        return EXIT_OK
    _print(f"{'run_id':<28} {'状态':<10} 标题")
    for row in rows:
        _print(f"{row['run_id']:<28} {row['status']:<10} {row['title']}")
    return EXIT_OK


def cmd_show(settings: Settings, args: argparse.Namespace) -> int:
    with Store(settings.data_dir / "learning-radar.sqlite") as store:
        rows = store.list_reports(limit=args.limit)
        if not rows:
            _print("还没有报告。")
            return EXIT_OK
        target = args.run_id or rows[0]["run_id"]
        row = store.get_report(target)
    if not row:
        _print(f"没有找到 {target}")
        return EXIT_USAGE
    _print(f"# {row['title']}")
    _print(f"状态：{row['status']}　生成时间：{row['created_at']}")
    _print(f"质量门槛：{row['validation']}")
    path = Path(row["markdown_path"] or "")
    if path.exists():
        _print(path.read_text(encoding="utf-8"))
    return EXIT_OK


def cmd_serve(settings: Settings, args: argparse.Namespace) -> int:
    store_path = settings.data_dir / "learning-radar.sqlite"
    if not store_path.exists():
        _print(f"还没有数据库：{store_path}；先运行 run 命令。")
        return EXIT_USAGE
    from .web_config import load_web_config
    server = serve(store_path, host=args.host, port=args.port, settings=settings,
                   web_config=load_web_config(settings, mode=getattr(args, "web_mode", None), demo=bool(getattr(args, "demo", False))),
                   demo=bool(getattr(args, "demo", False)))
    _print(f"报告页面已启动：http://{args.host}:{server.server_port}/  （Ctrl+C 退出）")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        _print("\n已停止。")
    finally:
        server.server_close()
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="learnradar", description="learning-radar（增量 A）")
    parser.add_argument("--version", action="version", version=f"learning-radar {__version__} (increment {INCREMENT})")
    sub = parser.add_subparsers(dest="command", required=True)

    verify = sub.add_parser("verify-models", help="核验模型接入是否真的可用")
    verify.add_argument("--candidate", help="只核验某个候选（默认全部）")
    verify.add_argument("--probe", dest="probe", action="store_true", default=None, help="强制发一次最小生成调用")
    verify.add_argument("--no-probe", dest="probe", action="store_false", help="只查 /models，不做生成调用")
    verify.set_defaults(func=cmd_verify_models)

    discover = sub.add_parser("discover", help="真实 GitHub 搜索候选")
    discover.add_argument("--topic", help="主题 id，默认使用第一个主题")
    discover.add_argument("--per-page", type=int, default=10)
    discover.add_argument("--limit", type=int, default=10)
    discover.set_defaults(func=cmd_discover)

    run = sub.add_parser("run", help="证据收集 -> 模型分析 -> 质量门槛 -> 渲染报告")
    run.add_argument("--topic", help="指定已有方向（focused）；省略则广泛发现")
    run.add_argument('--discovery-mode', choices=('broad','focused'))
    run.add_argument("--candidate", help="目标仓库 owner/name；不填则按规则自动选")
    run.add_argument("--compare", action="append", help="对照仓库 owner/name，可重复；不填则自动选")
    run.add_argument("--model", help="使用哪个模型候选（默认 settings.toml 里的 models.analysis）")
    run.add_argument("--candidates-from", dest="candidates_from",
                     help="复用某次运行保存的候选，不重新搜索")
    run.add_argument("--screening-only", dest="screening_only", action="store_true",
                     help="只跑初筛并保存结论，不取证、不调用分析模型")
    run.add_argument("--screening-from", dest="screening_from",
                     help="复用某次运行保存的初筛结论，不重复调用初筛模型")
    run.add_argument("--run-id", dest="run_id", help="自定义运行 ID（周任务用它绑定周期）")
    run.add_argument("--per-page", type=int, default=20)
    run.add_argument("--limit", type=int, default=20)
    run.set_defaults(func=cmd_run)

    list_cmd = sub.add_parser("list", help="列出报告")
    list_cmd.add_argument("--limit", type=int, default=10)
    list_cmd.set_defaults(func=cmd_list)

    show = sub.add_parser("show", help="打印报告内容")
    show.add_argument("--run-id", dest="run_id")
    show.add_argument("--limit", type=int, default=1)
    show.set_defaults(func=cmd_show)

    replay = sub.add_parser(
        "replay-analysis", help="用已保存的证据单独重跑分析阶段（不重新搜索、不重新初筛）"
    )
    replay.add_argument("--run", required=True, help="要重放的运行 ID（data/runs/<id>）")
    replay.add_argument("--target", help="覆盖目标仓库")
    replay.add_argument("--compare", action="append", help="覆盖对照仓库，可重复")
    replay.add_argument("--model", help="临时改用另一个分析候选")
    replay.add_argument("--run-id", dest="run_id", help="新运行 ID（默认自动生成）")
    replay.set_defaults(func=cmd_replay_analysis)

    weekly = sub.add_parser("weekly", help="云端每周任务：一次分析 + 一封邮件（systemd timer 调用）")
    weekly.add_argument("--topic", help="覆盖 settings.toml 里 [schedule].topic")
    weekly.add_argument('--discovery-mode', choices=('broad','focused'), help='缺省沿用 schedule.discovery_mode；旧配置保持 focused')
    weekly.add_argument("--cycle", help="覆盖周期 ID（默认按配置时区算 ISO 周，例如 2026-W39）")
    weekly.add_argument("--force-redo", dest="force_redo", action="store_true",
                        help="人工恢复：仅当该周期是 failed/interrupted/unknown 时才允许重跑")
    weekly.set_defaults(func=cmd_weekly)

    for entry in (run,replay,weekly):
        entry.add_argument("--data-dir",type=Path,help="统一队列数据目录")
        entry.add_argument("--demo",action="store_true",help="显式假模型/假 SMTP；须使用隔离数据目录")
        entry.add_argument("--demo-delay",type=float,default=1)
        entry.add_argument("--demo-scenario",choices=("success","rejected","model-failed","mail-failed","mail-unknown"),default="success")

    preview = sub.add_parser("email-preview", help="只读已保存结果，打印将要发送的邮件（不分析、不发送）")
    preview.add_argument("--run-id", dest="run_id")
    preview.add_argument("--cycle")
    preview.set_defaults(func=cmd_email_preview)

    retry = sub.add_parser("email-retry", help="单独重发已保存的邮件（不重新分析）")
    retry.add_argument("--email-id", dest="email_id")
    retry.add_argument("--run-id", dest="run_id")
    retry.add_argument("--cycle")
    retry.add_argument("--force", action="store_true", help="上一次结果不确定（unknown）时才需要")
    retry.set_defaults(func=cmd_email_retry)

    send_cmd = sub.add_parser(
        "email-send", help="给已保存的运行手动发送邮件（不分析、不占周期；已发送不重发）"
    )
    send_cmd.add_argument("--run-id", dest="run_id", required=True, help="运行 ID，例如 20260927T215205-agent-memory")
    send_cmd.add_argument("--force", action="store_true", help="上一次结果不确定（unknown）时才需要")
    send_cmd.set_defaults(func=cmd_email_send)

    recover_cmd = sub.add_parser("cycle-recover", help="把卡在 running 的周期明确记为 interrupted（带条件检查与备份）")
    recover_cmd.add_argument("--cycle", required=True, help="周期号，例如 2026-W38")
    recover_cmd.add_argument("--backup-out", dest="backup_out",
                             help="恢复前的一致性备份路径（默认 <数据目录>/backups/<周期>-before-recover.sqlite）")
    recover_cmd.set_defaults(func=cmd_cycle_recover)

    cycles_cmd = sub.add_parser("cycles", help="查看周期执行记录（人工恢复用）")
    cycles_cmd.add_argument("--limit", type=int, default=10)
    cycles_cmd.set_defaults(func=cmd_cycles)

    test_cmd = sub.add_parser("email-test", help="只测 SMTP：不发报告、不调用模型、不占周期")
    test_cmd.set_defaults(func=cmd_email_test)

    backup_cmd = sub.add_parser("backup", help="用 SQLite 在线备份 API 生成一致性备份")
    backup_cmd.add_argument("--out", required=True, help="备份文件路径，例如 /var/backups/learning-radar.sqlite")
    backup_cmd.add_argument("--bundle-dir", dest="bundle_dir",
                            help="同时把 reports/ 与 runs/ 复制到这个目录（不含档案与密钥）")
    backup_cmd.set_defaults(func=cmd_backup)

    serve_cmd = sub.add_parser("serve", help="启动本机报告页面")
    serve_cmd.add_argument("--host", default="127.0.0.1")
    serve_cmd.add_argument("--port", type=int, default=8765)
    serve_cmd.add_argument("--web-mode", choices=("loopback", "https"))
    serve_cmd.add_argument("--demo", action="store_true")
    serve_cmd.add_argument("--data-dir", type=Path)
    serve_cmd.set_defaults(func=cmd_serve)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        demo=bool(getattr(args,"demo",False))
        settings = load_settings(local_overrides=not demo)
        directory=getattr(args,"data_dir",None)
        if demo:
            if directory is None or directory.resolve()==settings.data_dir.resolve():
                raise ConfigError("--demo 必须使用显式隔离 --data-dir，不能使用正式目录")
            from .demo import demo_settings
            settings=demo_settings(settings,directory.resolve())
        elif directory:
            import dataclasses
            settings=dataclasses.replace(settings,app={**settings.app,"data_dir":str(directory.resolve())})
    except ConfigError as exc:
        _print(f"配置错误：{exc}")
        return EXIT_USAGE
    try:
        return int(args.func(settings, args))
    except ConfigError as exc:
        _print(f"参数错误：{exc}")
        return EXIT_USAGE
    except KeyboardInterrupt:
        _print("已中断。")
        return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
