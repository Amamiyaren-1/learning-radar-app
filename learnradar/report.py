"""Render Markdown from validated structured output; models do not write report files directly."""

from __future__ import annotations

import json
from typing import Any

from .evidence import Evidence
from .feedback import ITEMS as FEEDBACK_ITEMS
from .feedback import label as feedback_label
from .validate import ValidationResult

CLAIM_LABELS = {
    "fact": "事实证据",
    "vendor_claim": "项目方宣称",
    "inference": "模型推断",
}
DECISION_LABELS = {
    "recommend": "推荐",
    "watch": "待观察",
    "not_recommend": "不推荐",
}
LEVEL_LABELS = {
    "run_example": "跑通示例",
    "understand_principles": "理解原理",
    "modify_extend": "修改扩展",
}
CONTENT_REVIEW_LABEL = "未完成（需要人工核对摘录是否支持结论）"
def _sources(ids: Any, evidence_map: dict[str, Evidence]) -> str:
    """Replace evidence IDs with readable source titles/links; keep IDs in appendix A only."""
    links: list[str] = []
    for evidence_id in ids or []:
        item = evidence_map.get(str(evidence_id))
        if item is None:
            continue
        label = item.title or item.url
        link = f"[{label}]({item.url})"
        if link not in links:
            links.append(link)
    return "、".join(links) if links else "（无）"


CONDITION_LABELS = {
    "runtime": "运行时及版本",
    "os_support": "系统支持",
    "gpu": "GPU 要求",
    "external_cost": "外部费用",
}
CONDITION_STATUS_LABELS = {
    "evidence": "有证据",
    "unknown": "未知（未找到依据）",
    "inference": "模型推断",
    "not_applicable": "不适用",
}
KIND_LABELS = {
    "first_release": "首次发布",
    "major_version": "重要版本",
    "technical_change": "实质技术变化",
    "unclear": "证据不足，无法判定",
}


def _ids(items: Any) -> str:
    if not items:
        return "（无）"
    return "、".join(f"`{item}`" for item in items)


