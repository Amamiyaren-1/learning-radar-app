"""发布前质量门槛（聚焦"结构可核对"，不冒充内容已核验）。

参考规划第 3、6 节与 A.1 评审：
- 引文必须能追溯到证据编号，且必须能在所引证据的摘录里匹配到；
- 必须有至少两个**本次实际采集过**的、互不重复的对照项目；
- 对照结论必须引用属于该对照项目的证据；
- 项目数值（星数）必须绑定到所属项目，不能张冠李戴；
- 查阅状态由运行记录生成，不由模型自报；
- 不得输出装饰性评分与没有根据的精确学习时长；
- 学习动作：是否可执行由显式状态 `executable` 决定（不从链接形式或措辞推断）。声明可执行时必须给出
  具体动作、已核验入口与完成标准；声明不可执行时允许完成标准为空，并保留来源链接与缺口说明。

注意：这里的"通过"只代表结构检查通过，内容复核（摘录是否真的支持结论）由人工完成。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from .evidence import Evidence, normalize_repo_name, quote_supported_by

RULES_VERSION = "qa-6"
CONTENT_REVIEW_PENDING = "not_performed"
CONTENT_REVIEW_MANUAL = "performed_manual"

DIFFICULTY_LEVELS = {"run_example", "understand_principles", "modify_extend"}
DECISIONS = {"recommend", "watch", "not_recommend"}
CLAIM_TYPES = {"fact", "vendor_claim", "inference"}
QUOTE_REQUIRED_TYPES = {"fact", "vendor_claim"}

# 关键条件（通用分类，覆盖目标实际技术栈）：runtime = 运行时及版本
CONDITION_KEYS = ("runtime", "os_support", "gpu", "external_cost")
CONDITION_STATUS = {"evidence", "unknown", "inference", "not_applicable"}
_NOT_APPLICABLE_WORDS = re.compile(r"不适用")
# 用户已配置的通道（写在档案 resources 里）；其他提供方的密钥不能假定存在
OTHER_PROVIDER_PATTERN = re.compile(
    r"(anthropic|claude|openai|gpt-|gemini|google|azure|mistral|grok|xai|x\.ai)", re.I
)
GAP_MARKER_PATTERN = re.compile(r"缺口|未配置|未核验到|没有核验到|需另行申请|不适用|暂不可执行")
_REQUIRED_PROVIDER_SECRET = re.compile(
    r"(?:需要|要求|依赖)\s*" + OTHER_PROVIDER_PATTERN.pattern
    + r"[^。；;\n]{0,24}(?:密钥|凭据|API\s*key)", re.I
)
_ONLY_USER_API = re.compile(r"用户(?:仅|只)(?:有|配置了?|具备)\s*([A-Za-z][A-Za-z0-9._-]+)\s*API\b", re.I)
_ENTRY_UNAVAILABLE_PATTERN = re.compile(r"暂不可执行|未核验到|没有核验到|未取到")
_UNKNOWN_WORDS = re.compile(r"未知|不确定|未说明|没有找到|未提供")

REQUIRED_TOP_LEVEL = [
    "title",
    "target",
    "highlight",
    "novelty",
    "claims",
    "comparisons",
    "value_for_user",
    "starting_conditions",
    "difficulty",
    "learning_action",
    "mini_experiment",
    "explore_further",
    "recommendation",
]

_SCORE_PATTERNS = [
    re.compile(r"\b\d{1,3}\s*/\s*100\b"),
    re.compile(r"评分\s*[:：]?\s*\d"),
    re.compile(r"\bquality\s*score\s*[:：]?\s*\d", re.I),
]
_DURATION_PATTERN = re.compile(r"(大约|约|需|要)\s*\d+(\.\d+)?\s*(小时|天|周)")
_NUMBER_PATTERN = re.compile(r"(\d[\d,]*)\s*(?:stars?|星)", re.I)


@dataclass
class ValidationResult:
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    rules_version: str = RULES_VERSION
    content_review: str = CONTENT_REVIEW_PENDING

    @property
    def structure_label(self) -> str:
        return "结构检查通过" if self.ok else "结构检查未通过"

    def summary(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "structure_check": self.structure_label,
            "content_review": self.content_review,
            "rules_version": self.rules_version,
            "errors": self.errors,
            "warnings": self.warnings,
        }


def _err(result: ValidationResult, message: str) -> None:
    result.errors.append(message)


def _warn(result: ValidationResult, message: str) -> None:
    result.warnings.append(message)


def validate_report(
    report: dict[str, Any],
    *,
    evidence: list[Evidence],
    target_repo: str,
    min_comparators: int = 2,
    collected_repos: Iterable[str] | None = None,
    expected_verification: dict[str, Any] | None = None,
    require_quotes: bool = False,
    require_verified_entry: bool = False,
    verified_entry: dict[str, Any] | None = None,
) -> ValidationResult:
    result = ValidationResult(ok=True)
    if not isinstance(report, dict):
        _err(result, "报告顶层必须是对象")
        result.ok = False
        return result

    known = {item.evidence_id: item for item in evidence}
    owners = {item.evidence_id: normalize_repo_name(item.owner) for item in evidence}
    target_norm = normalize_repo_name(target_repo)
    allowed = {normalize_repo_name(name) for name in collected_repos} if collected_repos else None
    if allowed is not None:
        allowed.add(target_norm)

    for key in REQUIRED_TOP_LEVEL:
        if key not in report:
            _err(result, f"缺少必需字段：{key}")
    if result.errors:
        result.ok = False
        return result

    target = report["target"]
    if not isinstance(target, dict):
        _err(result, "target 必须是对象")
    else:
        repo = normalize_repo_name(target.get("repo"))
        if repo != target_norm:
            _err(result, f"target.repo={target.get('repo')!r} 与本次分析目标 {target_repo!r} 不一致")
        for key in ("summary_one_line", "category"):
            if not target.get(key):
                _err(result, f"target.{key} 不能为空")

    novelty = report["novelty"]
    if not isinstance(novelty, dict):
        _err(result, "novelty 必须是对象")
    else:
        if not novelty.get("what"):
            _err(result, "novelty.what 不能为空")
        ids = _check_ids(result, known, novelty.get("evidence_ids"), "novelty.evidence_ids", required=True)
        _check_owner_presence(result, ids, owners, allowed, label="novelty")
        if require_quotes:
            _check_quote(result, "novelty", novelty.get("quote"), ids, known, text=str(novelty.get("what") or ""))

    claims = report["claims"]
    if not isinstance(claims, list) or not claims:
        _err(result, "claims 必须是非空数组")
    else:
        for index, claim in enumerate(claims, start=1):
            if not isinstance(claim, dict):
                _err(result, f"claims[{index}] 必须是对象")
                continue
            claim_type = str(claim.get("type", ""))
            if claim_type not in CLAIM_TYPES:
                _err(result, f"claims[{index}].type 必须是 {sorted(CLAIM_TYPES)} 之一")
            if not claim.get("text"):
                _err(result, f"claims[{index}].text 不能为空")
            ids = _check_ids(result, known, claim.get("evidence_ids"), f"claims[{index}].evidence_ids", required=True)
            _check_owner_presence(result, ids, owners, allowed, label=f"claims[{index}]")
            if require_quotes and claim_type in QUOTE_REQUIRED_TYPES:
                _check_quote(
                    result, f"claims[{index}]", claim.get("quote"), ids, known, text=str(claim.get("text") or "")
                )
            _check_numbers(
                result,
                f"claims[{index}]",
                _text_of(claim),
                ids,
                known,
            )

    comparisons = report["comparisons"]
    if not isinstance(comparisons, list):
        _err(result, "comparisons 必须是数组")
    else:
        if len(comparisons) < min_comparators:
            _err(result, f"对照项目不足：需要至少 {min_comparators} 个，实际 {len(comparisons)} 个")
        seen: set[str] = set()
        for index, item in enumerate(comparisons, start=1):
            if not isinstance(item, dict):
                _err(result, f"comparisons[{index}] 必须是对象")
                continue
            for key in (
                "comparator",
                "why_this_comparator",
                "problem",
                "key_difference",
                "fit_conditions",
                "limitations",
            ):
                if not item.get(key):
                    _err(result, f"comparisons[{index}].{key} 不能为空")
            name = normalize_repo_name(item.get("comparator"))
            if not name:
                _err(result, f"comparisons[{index}].comparator 不能为空")
            elif name == target_norm:
                _err(result, f"comparisons[{index}] 把目标项目自己当成了对照项目")
            elif name in seen:
                _err(result, f"对照项目重复：{item.get('comparator')!r} 出现了不止一次")
            seen.add(name)
            ids = _check_ids(
                result, known, item.get("evidence_ids"), f"comparisons[{index}].evidence_ids", required=True
            )
            if allowed is not None and name and name != target_norm:
                resolved = _resolve_repo(name, allowed)
                if resolved is None:
                    _err(
                        result,
                        f"comparisons[{index}] 的对照项目 {item.get('comparator')!r} "
                        f"不在本次实际采集范围内：{sorted(allowed)}",
                    )
                else:
                    if resolved != name:
                        _warn(result, f"comparisons[{index}] 用了简写 {name!r}，已按 {resolved!r} 核对")
                    name = resolved
                    owned = {owners.get(str(evidence_id), "") for evidence_id in ids}
                    if name not in owned:
                        _err(
                            result,
                            f"comparisons[{index}] 没有引用属于 {name} 的证据（引用的是 "
                            f"{sorted(value for value in owned if value) or '无归属' }）",
                        )
                    foreign = {
                        owners[str(evidence_id)]
                        for evidence_id in ids
                        if owners.get(str(evidence_id)) and owners[str(evidence_id)] not in allowed
                    }
                    if foreign:
                        _err(result, f"comparisons[{index}] 引用了本次没有采集的项目证据：{sorted(foreign)}")
            _check_numbers(result, f"comparisons[{index}]", _text_of(item), ids, known)

    difficulty = report["difficulty"]
    if not isinstance(difficulty, dict):
        _err(result, "difficulty 必须是对象")
    else:
        level = str(difficulty.get("level", ""))
        if level not in DIFFICULTY_LEVELS:
            _err(result, f"difficulty.level={level!r} 不在 {sorted(DIFFICULTY_LEVELS)} 内")
        if not difficulty.get("basis"):
            _err(result, "difficulty.basis 不能为空（难度可以是推断，但要写依据）")
        _check_conditions(result, difficulty, known, owners, allowed)

    action = report["learning_action"]
    if not isinstance(action, dict):
        _err(result, "learning_action 必须是对象")
    else:
        # “本次是否提供了可执行动作”由显式状态 executable 决定：
        # 不看入口是不是 URL，也不看措辞。不可执行时同样允许保留来源链接与缺口说明。
        executable = action.get("executable")
        if not isinstance(executable, bool):
            _err(
                result,
                "learning_action.executable 必须是 true/false：本次是否提供了可执行的学习动作要显式写明，"
                "不能用入口是不是 URL 或措辞来推断",
            )
        if not action.get("action"):
            _err(result, "learning_action.action 不能为空")
        entry = str(action.get("entry", ""))
        is_link = entry.startswith(("http://", "https://"))
        if executable is True:
            if not entry:
                _err(result, "learning_action.entry 不能为空：提供了可执行动作就必须给出本次核验过的入口")
            elif not is_link:
                _err(result, "learning_action.entry 必须是本次核验过的 http(s) 入口")
            if not action.get("done_criteria"):
                _err(result, "learning_action.done_criteria 不能为空：提供了可执行动作时必须给出完成标准")
        # executable=false（或未显式给出，已由上一条报错覆盖）时：完成标准可以留空，
        # 也可以保留 README/文档链接与缺口说明，不要求入口是可执行动作。
        if is_link:
            in_evidence = any(item.url == entry for item in evidence)
            if executable is True and not in_evidence and require_verified_entry:
                _err(result, "学习入口不在本次实际读取的证据里：不能用猜出来的链接当作已核实的操作入口")
            elif not in_evidence:
                _warn(result, "learning_action.entry 不在本次证据清单里，请人工确认该入口可访问")
        if isinstance(verified_entry, dict):
            _check_verified_entry(result, action, verified_entry)

    verification = report.get("verification")
    if not isinstance(verification, dict):
        _err(result, "verification 缺失或不是对象（该字段应由运行记录生成）")
    else:
        if verification.get("actually_ran"):
            _err(result, "增量 A 不运行下载的项目，verification.actually_ran 不能为 true")
        for key in ("docs_reviewed", "source_reviewed", "actually_ran", "note"):
            if key not in verification:
                _err(result, f"verification.{key} 缺失")
        if expected_verification is not None:
            for key, value in expected_verification.items():
                if verification.get(key) != value:
                    _err(
                        result,
                        f"verification.{key}={verification.get(key)!r} 与运行记录 {value!r} 不一致，"
                        "查阅状态必须由运行记录生成",
                    )

    recommendation = report["recommendation"]
    if not isinstance(recommendation, dict):
        _err(result, "recommendation 必须是对象")
    else:
        decision = str(recommendation.get("decision", ""))
        if decision not in DECISIONS:
            _err(result, f"recommendation.decision={decision!r} 不在 {sorted(DECISIONS)} 内")
        if not recommendation.get("reason"):
            _err(result, "recommendation.reason 不能为空")
        if decision == "recommend":
            if not recommendation.get("evidence_gaps"):
                _err(result, "推荐时必须写明 evidence_gaps（可写“未发现关键缺口”）")
            if not recommendation.get("uncertainties"):
                _err(result, "推荐时必须写明 uncertainties")

    value = report["value_for_user"]
    if not isinstance(value, dict):
        _err(result, "value_for_user 必须是对象")
    else:
        for key in ("learn", "demo", "artifact"):
            if not value.get(key):
                _err(result, f"value_for_user.{key} 不能为空")

    highlight = report["highlight"]
    _check_highlight(result, highlight, known, require_quotes=require_quotes)

    starting = report["starting_conditions"]
    if not isinstance(starting, dict):
        _err(result, "starting_conditions 必须是对象")
    else:
        for key in ("have", "gaps"):
            values = starting.get(key)
            if not isinstance(values, list) or not values:
                _err(result, f"starting_conditions.{key} 必须是非空数组")

    experiment = report["mini_experiment"]
    if not isinstance(experiment, dict):
        _err(result, "mini_experiment 必须是对象")
    else:
        for key in ("suggested_change", "observable", "boundary"):
            if not experiment.get(key):
                _err(result, f"mini_experiment.{key} 不能为空")
        if experiment.get("evidence_ids"):
            _err(result, "mini_experiment 是建议改动，不能带 evidence_ids（不得写成已验证能力）")

    explore = report["explore_further"]
    if not isinstance(explore, dict):
        _err(result, "explore_further 必须是对象")
    else:
        for key in ("repo", "example_entry"):
            link = str(explore.get(key) or "")
            if link.startswith(("http://", "https://")):
                continue
            if (
                key == "example_entry"
                and not _has_example_entry(verified_entry)
                and _ENTRY_UNAVAILABLE_PATTERN.search(link)
            ):
                continue
            _err(result, f"explore_further.{key} 必须是 http(s) 链接（未核验到时写“未核验到”）")
        if not explore.get("known_limits"):
            _err(result, "explore_further.known_limits 不能为空（写来自证据的已知限制）")

    _check_unavailable_credentials(result, report)

    text_blob = _collect_text(report)
    for pattern in _SCORE_PATTERNS:
        if pattern.search(text_blob):
            _err(result, f"禁止装饰性质量评分：命中 {pattern.pattern!r}")
    if _DURATION_PATTERN.search(text_blob):
        _err(result, "禁止输出没有根据的精确学习时长")
    _check_global_numbers(result, text_blob, evidence)

    result.ok = not result.errors
    return result


def _resolve_repo(name: str, allowed: set[str]) -> str | None:
    if name in allowed:
        return name
    matches = [repo for repo in allowed if repo.split("/")[-1] == name]
    if len(matches) == 1:
        return matches[0]
    return None


def _check_ids(
    result: ValidationResult,
    known: dict[str, Evidence],
    ids: Any,
    label: str,
    *,
    required: bool,
) -> list[str]:
    if ids is None:
        if required:
            _err(result, f"{label} 缺失：每条结论都要能追溯到证据编号")
        return []
    if not isinstance(ids, list) or not ids:
        if required:
            _err(result, f"{label} 必须是非空数组")
        return []
    checked: list[str] = []
    for item in ids:
        text = str(item)
        if text not in known:
            _err(result, f"{label} 引用了不存在的证据编号 {item!r}")
            continue
        checked.append(text)
    return checked


def _has_example_entry(verified_entry: dict[str, Any] | None) -> bool:
    """本次是否取到"已读取且检测到示例信号"的入口；没有的话允许写“暂不可执行”。"""
    if not isinstance(verified_entry, dict):
        return False
    return bool(verified_entry.get("verified") and verified_entry.get("example_signal"))

def _check_highlight(
    result: ValidationResult,
    highlight: Any,
    known: dict[str, Evidence],
    *,
    require_quotes: bool,
) -> None:
    """亮点必须写清解决什么问题、为什么值得关注，并给证据。"""
    if not isinstance(highlight, dict):
        _err(result, "highlight 必须是对象")
        return
    items = highlight.get("items")
    if not isinstance(items, list) or not items:
        _err(result, "highlight.items 必须是非空数组（1–2 个亮点）")
        return
    if len(items) > 2:
        _err(result, "highlight.items 最多 2 个亮点")
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            _err(result, f"highlight.items[{index}] 必须是对象")
            continue
        for key in ("plain", "scenario", "how"):
            if not item.get(key):
                _err(result, f"highlight.items[{index}].{key} 不能为空")
        ids = _check_ids(
            result,
            known,
            item.get("evidence_ids"),
            f"highlight.items[{index}].evidence_ids",
            required=True,
        )
        if require_quotes:
            _check_quote(
                result, f"highlight.items[{index}]", item.get("quote"), ids, known, text=str(item.get("plain") or "")
            )


def _check_unavailable_credentials(result: ValidationResult, report: dict[str, Any]) -> None:
    """不得把用户没有的提供方密钥当作已满足：提到别的提供方时必须写明缺口。"""
    # 只检查"声称已具备"的字段：gaps 与 suggested_change 本来就是在讲缺口与改动，提到别的提供方不算违规
    starting = report.get("starting_conditions") or {}
    containers = {
        "learning_action": report.get("learning_action"),
        "mini_experiment.observable": (report.get("mini_experiment") or {}).get("observable")
        if isinstance(report.get("mini_experiment"), dict)
        else None,
        "starting_conditions.have": starting.get("have"),
    }
    for label, container in containers.items():
        if container is None:
            continue
        text = _text_of(container)
        if not OTHER_PROVIDER_PATTERN.search(text):
            continue
        # 显式不可执行动作可以用“需要 X 密钥，而用户只有 Y API”说明缺口。
        # 不只因 executable=false 放行；仍需同时给出需求和另一通道的资源限制。
        unavailable_action = False
        if label == "learning_action" and isinstance(container, dict) and container.get("executable") is False:
            explanation = str(container.get("action") or "")
            only = _ONLY_USER_API.search(explanation)
            unavailable_action = bool(
                _REQUIRED_PROVIDER_SECRET.search(explanation) and only
                and not OTHER_PROVIDER_PATTERN.search(only.group(1))
            )
        if not GAP_MARKER_PATTERN.search(text) and not unavailable_action:
            _err(
                result,
                f"{label} 里提到了用户没有的提供方凭据，却没有写明缺口："
                "不得把未具备的密钥当成已满足（档案只有已配置的通道）",
            )

def _check_conditions(
    result: ValidationResult,
    difficulty: dict[str, Any],
    known: dict[str, Evidence],
    owners: dict[str, str],
    allowed: set[str] | None,
) -> None:
    """关键条件逐条核对：evidence 要带可匹配引文，unknown 不许有证据，inference 要有依据。"""
    conditions = difficulty.get("conditions")
    if not isinstance(conditions, list) or not conditions:
        _err(result, "difficulty.conditions 必须是数组：运行环境等关键条件要逐条标明依据")
        return
    seen: set[str] = set()
    for index, item in enumerate(conditions, start=1):
        if not isinstance(item, dict):
            _err(result, f"difficulty.conditions[{index}] 必须是对象")
            continue
        key = str(item.get("condition", "")).strip()
        if not key:
            _err(result, f"difficulty.conditions[{index}].condition 不能为空")
            continue
        if key in seen:
            _err(result, f"关键条件重复：{key}")
        seen.add(key)
        status = str(item.get("status", ""))
        if status not in CONDITION_STATUS:
            _err(result, f"conditions[{key}].status={status!r} 必须是 {sorted(CONDITION_STATUS)} 之一")
        value = item.get("value")
        if not isinstance(value, str) or not value.strip():
            _err(result, f"conditions[{key}].value 不能为空")
        ids = _check_ids(
            result,
            known,
            item.get("evidence_ids"),
            f"conditions[{key}].evidence_ids",
            required=(status == "evidence"),
        )
        label = f"conditions[{key}]"
        if status == "evidence":
            _check_owner_presence(result, ids, owners, allowed, label=label)
            _check_quote(result, label, item.get("quote"), ids, known, text=str(item.get("value") or ""))
        elif status == "unknown":
            if ids:
                _err(result, f"{label} 标为未知却声称有证据：未知必须留空 evidence_ids")
            if isinstance(value, str) and not _UNKNOWN_WORDS.search(value):
                _err(result, f"{label} 的判断未知时，value 要写明“未知/未找到”，不要写成确定结论")
        elif status == "not_applicable":
            if ids:
                _err(result, f"{label} 标为不适用就不该引用证据")
            if isinstance(value, str) and not _NOT_APPLICABLE_WORDS.search(value):
                _err(result, f"{label} 标为 not_applicable 时，value 要写明“不适用”及原因")
        else:
            if not item.get("basis"):
                _err(result, f"{label} 属于模型推断，必须写明 basis（推断依据）")
    for required_key in CONDITION_KEYS:
        if required_key not in seen:
            _err(result, f"缺少关键条件 {required_key}：有证据就引用，找不到就写未知")


def _check_verified_entry(
    result: ValidationResult,
    action: dict[str, Any],
    verified_entry: dict[str, Any],
) -> None:
    """运行记录核验过入口时：声称可执行就必须用那个入口，不能换成别的链接。"""
    url = str(verified_entry.get("url") or "")
    if not url:
        return
    entry = str(action.get("entry", ""))
    if entry == url:
        if action.get("executable") is True and not verified_entry.get("example_signal"):
            _warn(
                result,
                "运行记录里这个入口没有检测到示例信号，报告却把动作标为可执行：请人工确认步骤是否真的存在",
            )
        return
    if action.get("executable") is True:
        _err(
            result,
            f"learning_action.entry 必须是本次核验过的入口 {url!r}，实际写的是 {entry!r}",
        )
    elif _has_example_entry(verified_entry):
        _warn(
            result,
            "本次核验到带示例信号的入口，但报告判定没有可执行动作：请人工确认这是采集问题还是判断问题",
        )

def _check_owner_presence(
    result: ValidationResult,
    ids: list[str],
    owners: dict[str, str],
    allowed: set[str] | None,
    *,
    label: str,
) -> None:
    if allowed is None or not ids:
        return
    present = [owners.get(evidence_id, "") for evidence_id in ids]
    known_owners = {owner for owner in present if owner}
    if not known_owners:
        _warn(result, f"{label} 只引用了没有仓库归属的证据（如网页），人工复核时需要确认归属")
        return
    outside = {owner for owner in known_owners if owner not in allowed}
    if outside:
        _err(result, f"{label} 引用了本次没有采集的项目证据：{sorted(outside)}")


_VERSION_TOKEN = re.compile(r"\d+\.\d+(?:\.\d+)?")
_DATE_TOKEN = re.compile(r"\d{4}-\d{2}-\d{2}")
_STAR_TOKEN = re.compile(r"(\d[\d,]*)\s*(?:stars?|星)", re.I)


def _check_metadata_values(
    result: ValidationResult,
    label: str,
    text: str,
    cited: list[Evidence],
) -> None:
    """免逐字引文的条件：结论里的元数据值要能在所属项目的结构化证据里找到。"""
    haystack = " ".join(
        [item.excerpt or "" for item in cited]
        + [json.dumps(item.extra or {}, ensure_ascii=False) for item in cited]
    )
    for token in sorted(set(_VERSION_TOKEN.findall(text or "")) | set(_DATE_TOKEN.findall(text or ""))):
        if token not in haystack:
            _err(result, f"{label} 里的元数据值 {token} 在所引项目的结构化证据里找不到")
    for match in _STAR_TOKEN.finditer(text or ""):
        value = match.group(1).replace(",", "")
        if value not in haystack:
            _err(result, f"{label} 里的星数 {value} 在所引项目的结构化证据里找不到")


_METADATA_FIELDS = (
    ("full_name", "仓库"),
    ("language", "语言"),
    ("license", "许可证"),
    ("stars", "星数"),
    ("forks", "Fork"),
    ("open_issues", "问题数"),
    ("created_at", "创建于"),
    ("pushed_at", "最后推送"),
    ("updated_at", "更新时间"),
    ("archived", "归档"),
    ("default_branch", "默认分支"),
    ("topics", "主题"),
    ("tag_name", "发布标签"),
    ("published_at", "发布时间"),
)
_CONNECTIVE_CHARS = set(
    "的是为于和与及以在使用其该本这有个最新版本发布创建仓库项目许可证星语言归档支持无不着了将可所等获得兼容"
)
_COVER_TOKEN = re.compile(r"[0-9A-Za-z][0-9A-Za-z.\-]*")
_CJK_CHAR = re.compile(r"[\u4e00-\u9fff]")


def _structured_fields_text(cited: list[Evidence]) -> str:
    """把结构化字段渲染成可比较文本（程序渲染，不要求模型抄写）。"""
    chunks: list[str] = []
    for item in cited:
        extra = item.extra or {}
        for key, label in _METADATA_FIELDS:
            value = extra.get(key)
            if value in (None, "", []):
                continue
            if isinstance(value, list):
                value = "、".join(str(entry) for entry in value)
            chunks.append(f"{label}={value}")
        chunks.append(str(item.title or ""))
    return "；".join(chunks)


def _covered_by_structured_fields(text: str, cited: list[Evidence]) -> bool:
    """结论是否完全由该项目的结构化字段覆盖；不覆盖就必须给连续原文引文。"""
    subject = text or ""
    if not subject.strip():
        return False
    # 此类显式元数据句式按字段绑定值，不用“显/示/近”等字是否出现在证据里来判定。
    # 整句必须解析完；失败不能退回字符覆盖，否则功能判断或字段值互换可能被误放行。
    prefix = "仓库元数据显示"
    if subject.startswith(prefix):
        labels = {label: key for key, label in _METADATA_FIELDS}
        labels["最近推送"] = "pushed_at"
        pattern = "|".join(re.escape(label) for label in sorted(labels, key=len, reverse=True))
        clauses = re.split(r"[，,；;]", subject[len(prefix):].rstrip("。"))
        fields: list[tuple[str, str]] = []
        for clause in clauses:
            match = re.fullmatch(rf"({pattern})(?:为|是|在|=)\s*(.+)", clause.strip())
            if not match:
                return False
            fields.append((labels[match[1]], match[2].strip()))
        for item in cited:
            if item.kind not in {"github_repo", "github_release", "github_commit"}:
                continue
            for key, value in fields:
                raw = (item.extra or {}).get(key)
                if raw is None or raw == "" or raw == []:
                    break
                expected = "、".join(str(entry) for entry in raw) if isinstance(raw, list) else str(raw)
                if value == expected:
                    continue
                if key.endswith("_at") and _DATE_TOKEN.fullmatch(value) and expected.startswith(value + "T"):
                    continue
                break
            else:
                return True
        return False
    fields_text = _structured_fields_text(cited)
    allowed_chars = set(fields_text)
    for token in _COVER_TOKEN.findall(subject):
        if token.lower() not in fields_text.lower():
            return False
    for char in _CJK_CHAR.findall(subject):
        if char in _CONNECTIVE_CHARS or char in allowed_chars:
            continue
        return False
    return True

def _check_quote(
    result: ValidationResult,
    label: str,
    quote: Any,
    ids: list[str],
    known: dict[str, Evidence],
    *,
    text: str = "",
) -> None:
    cited = [known[evidence_id] for evidence_id in ids if evidence_id in known]
    # 元数据类证据不要求逐字抄写，但结论里出现的版本/日期/星数必须能在该项目自己的结构化字段里找到
    if cited and all(item.kind in {"github_repo", "github_release", "github_commit"} for item in cited):
        subject = text or (quote if isinstance(quote, str) else "")
        if _covered_by_structured_fields(subject, cited):
            return
    if not isinstance(quote, str) or not quote.strip():
        _err(result, f"{label}.quote 缺失：事实与项目方宣称必须给出能在证据里匹配到的短引文")
        return
    matched, detail = quote_supported_by(quote, cited)
    if not matched:
        _err(result, f"{label}.quote 无法在所引证据中匹配：{detail}")


def _check_numbers(
    result: ValidationResult,
    label: str,
    text: str,
    ids: list[str],
    known: dict[str, Evidence],
) -> None:
    numbers = {int(match.group(1).replace(",", "")) for match in _NUMBER_PATTERN.finditer(text or "")}
    if not numbers:
        return
    cited = [known[evidence_id] for evidence_id in ids if evidence_id in known]
    if not cited:
        return
    available: set[int] = set()
    for item in cited:
        available |= item.numbers()
    for value in sorted(numbers):
        if value not in available:
            _err(
                result,
                f"{label} 里的数值 {value} 在所引证据里找不到（可能张冠李戴或编造）",
            )


def _check_global_numbers(result: ValidationResult, text: str, evidence: list[Evidence]) -> None:
    available: set[int] = set()
    for item in evidence:
        available |= item.numbers()
    for match in _NUMBER_PATTERN.finditer(text):
        value = int(match.group(1).replace(",", ""))
        if value not in available:
            _err(result, f"报告里的星数 {value} 与本次证据不一致（可能编造热度）")


def _text_of(node: Any) -> str:
    parts: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, dict):
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(node)
    return "\n".join(parts)


def _collect_text(report: dict[str, Any]) -> str:
    # Optional archive suggestions have a separate adoption check; the report
    # body's existing factual quality gate remains unchanged.
    return _text_of({key:value for key,value in report.items() if key != 'archive_tags'})
