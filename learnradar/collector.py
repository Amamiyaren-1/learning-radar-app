"""证据收集编排：对目标项目与对照项目抓取公开证据。

本轮（取证修复）要点：
- 读过的候选页面**全部保留**（正文 + 获取记录），不再读完就丢；调用方负责落盘。
- 送进模型的网页证据只挑**相关且去重**的片段，并有条数上限，给入门/核心机制材料留位置。
- 允许从项目明确链接的官方文档页**再跟随一跳**（同一站点、最多 N 个），只取入门/机制类链接，
  不是通用爬虫；安全与预算沿用 `learnradar/net.py` 与 `limits`。
- 示例文件只接受与示例相关的候选（得分 > 0 且非无关文件）；没有就如实记“未找到”，不凑数量。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .evidence import (
    Evidence,
    commit_evidence,
    dedupe,
    example_file_evidence,
    readme_evidence,
    release_evidence,
    repo_evidence,
    repo_facts,
    sha256_text,
    web_evidence,
)
from .net import FetchError, fetch, now_iso
from .readme import (
    analyze_example_content,
    docs_followup_links,
    example_file_links,
    pick_entry_candidates,
    section_locator,
    select_excerpt,
)
from .sources_github import GitHubClient

READABLE_CONTENT_TYPES = ("text/html", "text/plain", "text/markdown")
MECHANISM_HINTS = (
    "install",
    "getting started",
    "quickstart",
    "tutorial",
    "concept",
    "overview",
    "architecture",
    "how it works",
    "安装",
    "入门",
    "教程",
    "机制",
)


def _route(payload: dict[str, Any]) -> str:
    return str(payload.get("_evidence_route") or "")


@dataclass
class Collected:
    full_name: str
    role: str
    repo: dict[str, Any]
    evidence: list[Evidence] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    readme_available: bool = False
    routes: dict[str, str] = field(default_factory=dict)
    sections: list[dict[str, Any]] = field(default_factory=list)
    learning_entry: dict[str, Any] = field(default_factory=dict)
    pages: list[dict[str, Any]] = field(default_factory=list)

    @property
    def facts(self) -> dict[str, Any]:
        return repo_facts(self.repo)


def collect_repository(
    client: GitHubClient,
    full_name: str,
    *,
    role: str,
    max_sources: int = 8,
    readme_options: dict[str, Any] | None = None,
    fetch_learning_entry: bool = False,
    learning_entry_max_attempts: int = 3,
    example_file_max: int = 2,
    docs_followup_max: int = 1,
    entry_evidence_max: int = 2,
) -> Collected:
    options = {
        "head_chars": 500,
        "per_section": 900,
        "max_sections": 4,
        "budget": 3200,
        **(readme_options or {}),
    }
    collected = Collected(full_name=full_name, role=role, repo={})
    try:
        repo = client.get_repository(full_name)
    except FetchError as exc:
        collected.errors.append(f"读取仓库元数据失败：{exc}")
        return collected

    collected.repo = repo
    collected.routes["repo"] = _route(repo)
    stamp = now_iso()
    items: list[Evidence] = [repo_evidence(repo, retrieved_at=stamp, route=collected.routes["repo"])]

    default_branch = repo.get("default_branch") or "HEAD"
    commit_sha = ""
    if len(items) < max_sources:
        try:
            commits = client.latest_commits(full_name, ref=default_branch, per_page=3)
            commit_sha = str(commits[0].get("sha") or "") if commits else ""
            if commits:
                collected.routes["commits"] = _route(commits[0])
            item = commit_evidence(repo, commits, retrieved_at=stamp, route=collected.routes.get("commits", ""))
            if item is not None:
                items.append(item)
        except FetchError as exc:
            collected.errors.append(f"读取最近提交失败：{exc}")

    readme_text = ""
    if len(items) < max_sources:
        try:
            readme_text, readme_meta = client.get_readme(full_name, ref=commit_sha or default_branch)
            collected.routes["readme"] = _route(readme_meta)
            excerpt, used = select_excerpt(readme_text, **options)
            collected.sections = used
            items.append(
                readme_evidence(
                    repo,
                    readme_text,
                    readme_meta,
                    commit_sha=commit_sha,
                    retrieved_at=stamp,
                    route=collected.routes["readme"],
                    excerpt=excerpt,
                    locator_extra=section_locator(used),
                    notes_extra="摘录由程序按相关章节挑选（跳过徽章；含 Introduction/入门/示例/限制等）。",
                )
            )
            collected.readme_available = True
        except FetchError as exc:
            collected.errors.append(f"读取 README 失败：{exc}")

    if len(items) < max_sources:
        try:
            release = client.latest_release(full_name)
            if release is not None:
                collected.routes["release"] = _route(release)
                items.append(
                    release_evidence(release, repo, retrieved_at=stamp, route=collected.routes["release"])
                )
        except FetchError as exc:
            collected.errors.append(f"读取发布记录失败：{exc}")

    if role == "target" and fetch_learning_entry:
        from .readme import analyze_readme_entry
        readme_source = next((item for item in items if item.kind == 'github_readme'), None)
        readme_signals = analyze_readme_entry(readme_source.excerpt, full_name) if readme_source else {}
        # Reuse an already-read and retained source, before following unrelated
        # overview links. No new HTTP request or extra evidence slot is needed.
        if readme_source and readme_signals.get('example_signal'):
            entry = {'url': readme_source.url, 'verified': True, 'example_signal': True,
                     'signals': readme_signals['signals'], 'evidence_id': readme_source.evidence_id,
                     'source': 'retained_readme', 'error': '', 'attempts': [], 'example_files': []}
        else:
            entry = _collect_learning_entry(
                client,
                repo,
                readme_text,
                max_attempts=max(1, learning_entry_max_attempts),
                ref=commit_sha,
                max_files=max(0, example_file_max),
                docs_followup_max=max(0, docs_followup_max),
                entry_evidence_max=max(1, entry_evidence_max),
                remaining_sources=max(0, max_sources - len(items)),
                stamp=stamp,
            )
        for record in entry.pop("pages", []) or []:
            collected.pages.append(record)
        for item in entry.pop("_evidence", []) or []:
            items.append(item)
        for record in entry.get("example_files") or []:
            file_item = record.pop("_evidence", None)
            if file_item is not None:
                items.append(file_item)
        collected.learning_entry = entry
        skipped = entry.get("skipped_links") or []
        if skipped:
            preview = "；".join(
                f"{item.get('url')}（{item.get('reason')}）" for item in skipped[:2]
            )
            extra = f" 等 {len(skipped)} 个" if len(skipped) > 2 else ""
            collected.errors.append(f"跳过了无法请求的链接：{preview}{extra}")

    collected.evidence = dedupe(items)[:max_sources]
    return collected


def _fetch_page(client: GitHubClient, url: str, *, stamp: str, reason: str = "") -> dict[str, Any]:
    """读取一个网页并保留正文与获取记录（失败也记录）。"""
    record: dict[str, Any] = {"url": url, "retrieved_at": stamp, "reason": reason}
    try:
        result = fetch(
            url,
            headers={"User-Agent": client.user_agent, "Accept": "text/html,application/xhtml+xml"},
            timeout=client.timeout,
            max_bytes=min(client.max_bytes, 400_000),
            retries=1,
            audit=client.audit,
        )
    except FetchError as exc:
        record.update({"ok": False, "error": str(exc)[:200]})
        return record
    content_type = (result.content_type or "").lower()
    if content_type and not any(item in content_type for item in READABLE_CONTENT_TYPES):
        record.update({"ok": False, "error": f"内容类型不可读：{result.content_type}"})
        return record
    analysis = analyze_example_content(result.text())
    record.update(
        {
            "ok": True,
            "final_url": result.final_url,
            "bytes": result.size,
            "sha256": result.sha256,
            "content_type": result.content_type,
            "text": result.text(),
            "example_signal": analysis["example_signal"],
            "signals": analysis["signals"],
            "code_blocks": analysis["code_blocks"],
        }
    )
    return record


def _page_relevance(page: dict[str, Any]) -> int:
    """相关度：有代码/长正文/机制关键词的页面优先；列表页与空壳页靠后。"""
    text = (page.get("text") or "").lower()
    score = 0
    if page.get("example_signal"):
        score += 4
    if page.get("code_blocks"):
        score += 2
    lowered = text[:20000]
    if any(hint in lowered for hint in MECHANISM_HINTS):
        score += 3
    if len(text) > 1500:
        score += 1
    return score


def _select_entry_evidence(pages: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """只把相关且去重的网页送进模型证据，并保留条数上限。"""
    kept: list[dict[str, Any]] = []
    seen_hash: set[str] = set()
    seen_url: set[str] = set()
    ranked = sorted(
        [page for page in pages if page.get("ok")],
        key=lambda page: (-_page_relevance(page), -(page.get("bytes") or 0)),
    )
    for page in ranked:
        digest = str(page.get("sha256") or "")
        url = str(page.get("final_url") or page.get("url") or "")
        if digest and digest in seen_hash:
            continue
        if url in seen_url:
            continue
        seen_hash.add(digest)
        seen_url.add(url)
        kept.append(page)
        if len(kept) >= limit:
            break
    return kept


def _collect_learning_entry(
    client: GitHubClient,
    repo: dict[str, Any],
    readme_text: str,
    *,
    max_attempts: int,
    ref: str = "",
    max_files: int = 0,
    docs_followup_max: int = 1,
    entry_evidence_max: int = 2,
    remaining_sources: int = 4,
    stamp: str,
) -> dict[str, Any]:
    """读取候选入口页 →（必要时）同站一跳 → 选相关片段 → （没有示例信号时）找示例文件。"""
    pages: list[dict[str, Any]] = []
    skipped_links: list[dict[str, str]] = []
    ranked = pick_entry_candidates(
        repo, readme_text, limit=max_attempts + 2, skipped=skipped_links
    )
    ordered: list[dict[str, Any]] = []
    for purpose in ("docs", "examples"):
        for candidate in ranked:
            if candidate["purpose"] == purpose and candidate not in ordered:
                ordered.append(candidate)
                break
    for candidate in ranked:
        if candidate not in ordered:
            ordered.append(candidate)
    for candidate in ordered[:max_attempts]:
        url = str(candidate["url"])
        page = _fetch_page(
            client,
            url,
            stamp=stamp,
            reason="README 链接（{}；章节：{}）".format(candidate["purpose"], candidate["section"] or "未标注"),
        )
        pages.append(page)
        if page.get("ok") and page.get("example_signal"):
            break

    followed = 0
    for page in list(pages):
        if followed >= docs_followup_max or not page.get("ok"):
            continue
        base_url = str(page.get("final_url") or page.get("url"))
        for link, label in docs_followup_links(
            page.get("text") or "", base_url, limit=1, skipped=skipped_links
        ):
            if any(str(other.get("final_url")) == link for other in pages):
                continue
            followed_page = _fetch_page(
                client, link, stamp=stamp, reason=f"官方文档页一跳（{label or link}；来自 {base_url}）"
            )
            followed_page["followed_from"] = base_url
            pages.append(followed_page)
            followed += 1
            break

    kept_pages = _select_entry_evidence(pages, max(1, min(entry_evidence_max, remaining_sources)))
    kept_urls = {str(page.get("final_url") or page.get("url")) for page in kept_pages}
    for page in pages:
        page["kept_as_evidence"] = str(page.get("final_url") or page.get("url")) in kept_urls

    entry_url = ""
    entry_page = next((page for page in kept_pages if page.get("example_signal")), None)
    if entry_page is None:
        entry_page = kept_pages[0] if kept_pages else None
    if entry_page is not None:
        entry_url = str(entry_page.get("final_url") or entry_page.get("url"))

    evidence_items = [
        web_evidence(
            str(page.get("final_url") or page.get("url")),
            page.get("text") or "",
            retrieved_at=stamp,
            sha256=str(page.get("sha256") or ""),
            owner=str(repo.get("full_name") or ""),
            notes_extra=(
                ("页面里检测到示例信号（本次未实际运行示例）。" if page.get("example_signal") else "页面里没有检测到示例信号。")
                + ("（由官方文档页一跳取得）" if page.get("followed_from") else "")
            ),
        )
        for page in kept_pages
    ]

    files: list[dict[str, Any]] = []
    if max_files > 0 and not any(page.get("example_signal") for page in pages):
        files = _collect_example_files(
            client,
            repo,
            readme_text,
            ref=ref,
            max_files=max_files,
            stamp=stamp,
            skipped=skipped_links,
        )

    attempts = [
        {
            "url": str(page.get("final_url") or page.get("url")),
            "ok": bool(page.get("ok")),
            "example_signal": bool(page.get("example_signal")),
            "signals": page.get("signals") or [],
            "error": page.get("error", ""),
        }
        for page in pages
    ]

    if entry_url:
        error = ""
        if not (entry_page and entry_page.get("example_signal")):
            if files:
                names = "、".join(str(item.get("path")) for item in files)
                error = f"本次未核验到可执行路径（已读到示例文件：{names}；仅有代码，未核验到启动步骤）"
            else:
                error = "本次未核验到可执行路径（入口页与一跳文档都没有检测到示例信号，也没有找到合适的示例文件）"
        entry = {
            "url": entry_url,
            "verified": True,
            "example_signal": bool(entry_page and entry_page.get("example_signal")),
            "signals": (entry_page or {}).get("signals") or [],
            "error": error,
            "pages": pages,
            "attempts": attempts,
            "example_files": files,
            "skipped_links": skipped_links,
            "_evidence": evidence_items,
        }
    else:
        entry = {
            "url": "",
            "verified": False,
            "example_signal": False,
            "signals": [],
            "error": "候选学习入口都读取失败：" + "；".join(
                f"{page.get('url')}（{page.get('error')}）" for page in pages if not page.get("ok")
            ),
            "pages": pages,
            "attempts": attempts,
            "example_files": files,
            "skipped_links": skipped_links,
            "_evidence": evidence_items,
        }
    return entry


def _collect_example_files(
    client: GitHubClient,
    repo: dict[str, Any],
    readme_text: str,
    *,
    ref: str,
    max_files: int,
    stamp: str,
    skipped: list[dict[str, str]] | None = None,
) -> list[dict[str, Any]]:
    """沿 README 的明确链接读取与示例相关的具体文件；只接受示例相关候选。"""
    if max_files <= 0:
        return []
    results: list[dict[str, Any]] = []
    for path, html_url in example_file_links(readme_text, limit=max_files):
        try:
            text = client.fetch_repo_file(str(repo.get("full_name") or ""), path, ref=ref or None)
        except FetchError as exc:
            results.append({"path": path, "url": html_url, "ok": False, "error": str(exc)[:200]})
            continue
        item = example_file_evidence(
            repo,
            path,
            text,
            ref=ref,
            html_url=html_url,
            retrieved_at=stamp,
            route=f"raw.githubusercontent.com/{path}",
        )
        results.append(
            {
                "path": path,
                "url": html_url,
                "ok": True,
                "sha256": sha256_text(text),
                "evidence_id": item.evidence_id,
                "_evidence": item,
            }
        )
    return results