def render_markdown(
    *,
    report: dict[str, Any],
    evidence: list[Evidence],
    meta: dict[str, Any],
    validation: ValidationResult,
    stage_usage: list[dict[str, Any]] | None = None,
    feedback_rows: list[dict[str, Any]] | None = None,
    include_run_meta: bool = True,
) -> str:
    target = report.get("target", {})
    novelty = report.get("novelty", {})
    difficulty = report.get("difficulty", {})
    action = report.get("learning_action", {})
    verification = report.get("verification", {})
    recommendation = report.get("recommendation", {})
    fit = report.get("value_for_user", {})

    lines: list[str] = []
    lines.append(f"# {report.get('title', '未命名报告')}")
    lines.append("")
    lines.append(f"> 目标项目：`{target.get('repo', '?')}`　版本：{target.get('version', '未标注')}")
    lines.append(f"> 分类：{' / '.join(target.get('category') or []) or '未分类'}")
    lines.append("")
    lines.append(f"**一句话：** {target.get('summary_one_line', '（缺失）')}")
    lines.append("")

    lines.append("")

    evidence_map = {item.evidence_id: item for item in evidence}
    highlight = report.get("highlight", {}) or {}
    lines.append("## 1. 项目亮点与对照")
    lines.append("")
    for index, item in enumerate(highlight.get("items") or [], start=1):
        if not isinstance(item, dict):
            continue
        lines.append(f"### 亮点 {index}：{item.get('name', '（未命名）')}")
        lines.append("")
        lines.append(f"- 它是什么：{item.get('plain', '（缺失）')}")
        lines.append(f"- 没有它会怎样（场景）：{item.get('scenario', '（缺失）')}")
        lines.append(f"- 项目怎么做（含尚未核验的部分）：{item.get('how', '（缺失）')}")
        lines.append(f"- 与相关方案的差异：{item.get('difference', '（未写）')}")
        lines.append(f"- 对你的价值：{item.get('value', '（未写）')}")
        lines.append(f"- 依据：{basis_text(item, evidence_map)}")
        lines.append("")
    lines.append(f"- 新颖之处（区分首次发布 / 重要版本 / 实质技术变化）：{novelty.get('what', '（缺失）')}")
    lines.append(
        f"  时间：{novelty.get('when', '不确定')}　"
        f"类型：{KIND_LABELS.get(str(novelty.get('kind')), str(novelty.get('kind', '未标注')))}　"
        f"依据：{_sources(novelty.get('evidence_ids'), evidence_map)}"
    )
    lines.append("")
    lines.append("**关键结论（事实 / 项目方宣称 / 推断）**")
    lines.append("")
    lines.append("")
    lines.append("| 类别 | 结论 | 来源 |")
    lines.append("| --- | --- | --- |")
    for claim in report.get("claims", []):
        label = CLAIM_LABELS.get(str(claim.get("type")), str(claim.get("type")))
        text = str(claim.get("text", "")).replace("|", "\\|")
        lines.append(f"| {label} | {text} | {_sources(claim.get('evidence_ids'), evidence_map)} |")
    lines.append("")
    lines.append("**每条结论的依据摘录与定位**（结构检查只保证引文能匹配，是否真的支持结论需人工复核）")
    lines.append("")
    for index, claim in enumerate(report.get("claims", []), start=1):
        lines.append(f"- 结论 {index}：{basis_text(claim, evidence_map)}")
    lines.append("")

    lines.append("**与相关项目的对比（含为什么选这些对照）**")
    lines.append("")
    lines.append("| 对照项目 | 为什么选它 | 解决的问题 | 关键差异 | 适用条件 | 限制 | 来源 |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- |")
    for item in report.get("comparisons", []):
        row = [
            str(item.get("comparator", "")),
            str(item.get("why_this_comparator", "")),
            str(item.get("problem", "")),
            str(item.get("key_difference", "")),
            str(item.get("fit_conditions", "")),
            str(item.get("limitations", "")),
            _sources(item.get("evidence_ids"), evidence_map),
        ]
        lines.append("| " + " | ".join(cell.replace("|", "\\|").replace("\n", " ") for cell in row) + " |")
    lines.append("")

    lines.append("## 2. 对我的价值")
    lines.append("")
    lines.append(f"- 能学到什么：{fit.get('learn', '（缺失）')}")
    lines.append(f"- 可能的演示：{fit.get('demo', '（缺失）')}")
    lines.append(f"- 可能的个人成果：{fit.get('artifact', '（缺失）')}")
    lines.append("")

    starting = report.get("starting_conditions", {}) or {}
    lines.append("## 3. 开始条件")
    lines.append("")
    lines.append(f"- 难度：**{LEVEL_LABELS.get(str(difficulty.get('level')), str(difficulty.get('level')))}**（{difficulty.get('basis', '（缺失）')}）")
    lines.append("- 已具备的条件：")
    lines.extend([f"  - {item}" for item in (starting.get('have') or [])] or ["  - （未填写）"])
    lines.append("- 缺口：")
    lines.extend([f"  - {item}" for item in (starting.get('gaps') or [])] or ["  - （未填写）"])
    lines.append("")
    lines.append("**运行条件（正文与下表同源，均来自这组条件）**")
    lines.append("")
    lines.append("| 条件 | 判断 | 状态 | 依据或引文 | 定位 |")
    lines.append("| --- | --- | --- | --- | --- |")
    conditions = difficulty.get("conditions") or []
    if not isinstance(conditions, list):
        conditions = []
    for item in conditions:
        if not isinstance(item, dict):
            continue
        key = str(item.get("condition", ""))
        status = str(item.get("status", ""))
        parts: list[str] = []
        if status == "evidence":
            if item.get("quote"):
                parts.append(f"引文「{item['quote']}」")
        elif status == "inference":
            parts.append(str(item.get("basis") or "（未写依据）"))
        for evidence_id in item.get("evidence_ids") or []:
            parts.append(f"`{evidence_id}`")
        locators: list[str] = []
        for evidence_id in item.get("evidence_ids") or []:
            found = evidence_map.get(str(evidence_id))
            if found is not None:
                locators.append(f"{evidence_id}：{found.locator or '定位缺失'}")
        cells = [
            CONDITION_LABELS.get(key, key),
            str(item.get("value", "")),
            CONDITION_STATUS_LABELS.get(status, status),
            "；".join(parts) or "（未写依据）",
            "；".join(locators) or "—",
        ]
        lines.append("| " + " | ".join(cell.replace("|", "\\|").replace("\n", " ") for cell in cells) + " |")
    lines.append("")

    experiment = report.get("mini_experiment", {}) or {}
    explore = report.get("explore_further", {}) or {}
    lines.append("## 4. 一个小实验")
    lines.append("")
    done_criteria = str(action.get("done_criteria") or "").strip()
    entry_text = str(action.get("entry") or "").strip()
    if action.get("executable") is True:
        lines.append(f"- 先复现：{action.get('action', '（缺失）')}")
        lines.append(f"- 复现完成标志：{done_criteria or '（缺失）'}")
        lines.append(f"- 入口：{entry_text or '（缺失）'}")
    else:
        lines.append("- 本次没有可执行的学习动作；下面只保留情况说明、可参考来源与缺口。")
        lines.append(f"- 情况说明：{action.get('action', '（缺失）')}")
        lines.append(f"- 可参考来源：{entry_text or '（未提供）'}")
        lines.append(f"- 完成标准：{done_criteria or '不适用：本次未提供可执行学习动作'}")
    lines.append(f"- **建议尝试的改动（不是项目已具备的功能）**：{experiment.get('suggested_change', '（缺失）')}")
    lines.append(f"- 成功后能观察到：{experiment.get('observable', '（缺失）')}")
    lines.append(f"- 本次范围边界：{experiment.get('boundary', '（缺失）')}")
    lines.append("")
    lines.append("## 5. 继续探索的材料")
    lines.append("")
    lines.append(f"- 仓库：{explore.get('repo', '（缺失）')}")
    lines.append(f"- 文档：{explore.get('docs', '未核验到')}")
    lines.append(f"- 示例入口：{explore.get('example_entry', '（缺失）')}")
    lines.append("- 已知限制：")
    lines.extend([f"  - {item}" for item in (explore.get('known_limits') or [])] or ["  - （未填写）"])
    lines.append("")

    lines.append("## 6. 验证状态（由运行记录生成，不由模型自报）")
    lines.append("")
    lines.append(
        f"- 查阅文档：{'是' if verification.get('docs_reviewed') else '否'}　"
        f"查阅源码：{'是' if verification.get('source_reviewed') else '否'}　"
        f"实际运行：{'是' if verification.get('actually_ran') else '否'}"
    )
    if verification.get("release_reviewed") is not None:
        lines.append(f"- 查阅发布记录：{'是' if verification.get('release_reviewed') else '否'}")
    lines.append(f"- 说明：{verification.get('note', '（缺失）')}")
    lines.append(f"- 内容复核：{CONTENT_REVIEW_LABEL}（结构检查{'通过' if validation.ok else '未通过'}不等于内容已核验）")
    lines.append("")

    lines.append("## 7. 结论")
    lines.append("")
    lines.append(f"- 判定：**{DECISION_LABELS.get(str(recommendation.get('decision')), '未标注')}**")
    lines.append(f"- 理由：{recommendation.get('reason', '（缺失）')}")
    gaps = recommendation.get("evidence_gaps") or []
    lines.append("- 证据缺口：")
    lines.extend([f"  - {item}" for item in gaps] or ["  - （未填写）"])
    uncertainties = recommendation.get("uncertainties") or []
    lines.append("- 不确定性：")
    lines.extend([f"  - {item}" for item in uncertainties] or ["  - （未填写）"])
    lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("## 附录 A：证据清单")
    lines.append("")
    lines.append("| 证据编号 | 类型 | 标题 | 来源类型 | 获取时间 | 定位 | 链接 |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- |")
    for item in evidence:
        lines.append(
            f"| `{item.evidence_id}` | {item.kind} | {item.title} | {item.source_type} |"
            f" {item.retrieved_at} | {item.locator or '-'} | {item.url} |"
        )
    lines.append("")

    if include_run_meta:
        lines.append("## 附录 B：本次运行与消耗")
        lines.append("")
        lines.append("| 项目 | 值 |")
        lines.append("| --- | --- |")
        for key, value in meta.items():
            lines.append(f"| {key} | {value} |")
        lines.append("")
        lines.extend(_stage_usage_block(stage_usage))
        lines.append("")
    lines.append("## 附录 C：质量门槛")
    lines.append("")
    lines.append(f"- 规则版本：{validation.rules_version}　{validation.structure_label}")
    lines.append(f"- 内容复核：{CONTENT_REVIEW_LABEL}")
    lines.append(
        "- 结构检查只能证明引文逐字出现在已取材料里，**不能证明引文支持结论**："
        "例如引用“支持 Linux”不能推出“支持 Windows”；语义仍需人工复核。"
    )
    for warning in validation.warnings:
        lines.append(f"- 警告：{warning}")
    for error in validation.errors:
        lines.append(f"- 错误：{error}")
    if not validation.warnings and not validation.errors:
        lines.append("- 没有警告与错误。")
    lines.append("")

    lines.append("## 附录 D：本项目没有做到的事")
    lines.append("")
    lines.append("- 没有运行下载的项目代码，没有复现任何性能结论。")
    lines.append("- 没有验证项目方宣称的性能数字。")
    lines.append("- 自动化候选发现归档、邮件周报、手机工作台属于增量 B/C/D，本报告不涉及。")
    lines.append("")
    if feedback_rows:
        lines.append("## 附录 E：用户对相关项目的既有反馈（用户原话，不是模型判断）")
        lines.append("")
        header = ["项目"] + [text for _key, text in FEEDBACK_ITEMS] + ["备注", "填写时间", "所属运行"]
        lines.append("| " + " | ".join(header) + " |")
        lines.append("|" + " --- |" * len(header))
        for row in feedback_rows:
            cells = [str(row.get("repo") or "")]
            cells += [feedback_label(str(row.get(key) or "")) for key, _text in FEEDBACK_ITEMS]
            note = str(row.get("note") or "").strip().replace("\n", " ") or "未填写"
            cells += [note, str(row.get("updated_at") or ""), str(row.get("run_id") or "")]
            lines.append("| " + " | ".join(cell.replace("|", "\\|") for cell in cells) + " |")
        lines.append("")
        lines.append("- 这些是用户原话：不参与证据校验、不代表用户认可项目，也不用于跨项目偏好推断。")
        lines.append("")
    return "\n".join(lines)


