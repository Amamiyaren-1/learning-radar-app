"""分析阶段：把证据与档案组装成提示词，调用配置好的分析角色模型，解析结构化输出。

失败分类（用于决定是否值得再花一次请求）：
- `empty_output`：正文为空（常见于推理把输出预算吃光）。**不重发同一请求**，
  而是改用"压缩输出策略"再试一次；再失败就停下并记录原因。
- `truncated`：`finish_reason` 表明被输出上限截断，处理方式同上。
- `invalid_json`：有正文但 JSON 不合法，用一次修复调用（提示具体错误）。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from .config import Settings
from .evidence import Evidence
from .feedback import EMPTY_TEXT as NO_FEEDBACK_TEXT
from .models import ModelAdapter, ModelError, ModelResponse

PROMPT_VERSION = "analysis-12"

SYSTEM_PROMPT = (
    "你是一名严格的技术分析助手，为一位置身中文环境的个人学习者做项目对比。"
    "你只能使用用户消息中给出的证据编号来支撑结论，禁止补充记忆中的事实、禁止编造版本号、"
    "Star 数、性能数字或发布日期。证据不足时，必须明确写出缺口，并把结论降级为 watch。"
    "只输出一个完整的 JSON 对象，不要输出解释性文字或 Markdown 代码块。"
    "解释性字段（亮点解释、对我的价值）可以写成完整段落，以讲清楚为准；"
    "引用证据时必须原样摘录连续片段，不要改写，也不要为了简短牺牲解释。"
)

COMPRESS_INSTRUCTION = (
    "\n\n# 输出策略调整（上一次的失败原因见下）\n"
    "上一次没有产出可用正文（空正文或超过输出上限被截断）。"
    "这次请把回答压到最短，优先级如下：\n"
    "1) 仍然必须为每条 `fact` 与 `vendor_claim` 给 `quote`（从证据里原样抄最短的一段）；\n"
    "2) `claims` 只保留最重要的 3 条，每条不超过 35 字；\n"
    "3) `comparisons` 只写必要差异，每个字段不超过 40 字；\n"
    "4) `highlight`、`novelty`、`value_for_user`、`recommendation.reason` 各不超过 40 字；\n"
    "5) `difficulty.conditions` 仍要给全四条，但 `value` 不超过 30 字，"
    "`status=\"evidence\"` 仍要带最短 `quote`；\n"
    "6) 整个 JSON 控制在 1200 字以内，不要输出任何解释或代码块标记。\n"
)


class AnalysisError(RuntimeError):
    """分析阶段的失败。kind 取值：empty_output / truncated / invalid_json / model_error。"""

    def __init__(self, message: str, *, kind: str = "invalid_json", attempts: int = 1) -> None:
        super().__init__(message)
        self.kind = kind
        self.attempts = attempts


@dataclass
class AnalysisOutcome:
    report: dict[str, Any]
    responses: list[ModelResponse]
    attempts: int
    repaired: bool = False
    retry_kind: str = ""

    @property
    def fixture(self) -> bool:
        return any(item.fixture for item in self.responses)


def load_prompt_template(settings: Settings) -> str:
    path = settings.prompts_dir / "analysis.md"
    if not path.exists():
        raise AnalysisError(f"缺少提示词模板：{path}")
    return path.read_text(encoding="utf-8")


def _learning_entry_block(target: dict[str, Any]) -> str:
    """把本次实际读取到的学习入口写进提示词；没读到示例信号就必须标为不可执行。"""
    entry = target.get("learning_entry") if isinstance(target, dict) else None
    if not isinstance(entry, dict) or not entry.get("url"):
        return '本次没有取到可核验的学习入口：executable=false，entry=""；缺口只写在action等文字字段，done_criteria允许为空。'
    url = str(entry["url"])
    if entry.get("verified") and entry.get("example_signal"):
        signals = "、".join(entry.get("signals") or []) or "检测到代码块与安装/运行片段"
        return (
            f"{url}（本次已实际读取，检测到示例信号：{signals}；本次未实际运行示例；"
            f"evidence_id={entry.get('evidence_id')}）"
        )
    if entry.get("verified"):
        files = entry.get("example_files") or []
        file_text = ""
        if files:
            names = "、".join(str(item.get("path")) for item in files)
            file_text = f"已读到示例文件：{names}（仅有代码，未核验到启动步骤）；"
        return (
            f"{url}（本次已实际读取，但没有检测到示例信号：{file_text}"
            f"{entry.get('error') or '本次未核验到可执行路径'}）："
            'learning_action.executable=false，entry仅保留上述URL或留空；action说明“本次未核验到可执行路径”，done_criteria允许为空。不得写成项目没有示例或教程质量问题。'
        )
    return (
        f"{url}（读取失败：{entry.get('error')}）：入口未被核验，"
        'learning_action.executable=false，entry留空；缺口写在action，done_criteria允许为空。'
    )


def build_user_prompt(
    settings: Settings,
    *,
    profile: dict[str, Any],
    profile_confirmed: bool,
    target: dict[str, Any],
    comparators: list[dict[str, Any]],
    evidence: list[Evidence],
    feedback_text: str = "",
) -> str:
    template = load_prompt_template(settings)
    evidence_block = "\n\n".join(item.prompt_block() for item in evidence)
    replacements = {
        "{{PROFILE}}": json.dumps(profile, ensure_ascii=False, indent=2),
        "{{PROFILE_STATUS}}": "用户已确认" if profile_confirmed else "示例占位档案，尚未由用户确认",
        "{{TARGET}}": json.dumps(target, ensure_ascii=False, indent=2),
        "{{COMPARATORS}}": json.dumps(comparators, ensure_ascii=False, indent=2),
        "{{EVIDENCE}}": evidence_block,
        "{{LIMITS}}": json.dumps(
            {
                "min_comparators": settings.limit("min_comparators", 2),
                "max_sources_per_candidate": settings.limit("max_sources_per_candidate", 8),
            },
            ensure_ascii=False,
        ),
        "{{LEARNING_ENTRY}}": _learning_entry_block(target),
        "{{SCREENING_NOTE}}": str(
            (target.get("screening_note") if isinstance(target, dict) else "") or "本次没有初筛结论。"
        ),
        "{{FEEDBACK}}": feedback_text.strip() or NO_FEEDBACK_TEXT,
    }
    prompt = template
    for key, value in replacements.items():
        prompt = prompt.replace(key, value)
    return prompt


def parse_json_response(text: str) -> dict[str, Any]:
    cleaned = (text or "").strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", cleaned, flags=re.S)
    if fence:
        cleaned = fence.group(1).strip()
    if not cleaned.startswith("{"):
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise AnalysisError(f"模型输出里找不到完整的 JSON 对象：{text[:200]!r}")
        cleaned = cleaned[start : end + 1]
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise AnalysisError(f"模型输出不是合法 JSON：{exc}；片段：{cleaned[:200]!r}") from exc
    if not isinstance(parsed, dict):
        raise AnalysisError("模型输出的 JSON 顶层必须是对象")
    return parsed


def classify_failure(response: ModelResponse) -> str | None:
    """空正文 / 被截断先判定；返回 None 表示正文存在，可以尝试解析。"""
    if not (response.text or "").strip():
        return "empty_output"
    if getattr(response, "truncated", False):
        return "truncated"
    return None


def run_analysis(
    adapter: ModelAdapter,
    settings: Settings,
    *,
    profile: dict[str, Any],
    profile_confirmed: bool,
    target: dict[str, Any],
    comparators: list[dict[str, Any]],
    evidence: list[Evidence],
    max_calls: int,
    sink: list[ModelResponse] | None = None,
    feedback_text: str = "",
) -> AnalysisOutcome:
    """最多调用 max_calls 次；按失败类型选择"换策略"或"修 JSON"，不做无意义重发。

    sink 用于记录每次真实调用（即使随后失败，调用与消耗也不会丢失）。
    """
    user_prompt = build_user_prompt(
        settings,
        profile=profile,
        profile_confirmed=profile_confirmed,
        target=target,
        comparators=comparators,
        evidence=evidence,
        feedback_text=feedback_text,
    )
    responses: list[ModelResponse] = []
    if max_calls < 1:
        raise AnalysisError("max_calls 必须至少为 1", kind="invalid_json", attempts=0)

    response = adapter.complete(system=SYSTEM_PROMPT, user=user_prompt)
    responses.append(response)
    if sink is not None:
        sink.append(response)

    kind = classify_failure(response)
    first_error: AnalysisError | None = None
    if kind is None:
        try:
            return AnalysisOutcome(report=parse_json_response(response.text), responses=responses, attempts=1)
        except AnalysisError as exc:
            kind = "invalid_json"
            first_error = exc
    else:
        detail = "模型正文为空" if kind == "empty_output" else "模型输出被输出上限截断"
        first_error = AnalysisError(
            f"{detail}（finish_reason={getattr(response, 'finish_reason', '') or '未提供'}）",
            kind=kind,
        )

    if max_calls < 2:
        first_error.attempts = 1
        raise first_error

    if kind in ("empty_output", "truncated"):
        # 不重发同一请求：换成压缩输出策略，并说明上一次的失败类型
        retry_prompt = user_prompt + COMPRESS_INSTRUCTION + f"\n上一次的失败类型：{kind}（{first_error}）\n"
    else:
        retry_prompt = (
            user_prompt
            + "\n\n# 上一次输出的问题\n"
            + f"你的上一次输出无法解析：{first_error}\n"
            + "请重新输出一个**完整且合法**的 JSON 对象，字段齐全、字符串闭合、不要任何解释或代码块标记，"
            + "并在此之前压缩内容：claims 最多 5 条、每条不超过 50 字。"
        )
    try:
        second = adapter.complete(system=SYSTEM_PROMPT, user=retry_prompt)
    except ModelError as exc:
        first_error.attempts = 1
        raise AnalysisError(f"修复调用失败：{exc}", kind=kind, attempts=1) from exc
    responses.append(second)
    if sink is not None:
        sink.append(second)

    second_kind = classify_failure(second)
    if second_kind is not None:
        detail = "第二次仍然是空正文" if second_kind == "empty_output" else "第二次仍被截断"
        raise AnalysisError(
            f"{detail}（kind={second_kind}）；已停止，不再消耗预算",
            kind=second_kind,
            attempts=2,
        )
    try:
        report = parse_json_response(second.text)
    except AnalysisError as second_error:
        raise AnalysisError(str(second_error), kind="invalid_json", attempts=2) from second_error
    return AnalysisOutcome(report=report, responses=responses, attempts=2, repaired=True, retry_kind=kind)
