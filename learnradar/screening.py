"""候选初筛阶段（角色 screening）。

职责最小化：只对已发现的候选做保留/排除判断，并保留排除理由；不确定的候选一律保留。
关键约束：
- 只调用配置里 screening 角色指向的模型，失败就记录失败原因，不会换别的模型试；
- 输出结构化 JSON，程序负责核对"每个输入候选都有结论"，缺失的按不确定保留；
- 这一阶段的结论只是选点依据，不能当作证据（报告里要写明）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .analysis import AnalysisError, parse_json_response
from .models import ModelAdapter, ModelError, ModelResponse

SCREENING_PROMPT_VERSION = "screening-1"

DECISIONS = ("keep", "uncertain", "exclude")
KEEP_DECISIONS = ("keep", "uncertain")
FIT_LEVELS = ("high", "medium", "low", "unknown")

SYSTEM_PROMPT = (
    "你是技术项目候选的初筛助手，为一位置身中文环境、在 AI 协助下学习的个人学习者做初筛。"
    "你只能看到仓库元数据（描述、主题、语言、星数、推送时间），看不到 README 正文，因此判断必须保持初步性质："
    "不得臆测教程是否完整、文档质量、工程质量或代码可读性；材料不足就把候选标成 uncertain。"
    "判断依据只有四条：1) 是否属于 Agent/生成式软件工程领域、亮点是否有材料支持；"
    "2) 是否有清楚的学习收益与展示（可演示、可展示）潜力；"
    "3) 在 AI 协助下能否开始一个范围有限的小实验；"
    "4) 环境、依赖、设备、API 与改动范围是否构成实际门槛。"
    "编程语言只是环境信息：陌生语言不能单独作为排除理由，AI 协助也不是能克服所有困难的保证。"
    "给出 keep（保留）、uncertain（不确定，保留）或 exclude（排除）；拿不准的一律 uncertain。"
    "输出要极短：每个候选只写 decision、fit、一条最关键的理由、以及一个需要后续核验的门槛。"
    "不要逐条展开四个维度，不要评价还没读到的教程或工程质量。"
    "只输出一个完整的 JSON 对象，不要输出解释性文字或 Markdown 代码块。"
)


@dataclass
class ScreeningOutcome:
    entries: list[dict[str, Any]]
    responses: list[ModelResponse] = field(default_factory=list)
    failed: bool = False
    error: str = ""
    warnings: list[str] = field(default_factory=list)
    attempts: int = 0
    requests: int = 0

    @property
    def retained_names(self) -> list[str]:
        return [item["repo"] for item in self.entries if item["decision"] in KEEP_DECISIONS]

    @property
    def excluded(self) -> list[dict[str, Any]]:
        return [item for item in self.entries if item["decision"] == "exclude"]

    def ranking(self) -> list[dict[str, Any]]:
        """按选点规则排好序的候选视图（decision -> fit），用于打印与留档。"""
        fit_order = {"high": 0, "medium": 1, "low": 2, "unknown": 3}
        rows = list(self.entries)
        rows.sort(key=lambda item: (item.get("decision") != "keep", fit_order.get(str(item.get("fit")), 3)))
        return rows

    def decision_for(self, repo: str) -> str:
        """目标项目的初筛结论；不在候选清单里就明确写出来，不假装筛过。"""
        for item in self.entries:
            if str(item.get("repo", "")).lower() == str(repo).lower():
                return str(item.get("decision"))
        return "未在本次候选清单内"

    def summary(self, *, limit: int = 6) -> str:
        kept = [item for item in self.entries if item["decision"] == "keep"]
        uncertain = [item for item in self.entries if item["decision"] == "uncertain"]
        excluded = self.excluded
        high = [item for item in kept if str(item.get("fit")) == "high"]
        head = f"初筛 {len(self.entries)} 个候选：保留 {len(kept)}（其中 fit=high {len(high)}）、不确定 {len(uncertain)}、排除 {len(excluded)}"
        if self.failed:
            head = f"初筛未完成（{self.error}）：全部候选按不确定保留，共 {len(self.entries)} 个"
        details = []
        for item in (excluded[:limit]):
            details.append(f"排除 {item['repo']}：{item.get('reason') or '（未给理由）'}")
        for item in (uncertain[:limit]):
            details.append(f"不确定 {item['repo']}：{item.get('reason') or '（未给理由）'}")
        return head + ("；" + "；".join(details) if details else "")

    def prompt_note(self) -> str:
        """给分析模型看的说明：明确这不是证据。"""
        lines = [
            "以下是初筛阶段（screening 角色）的结论，只用于说明为什么选中这个项目。",
            "它不是证据：不能作为任何事实结论的 evidence_ids，也不能替代下面的证据清单。",
            self.summary(limit=4),
        ]
        return "\n".join(lines)


def build_prompt(
    *,
    profile: dict[str, Any],
    profile_confirmed: bool,
    candidates: list[dict[str, Any]],
    topic: dict[str, Any],
    max_keep: int,
) -> str:
    compact = [
        {
            "repo": item.get("full_name"),
            "description": item.get("description"),
            "topics": item.get("topics") or [],
            "language": item.get("language"),
            "stars": item.get("stargazers_count"),
            "pushed_at": item.get("pushed_at"),
            "archived": item.get("archived"),
            "is_fork": item.get("fork"),
        }
        for item in candidates
    ]
    return "\n".join(
        [
            "# 用户学习档案（" + ("用户已确认" if profile_confirmed else "草稿，尚未确认") + "）",
            "",
            "```json",
            json.dumps(profile, ensure_ascii=False, indent=2),
            "```",
            "",
            "# 本次主题",
            "",
            f"{topic.get('id')}（{topic.get('label')}）：{topic.get('github_query')}",
            "",
            "# 候选列表（共 " + str(len(compact)) + " 个）",
            "",
            "```json",
            json.dumps(compact, ensure_ascii=False, indent=2),
            "```",
            "",
            "# 要求",
            "",
            "1. 每个候选恰好给一条结论，`repo` 必须与上面完全一致，不要新增不存在的仓库。",
            "2. `decision` 只能是 `keep`、`uncertain`、`exclude`。",
            f"3. 最多保留 {max_keep} 个最相关的（keep 优先、uncertain 次之）；其余可以 exclude，但必须写理由。",
            "4. 对 keep/uncertain 的候选给 `fit`（`high`/`medium`/`low`）：综合四个维度的适配程度；证据不足就给 low。",
            "5. `reason` 只写**一条最关键的理由**，不超过 60 字（可以只涉及四个维度里的一个）；看不出材料支撑就写\"元数据不足\"。",
            "6. `gate` 写一个需要后续核验的门槛（例如需要什么运行时/密钥/设备，或哪一点还没核验），没有就写空字符串；不要把没读到的内容写成结论。",
            "7. 只看元数据：不要臆测教程完整性、文档质量或工程质量；拿不准写 uncertain。",
            "8. 语言只作环境信息：不要仅因为语言陌生就排除；也不要因为 AI 协助就假定困难都能解决。",
            "9. 不要评价代码质量，也不要引用候选之外的信息。",
            "",
            "# 输出格式（只输出 JSON）",
            "",
            "```json",
            "{",
            '  "candidates": [',
            '    {"repo": "owner/name", "decision": "keep", "fit": "high",',
            '     "reason": "一条最关键的理由（不超过 60 字）",',
            '     "gate": "需要核验的门槛；没有就写空字符串"}',
            "  ],",
            '  "notes": "可选的整体说明"',
            "}",
            "```",
        ]
    )


def _normalize(
    candidates: list[dict[str, Any]],
    payload: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[str]]:
    """把模型输出对齐到候选清单：缺失的按不确定保留，未知仓库忽略并记警告。"""
    wanted = [str(item.get("full_name")) for item in candidates]
    decisions: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []
    raw_entries = payload.get("candidates")
    if not isinstance(raw_entries, list):
        raise AnalysisError("初筛输出里没有 candidates 数组")
    for item in raw_entries:
        if not isinstance(item, dict):
            continue
        repo = str(item.get("repo") or "").strip()
        if repo not in wanted:
            if repo:
                warnings.append(f"初筛输出了本次候选之外的仓库，已忽略：{repo}")
            continue
        decision = str(item.get("decision") or "").strip().lower()
        reason = str(item.get("reason") or "").strip()
        if decision not in DECISIONS:
            warnings.append(f"{repo} 的 decision={decision!r} 不是合法取值，按不确定保留")
            decision = "uncertain"
            reason = reason or "初筛给出的取值非法"
        gate = str(item.get("gate") or "").strip()
        if decision in ("exclude", "keep") and not reason:
            warnings.append(f"{repo} 给了 {decision} 却没有写理由，按不确定保留")
            decision = "uncertain"
            reason = f"模型给了 {decision} 但未写理由"
        fit = str(item.get("fit") or "").strip().lower()
        if decision == "exclude":
            fit = ""
        elif fit not in FIT_LEVELS or fit == "unknown":
            if fit and fit != "unknown":
                warnings.append(f"{repo} 的 fit={fit!r} 不是合法取值，按 unknown 处理")
            fit = "unknown"
        decisions[repo] = {
            "repo": repo,
            "decision": decision,
            "fit": fit,
            "reason": reason,
            "gate": gate,
        }

    entries: list[dict[str, Any]] = []
    for repo in wanted:
        if repo in decisions:
            entries.append(decisions[repo])
        else:
            entries.append(
                {
                    "repo": repo,
                    "decision": "uncertain",
                    "fit": "unknown",
                    "reason": "初筛没有给出判断，按不确定保留",
                }
            )
            warnings.append(f"{repo} 在初筛输出里缺失，按不确定保留")
    return entries, warnings


def run_screening(
    adapter: ModelAdapter,
    *,
    profile: dict[str, Any],
    profile_confirmed: bool,
    candidates: list[dict[str, Any]],
    topic: dict[str, Any],
    max_keep: int,
    max_calls: int,
    sink: list[ModelResponse] | None = None,
) -> ScreeningOutcome:
    """调用一次（必要时用一次修复调用）；失败就如实返回 failed，保留全部候选。"""
    if not candidates:
        return ScreeningOutcome(entries=[], failed=False, error="没有候选可筛")
    prompt = build_prompt(
        profile=profile,
        profile_confirmed=profile_confirmed,
        candidates=candidates,
        topic=topic,
        max_keep=max_keep,
    )
    responses: list[ModelResponse] = []
    if max_calls < 1:
        raise AnalysisError("max_calls 必须至少为 1")

    def all_uncertain(error: str) -> ScreeningOutcome:
        entries = [
            {
                "repo": str(item.get("full_name")),
                "decision": "uncertain",
                "fit": "unknown",
                "reason": f"初筛失败（{error}），按不确定保留",
            }
            for item in candidates
        ]
        return ScreeningOutcome(
            entries=entries,
            responses=responses,
            failed=True,
            error=error,
            warnings=[],
            attempts=len(responses),
        )

    try:
        first = adapter.complete(system=SYSTEM_PROMPT, user=prompt)
    except ModelError as exc:
        return all_uncertain(str(exc)[:300])
    responses.append(first)
    if sink is not None:
        sink.append(first)
    try:
        payload = parse_json_response(first.text)
        entries, warnings = _normalize(candidates, payload)
        return ScreeningOutcome(
            entries=entries, responses=responses, warnings=warnings, attempts=1
        )
    except AnalysisError as first_error:
        if max_calls < 2:
            return all_uncertain(str(first_error)[:300])
        repair_prompt = (
            prompt
            + "\n\n# 上一次输出的问题\n"
            + f"你的上一次输出无法使用：{first_error}\n"
            + "请重新输出一个**完整且合法**的 JSON 对象，为上面每一个候选各给一条结论。"
        )
        try:
            second = adapter.complete(system=SYSTEM_PROMPT, user=repair_prompt)
        except ModelError as exc:
            return all_uncertain(str(exc)[:300])
        responses.append(second)
        if sink is not None:
            sink.append(second)
        try:
            payload = parse_json_response(second.text)
            entries, warnings = _normalize(candidates, payload)
        except AnalysisError as second_error:
            return all_uncertain(str(second_error)[:300])
        return ScreeningOutcome(
            entries=entries, responses=responses, warnings=warnings, attempts=2
        )