def meta_block(
    *,
    validation: Any = None,
    run_id: str,
    created_at: str,
    model_label: str,
    model_id: str,
    prompt_version: str,
    rules_version: str,
    profile_label: str,
    discovery: dict[str, Any],
    usage: dict[str, Any],
    http_summary: dict[str, Any],
    fixture: bool,
    comparators: list[str],
    roles_text: str = "",
    screening_text: str = "",
    budget_text: str = "",
) -> dict[str, Any]:
    cost_text = "费用未知（未配置单价）"
    if usage.get("cost_value") is not None:
        cost_text = f"{usage['cost_value']} {usage.get('cost_currency') or ''}".strip()
    return {
        "运行 ID": run_id,
        "生成时间": created_at,
        "分析模型": f"{model_label}（model_id={model_id}）",
        "提示词版本": prompt_version,
        "质量规则版本": rules_version,
        "学习档案": profile_label,
        "候选发现": discovery.get("summary", "未记录"),
        "对照项目": "、".join(comparators) or "无",
        "模型调用次数": usage.get("calls", 0),
        "输入 Token": usage.get("input_tokens", "未知"),
        "输出 Token": usage.get("output_tokens", "未知"),
        "模型耗时合计(ms)": usage.get("latency_ms", "未知"),
        "估算费用": cost_text,
        "HTTP 请求数（含模型 API 调用）": f"{http_summary.get('requests', 0)} 次成功 {http_summary.get('ok', 0)} 次，"
        f"失败 {http_summary.get('failed', 0)} 次，读取 {http_summary.get('bytes', 0)} 字节",
        **({"模型角色": roles_text} if roles_text else {}),
        **({"初筛结论": screening_text} if screening_text else {}),
        **({"模型请求": budget_text} if budget_text else {}),
        "结构检查": validation.structure_label if validation is not None else "未记录",
        "内容复核": "未完成（需要人工核对摘录是否支持结论）",
        "数据来源标记": "离线夹具（非真实模型调用）" if fixture else "真实模型调用与真实来源读取",
    }


