"""离线复检：用已保存的原始产物重跑质量门槛，不调用模型、不改动原产物。

用法（开发/审计用，不属于产品流程）：

    python scripts/offline_recheck.py <run-id> [--data-dir data]

它读取 `data/runs/<run-id>/` 里的 `report.json`、`evidence.json`、`run-context.json`
与 `validation.json`，按当前 `learnradar/validate.py` 的规则重新核对一次，把结果
写成同目录的 `offline-recheck.json`（含规则版本、前后差异）。原 `report.json`、
`validation.json`、`raw_model_output.txt` 一律不改。

注意：复检只说明“结构门槛”在新规则下的结果，不代表内容复核（引文是否真的支持结论）已完成。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from learnradar.cli import _generated_verification  # noqa: E402  （与正常流程共用同一套查阅状态生成）
from learnradar.config import load_settings  # noqa: E402
from learnradar.evidence import Evidence, matchable, quote_supported_by  # noqa: E402
from learnradar.validate import validate_report  # noqa: E402
from learnradar import evidence as evidence_module, validate as validate_module  # noqa: E402


def _apply_compat_conversions(report: Any, conversions: list[str]) -> None:
    """旧产物缺显式可执行状态时的兼容转换：只补状态，绝不判定该动作可执行。"""
    action = report.get("learning_action") if isinstance(report, dict) else None
    if isinstance(action, dict) and "executable" not in action:
        action["executable"] = False
        conversions.append(
            "learning_action.executable 缺失：按最小显式状态补为 false（兼容转换，未判定该动作可执行）；"
            "磁盘上的 report.json 未改动"
        )


def _quote_diagnostics(
    report: Any, evidence: list[Evidence], errors: list[str] | None = None,
) -> list[dict[str, Any]]:
    """记录与 validate_report 相同的按 ID 选取（重复 ID 时最后一项），只诊断不改变判定。"""
    known = {item.evidence_id: (index, item) for index, item in enumerate(evidence)}
    rows: list[dict[str, Any]] = []

    def visit(node: Any, path: str, pointer: str) -> None:
        if isinstance(node, list):
            for index, item in enumerate(node):
                visit(item, f"{path}[{index + 1}]", f"{pointer}/{index}")
        elif isinstance(node, dict):
            quote = node.get("quote")
            ids = node.get("evidence_ids")
            if isinstance(quote, str) and isinstance(ids, list):
                selected = []
                for evidence_id in ids:
                    selected_item = known.get(str(evidence_id))
                    if selected_item is None:
                        selected.append({"evidence_id": evidence_id, "missing": True})
                        continue
                    index, item = selected_item
                    normalized_excerpt = matchable(item.excerpt)
                    selected.append({
                        "evidence_id": item.evidence_id, "array_index_zero_based": index,
                        "same_id_count": sum(e.evidence_id == item.evidence_id for e in evidence),
                        "kind": item.kind, "owner": item.owner, "url": item.url,
                        "text_field": "excerpt", "normalized_excerpt": normalized_excerpt,
                        "substring_match": matchable(quote) in normalized_excerpt,
                        "format_aware_match": quote_supported_by(quote, [item])[0],
                    })
                rows.append({"path": path, "path_index_base": 1, "json_pointer": pointer,
                             "validation_errors": [error for error in (errors or [])
                                                   if error.startswith(f"{path}.quote ")],
                             "quote": quote, "normalized_quote": matchable(quote),
                             "selected_evidence": selected})
            for key, value in node.items():
                if isinstance(value, (list, dict)):
                    pointer_key = key.replace("~", "~0").replace("/", "~1")
                    visit(value, f"{path}.{key}" if path else key, f"{pointer}/{pointer_key}")

    visit(report, "", "")
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="对已保存的运行做离线结构复检（不调用模型）")
    parser.add_argument("run_id", help="data/runs 下的运行目录名")
    parser.add_argument("--data-dir", default="data", help="数据目录（默认 data）")
    parser.add_argument("--out", type=Path, help="独立复核文件；默认沿用运行目录offline-recheck.json")
    args = parser.parse_args(argv)

    run_dir = Path(args.data_dir) / "runs" / args.run_id
    if not run_dir.is_dir():
        print(f"找不到运行目录：{run_dir}", file=sys.stderr)
        return 2

    required = ("report.json", "evidence.json", "run-context.json")
    missing = [name for name in required if not (run_dir / name).is_file()]
    if missing:
        print(
            f"离线复检未执行：缺少 {', '.join(missing)}。"
            "需要保存的 target、comparators（实际采集范围）、learning_entry（核验状态及入口）；"
            "report.json 是待检模型输出，不能用它推定运行上下文；"
            "evidence.json 的归属也不能确定目标/对照角色。未生成复检结果、未改动原产物。",
            file=sys.stderr,
        )
        return 2
    try:
        report = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
        raw_evidence = json.loads((run_dir / "evidence.json").read_text(encoding="utf-8"))
        evidence = [Evidence(**item) for item in raw_evidence]
        context = json.loads((run_dir / "run-context.json").read_text(encoding="utf-8"))
        previous_path = run_dir / "validation.json"
        previous = json.loads(previous_path.read_text(encoding="utf-8")) if previous_path.exists() else {}
    except (OSError, ValueError, TypeError) as exc:
        print(f"离线复检未执行：保存产物无法读取/解析：{exc}", file=sys.stderr)
        return 2
    if (
        not isinstance(context, dict)
        or not isinstance(context.get("target"), str) or not context["target"].strip()
        or not isinstance(context.get("comparators"), list)
        or any(not isinstance(name, str) or not name.strip() for name in context["comparators"])
        or not isinstance(context.get("learning_entry"), dict)
        or not isinstance(context["learning_entry"].get("verified"), bool)
        or (context["learning_entry"].get("verified") and (
            not isinstance(context["learning_entry"].get("url"), str)
            or not context["learning_entry"]["url"].strip()
            or not isinstance(context["learning_entry"].get("example_signal"), bool)
        ))
    ):
        print(
            "离线复检未执行：run-context.json 不完整，需要 target（非空字符串）、"
            "comparators（项目名列表）、learning_entry.verified（布尔值）；"
            "已核验入口还需 url 与 example_signal（布尔值）。不推定缺失上下文。",
            file=sys.stderr,
        )
        return 2
    conversions: list[str] = []
    _apply_compat_conversions(report, conversions)

    target = str(context.get("target") or "")
    comparators = [str(item) for item in (context.get("comparators") or [])]
    learning_entry = context.get("learning_entry") or {}
    settings = load_settings()

    result = validate_report(
        report,
        evidence=evidence,
        target_repo=target,
        min_comparators=settings.limit("min_comparators", 2),
        collected_repos=[target, *comparators],
        expected_verification=_generated_verification(evidence),
        require_quotes=True,
        require_verified_entry=bool(learning_entry.get("verified")),
        verified_entry=learning_entry or None,
    )
    summary = result.summary()
    payload = {
        "run_id": args.run_id,
        "kind": "offline-recheck",
        "target": target,
        "comparators": comparators,
        "previous_validation": previous,
        "conversions": conversions,
        "inputs": {
            name: {"path": str((run_dir / name).resolve()),
                   "sha256": hashlib.sha256((run_dir / name).read_bytes()).hexdigest()}
            for name in required
        },
        "validation_options": {"require_quotes": True,
                               "min_comparators": settings.limit("min_comparators", 2),
                               "require_verified_entry": bool(learning_entry.get("verified"))},
        "quote_diagnostics": _quote_diagnostics(report, evidence, summary["errors"]),
        "implementation": {
            name: {"path": str(Path(module.__file__).resolve()),
                   "sha256": hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()}
            for name, module in (("evidence", evidence_module), ("validate", validate_module))
        },
        "recheck": summary,
        "changed": {
            "ok": [previous.get("ok"), summary["ok"]],
            "rules_version": [previous.get("rules_version"), summary["rules_version"]],
            "errors_removed": sorted(set(previous.get("errors") or []) - set(summary["errors"])),
            "errors_added": sorted(set(summary["errors"]) - set(previous.get("errors") or [])),
        },
        "note": (
            "复检只用已保存的原始产物重跑结构门槛：不调用模型、不重新取证、"
            "不覆盖 report.json 与 validation.json；结构复检通过也不代表内容复核已完成。"
            "若 conversions 非空，说明该旧产物缺少显式可执行状态，复检按最小状态补为 false 后判定，不把它当作已提供可执行动作。"
        ),
    }
    output = args.out or run_dir / "offline-recheck.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "run_id": args.run_id,
                "rules_version": summary["rules_version"],
                "structure_check": summary["structure_check"],
                "errors": summary["errors"],
                "warnings": summary["warnings"],
                "quote_error_details": [row for row in payload["quote_diagnostics"]
                                        if row["validation_errors"]],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    print(f"已写入 {output}")
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
