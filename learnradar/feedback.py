"""Single source for feedback values, display text, and prompt blocks; see docs/FEEDBACK.md.

Feedback is verbatim user speech, not evidence or model judgment.
Bind it to a project/run; repeated submissions update the same report record.
Disinterest does not create exclusion or preference rules; comprehension does not mean endorsement.
Do not infer cross-project preferences or add a feedback model."""

from __future__ import annotations

from typing import Any, Iterable

ITEMS: tuple[tuple[str, str], ...] = (
    ("understood_what", "是否读懂项目做什么"),
    ("understood_highlight", "是否看懂至少一个具体亮点"),
    ("want_more", "是否想进一步了解"),
)
CHOICES: tuple[tuple[str, str], ...] = (
    ("yes", "是"),
    ("unsure", "不确定"),
    ("no", "否"),
)
BLANK_LABEL = "未填写"
MAX_NOTE_CHARS = 2000
EMPTY_TEXT = "本次没有相关项目的既有反馈。"

PROMPT_RULES = (
    "这些是用户本人填写的原话，不是证据，也不是模型判断：\n"
    "- 不得把它们当作 `evidence_ids` 或事实依据，也不要据此改写任何结论的 type；\n"
    "- “不想进一步了解”只描述这一次的意愿，不构成排除名单、语言偏好或长期兴趣规则；\n"
    "- “读懂了项目做什么 / 看懂了亮点”只表示叙述可理解，不代表用户认可项目，也不代表内容已核验；\n"
    "- 备注是用户原话，可以引用；不要替用户解释原因；\n"
    "- 不得跨项目推断偏好（例如由某个项目推出用户喜欢或讨厌某一类框架）。"
)


def clean_answers(raw: dict[str, Any]) -> dict[str, str]:
    """Accept known options only; absent/invalid choices remain empty, preserving the unanswered state."""
    allowed = {code for code, _ in CHOICES}
    answers: dict[str, str] = {}
    for key, _label in ITEMS:
        value = str(raw.get(key, "") or "").strip().lower()
        answers[key] = value if value in allowed else ""
    return answers


def clean_note(raw: Any) -> str:
    return str(raw or "").strip()[:MAX_NOTE_CHARS]


def label(code: str) -> str:
    for value, text in CHOICES:
        if value == code:
            return text
    return BLANK_LABEL


def summary_line(row: dict[str, Any]) -> str:
    return "｜".join(f"{text}：{label(str(row.get(key) or ''))}" for key, text in ITEMS)


def prompt_text_for_repos(store: Any, repos: Iterable[str]) -> str:
    """Build a verbatim feedback prompt block from the latest feedback for each relevant project."""
    rows = store.feedback_for_repos(list(repos))
    if not rows:
        return EMPTY_TEXT
    lines = [PROMPT_RULES, "", "以下是用户原话（按项目列出，含填写时间与所属运行）："]
    for row in rows:
        note = str(row.get("note") or "").strip() or BLANK_LABEL
        lines.append(
            f"- {row.get('repo')}：{summary_line(row)}｜备注：{note}"
            f"｜填写时间 {row.get('updated_at')}｜所属运行 {row.get('run_id')}"
        )
    lines.append("")
    lines.append("如果本次的目标项目不在上面，就写“本次没有该项目的既有反馈”，不要推测。")
    return "\n".join(lines)


def report_rows(store: Any, repos: Iterable[str]) -> list[dict[str, Any]]:
    """Build report appendix lines from the same source as the prompt to avoid inconsistent wording."""
    return store.feedback_for_repos(list(repos))