def render_rejection(
    *,
    report: Any,
    validation: ValidationResult,
    meta: dict[str, Any],
    stage_usage: list[dict[str, Any]] | None = None,
) -> str:
    """Display structural failures safely without assuming payload structure or presenting learning advice."""
    lines: list[str] = [
        "# 报告未通过结构检查（未发布）",
        "",
        "本次运行的报告没有通过结构检查，因此不作为学习建议发布。"
        "原始模型输出、证据、用量与审计都保存在运行目录里，便于修复分析逻辑后复查。",
        "",
        "## 结构检查结果",
        "",
        f"- 规则版本：{validation.rules_version}",
        f"- 结构检查：{validation.structure_label}",
        f"- 内容复核：未完成（需要人工核对摘录是否真的支持结论）",
        f"- 错误 {len(validation.errors)} 条，警告 {len(validation.warnings)} 条",
        "",
    ]
    for item in validation.errors:
        lines.append(f"- 错误：{item}")
    for item in validation.warnings:
        lines.append(f"- 警告：{item}")
    if not validation.errors and not validation.warnings:
        lines.append("- 没有错误与警告。")
    lines += ["", "## 顶层字段概览（只读摘要）", ""]
    if isinstance(report, dict):
        for key, value in report.items():
            lines.append(f"- `{key}`：{type(value).__name__}")
    else:
        lines.append(f"- 报告顶层类型：{type(report).__name__}")
    lines += ["", "## 原始输出片段（截断）", "", "```json"]
    try:
        snapshot = json.dumps(report, ensure_ascii=False, default=str)[:2000]
    except Exception as exc:  # pragma: no cover - fallback
        snapshot = f"（无法序列化：{type(exc).__name__}: {exc}）"
    lines.append(snapshot)
    lines += ["```", "", "## 本次运行与消耗", "", "| 项目 | 值 |", "| --- | --- |"]
    for key, value in (meta or {}).items():
        lines.append(f"| {key} | {value} |")
    lines.append("")
    lines.extend(_stage_usage_block(stage_usage))
    return "\n".join(lines)


def basis_text(container: Any, evidence_map: dict[str, Evidence]) -> str:
    """Use readable sources/excerpts in prose; preserve IDs, commits, and locators in appendix A."""
    parts: list[str] = []
    snippet_source = ""
    if isinstance(container, dict):
        quote = container.get("quote")
        if isinstance(quote, str) and quote.strip():
            parts.append(f"引文「{quote.strip()}」")
        ids = container.get("evidence_ids") or []
    else:
        ids = []
    sources: list[str] = []
    for evidence_id in ids:
        item = evidence_map.get(str(evidence_id))
        if item is None:
            continue
        label = item.title or item.url
        link = f"[{label}]({item.url})"
        if link not in sources:
            sources.append(link)
        if not snippet_source:
            snippet_source = " ".join((item.excerpt or "").split())[:140]
    if sources:
        parts.append("来源：" + "、".join(sources))
    if snippet_source:
        parts.append(f"材料摘录：{snippet_source}" + ("…" if snippet_source else ""))
    return "；".join(parts) if parts else "（缺少可展示的依据）"


def _stage_usage_block(stage_usage: list[dict[str, Any]] | None) -> list[str]:
    """Build stage usage rows: model, calls, tokens, duration, and failure reasons."""
    if not stage_usage:
        return []
    lines = [
        "**分阶段模型调用**（按阶段记录模型、调用次数、token、耗时与失败原因）",
        "",
        "| 阶段 | 模型 | 调用 | 失败 | 输入 token | 输出 token | 推理 token | 耗时(ms) | 失败原因 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in stage_usage:
        cells = [
            str(row.get("stage", "")),
            str(row.get("model_id", "")),
            str(row.get("calls", 0)),
            str(row.get("failed", 0)),
            str(row.get("input_tokens", 0)),
            str(row.get("output_tokens", 0)),
            str(row.get("reasoning_tokens", 0)),
            str(row.get("latency_ms", 0)),
            str(row.get("error") or "—"),
        ]
        lines.append("| " + " | ".join(cell.replace("|", "\\|") for cell in cells) + " |")
    lines.append("")
    return lines
