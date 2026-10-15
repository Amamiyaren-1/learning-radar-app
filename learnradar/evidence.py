"""Evidence and provenance.
Retain URL, retrieval time, title, repository owner, commit/path/interface/section locator, hash, and excerpts.
This evidence record does not contain the full original page."""

from __future__ import annotations

import hashlib
import html
import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable

EXCERPT_LIMIT = 1200

SOURCE_OFFICIAL = "official"
SOURCE_METADATA = "metadata"
SOURCE_THIRD_PARTY = "third_party"

KIND_REPO = "github_repo"
KIND_README = "github_readme"
KIND_RELEASE = "github_release"
KIND_COMMIT = "github_commit"
KIND_WEB = "web_page"

STAR_PATTERN = re.compile(r"(\d[\d,]*)\s*(?:stars?|星)\b", re.I)


def _norm_url(url: str) -> str:
    return re.sub(r"#.*$", "", (url or "").strip().rstrip("/"))


def normalize_repo_name(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text.startswith("repo:"):
        text = text[len("repo:") :]
    if "://" in text:
        text = text.split("://", 1)[1]
        text = text.split("/", 1)[1] if "/" in text else text
    text = text.strip().strip("/")
    if text.endswith(".git"):
        text = text[: -len(".git")]
    return text.strip()


def evidence_id(kind: str, url: str) -> str:
    digest = hashlib.sha1(f"{kind}|{_norm_url(url)}".encode("utf-8")).hexdigest()[:10]
    return f"E-{kind.split('_')[-1]}-{digest}"


@dataclass
class Evidence:
    evidence_id: str
    kind: str
    url: str
    title: str
    retrieved_at: str
    source_type: str = SOURCE_OFFICIAL
    owner: str = ""
    commit_sha: str = ""
    sha256: str = ""
    locator: str = ""
    excerpt: str = ""
    notes: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def prompt_block(self) -> str:
        lines = [
            f"[{self.evidence_id}] {self.kind} | {self.title}",
            f"  url: {self.url}",
            f"  获取时间: {self.retrieved_at} | 来源类型: {self.source_type} | 所属仓库: {self.owner or '未标注'}",
        ]
        if self.commit_sha:
            lines.append(f"  commit: {self.commit_sha}")
        if self.locator:
            lines.append(f"  定位: {self.locator}")
        if self.excerpt:
            lines.append("  摘录:")
            lines.append(indent(self.excerpt, "    "))
        return "\n".join(lines)

    def numbers(self) -> set[int]:
        """A verifiable numeric value present in this evidence, such as star count."""
        found: set[int] = set()
        stars = self.extra.get("stars")
        if isinstance(stars, int):
            found.add(int(stars))
        for match in STAR_PATTERN.finditer(self.excerpt or ""):
            found.add(int(match.group(1).replace(",", "")))
        return found


def indent(text: str, prefix: str) -> str:
    return "\n".join(prefix + line for line in text.splitlines())


def normalize_whitespace(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return re.sub(r"\n{3,}", "\n\n", text).strip()


# Remove emphasis/inline-code markers without spaces: **Python 3.10-3.14**.
# Inserting a space before the period would cause a false-negative literal quote match.
_MARKDOWN_STRIP = re.compile(r"[*`]")
_MARKDOWN_SPACE = ("_", ">", "#", "[", "]", "(", ")", "|")
_INLINE_LINK = re.compile(r"\[([^\]\n]+)\]\((https?://[^\s)]+)\)")


def matchable(text: str) -> str:
    """Normalize whitespace, Markdown markers, and ASCII case for contiguous quote matching.
A matching quote does not establish that it supports the conclusion."""
    text = normalize_whitespace(text or "")
    text = _MARKDOWN_STRIP.sub("", text)
    for token in _MARKDOWN_SPACE:
        text = text.replace(token, " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip().lower()


def quote_supported_by(quote: str, items: Iterable[Evidence]) -> tuple[bool, str]:
    """Return whether a quote matches an evidence excerpt, plus an explanation."""
    normalized = matchable(quote)
    if len(normalized) < 8:
        return False, "引文过短，无法核对"
    for item in items:
        if normalized in matchable(item.excerpt):
            return True, item.evidence_id
        # Visible link text is contiguous when Markdown is rendered. The
        # hidden destination must not interrupt a verbatim visible quotation.
        # Keep the raw path above for quotations explicitly including URLs.
        visible = _INLINE_LINK.sub(r"\1", item.excerpt)
        if normalized in matchable(visible):
            return True, item.evidence_id
    return False, "在所引证据的摘录里找不到这段文字"


CODE_BLOCK_HTML = re.compile(r"(?is)<pre[^>]*>(.*?)</pre>")
INLINE_CODE_HTML = re.compile(r"(?is)<code[^>]*>(.*?)</code>")
ANCHOR_HTML = re.compile(r'(?is)<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>')
INNER_TAG = re.compile(r"(?s)<[^>]+>")


def strip_html(raw: str) -> str:
    """Extract page text while preserving code blocks and links needed for example detection."""
    text = raw or ""
    text = CODE_BLOCK_HTML.sub(
        lambda m: "\n```\n" + html.unescape(INNER_TAG.sub("", m.group(1))).strip() + "\n```\n",
        text,
    )
    text = INLINE_CODE_HTML.sub(
        lambda m: "`" + html.unescape(INNER_TAG.sub("", m.group(1))).strip() + "`",
        text,
    )
    text = ANCHOR_HTML.sub(
        lambda m: (
            html.unescape(INNER_TAG.sub("", m.group(2))).strip() + " (" + m.group(1) + ")"
        ).strip(),
        text,
    )
    without_scripts = re.sub(r"(?is)<(script|style).*?</\1>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", without_scripts)
    return re.sub(r"[ \t\u00a0]{2,}", " ", html.unescape(text)).strip()


def clip(text: str, limit: int = EXCERPT_LIMIT) -> str:
    text = normalize_whitespace(text)
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "…（节选）"


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _with_route(locator: str, route: str) -> str:
    return f"{locator}（来源入口：{route}）" if route else locator


def repo_facts(repo: dict[str, Any]) -> dict[str, Any]:
    license_info = repo.get("license") or {}
    return {
        "full_name": repo.get("full_name"),
        "html_url": repo.get("html_url"),
        "description": repo.get("description"),
        "stars": repo.get("stargazers_count"),
        "forks": repo.get("forks_count"),
        "open_issues": repo.get("open_issues_count"),
        "created_at": repo.get("created_at"),
        "pushed_at": repo.get("pushed_at"),
        "updated_at": repo.get("updated_at"),
        "archived": repo.get("archived"),
        "is_fork": repo.get("fork"),
        "language": repo.get("language"),
        "license": license_info.get("spdx_id"),
        "topics": repo.get("topics") or [],
        "default_branch": repo.get("default_branch"),
        "homepage": repo.get("homepage"),
    }


def repo_evidence(repo: dict[str, Any], *, retrieved_at: str, route: str = "") -> Evidence:
    facts = repo_facts(repo)
    url = str(facts["html_url"])
    excerpt = (
        f"仓库元数据（GitHub 官方接口）：{facts['full_name']}；"
        f"Stars={facts['stars']}；Forks={facts['forks']}；"
        f"创建于 {facts['created_at']}；最后推送 {facts['pushed_at']}；"
        f"归档={facts['archived']}；Fork={facts['is_fork']}；"
        f"语言={facts['language']}；许可证={facts['license']}；"
        f"默认分支={facts['default_branch']}；主题={', '.join(facts['topics'])}。\n"
        f"仓库自述：{facts['description'] or '（无）'}"
    )
    return Evidence(
        evidence_id=evidence_id(KIND_REPO, url),
        kind=KIND_REPO,
        url=url,
        title=f"{facts['full_name']} 仓库元数据",
        retrieved_at=retrieved_at,
        source_type=SOURCE_METADATA,
        owner=normalize_repo_name(facts["full_name"]),
        sha256=sha256_text(json.dumps(facts, ensure_ascii=False, sort_keys=True)),
        locator=_with_route("GitHub 仓库元数据接口", route),
        excerpt=clip(excerpt, 900),
        extra=facts,
    )


def readme_evidence(
    repo: dict[str, Any],
    readme_text: str,
    readme_meta: dict[str, Any],
    *,
    commit_sha: str,
    retrieved_at: str,
    route: str = "",
    excerpt: str | None = None,
    locator_extra: str = "",
    notes_extra: str = "",
) -> Evidence:
    """README evidence uses caller-selected excerpts, falling back to whole-document truncation."""
    owner = normalize_repo_name(repo.get("full_name"))
    path = readme_meta.get("path") or "README"
    html_url = readme_meta.get("html_url") or f"{repo.get('html_url')}#readme"
    locator = f"{path} @ {commit_sha[:12] if commit_sha else 'HEAD（未锁定 commit）'}"
    if locator_extra:
        locator = f"{locator}（{locator_extra}）"
    body = excerpt if excerpt is not None else clip(readme_text, 1800)
    notes = "README 由项目维护者撰写，属于项目方自述（一方证据），不是独立验证。"
    if notes_extra:
        notes = f"{notes}{notes_extra}"
    return Evidence(
        evidence_id=evidence_id(KIND_README, str(html_url)),
        kind=KIND_README,
        url=str(html_url),
        title=f"{owner} {path}",
        retrieved_at=retrieved_at,
        source_type=SOURCE_OFFICIAL,
        owner=owner,
        commit_sha=commit_sha,
        sha256=sha256_text(readme_text),
        locator=_with_route(locator, route),
        excerpt=body,
        notes=notes,
    )


def release_evidence(
    release: dict[str, Any],
    repo: dict[str, Any],
    *,
    retrieved_at: str,
    route: str = "",
) -> Evidence:
    owner = normalize_repo_name(repo.get("full_name"))
    url = str(release.get("html_url") or repo.get("html_url"))
    body = clip(release.get("body") or "", 900)
    excerpt = (
        f"发布标签：{release.get('tag_name')}；名称：{release.get('name')}；"
        f"发布时间：{release.get('published_at')}；预发布：{release.get('prerelease')}。\n"
        f"发布说明摘录：{body or '（本次没有取到发布说明正文）'}"
    )
    return Evidence(
        evidence_id=evidence_id(KIND_RELEASE, url),
        kind=KIND_RELEASE,
        url=url,
        title=f"{owner} 发布 {release.get('tag_name')}",
        retrieved_at=retrieved_at,
        source_type=SOURCE_OFFICIAL,
        owner=owner,
        locator=_with_route("GitHub 发布记录接口", route),
        excerpt=clip(excerpt, 1100),
        extra={
            "tag_name": release.get("tag_name"),
            "published_at": release.get("published_at"),
            "prerelease": release.get("prerelease"),
        },
        notes="" if body else "发布说明正文缺失，只有标签与时间。",
    )


def commit_evidence(
    repo: dict[str, Any],
    commits: list[dict[str, Any]],
    *,
    retrieved_at: str,
    route: str = "",
) -> Evidence | None:
    if not commits:
        return None
    owner = normalize_repo_name(repo.get("full_name"))
    head = commits[0]
    sha = str(head.get("sha") or "")
    date = ((head.get("commit") or {}).get("committer") or {}).get("date")
    message = ((head.get("commit") or {}).get("message") or "").splitlines()[0]
    url = f"{repo.get('html_url')}/commit/{sha}"
    excerpt = (
        f"默认分支最近提交：{sha[:12]}，时间 {date}，提交信息：{message}。"
        "注意：最近有提交不等于有实质技术创新。"
    )
    return Evidence(
        evidence_id=evidence_id(KIND_COMMIT, url),
        kind=KIND_COMMIT,
        url=url,
        title=f"{owner} 最近提交",
        retrieved_at=retrieved_at,
        source_type=SOURCE_METADATA,
        owner=owner,
        commit_sha=sha,
        locator=_with_route("GitHub 提交记录", route),
        excerpt=clip(excerpt, 400),
    )


KIND_EXAMPLE_FILE = "github_example_file"


def example_file_evidence(
    repo: dict[str, Any],
    path: str,
    text: str,
    *,
    ref: str,
    html_url: str,
    retrieved_at: str,
    route: str = "",
) -> Evidence:
    """Preserve concrete example file paths, commit IDs, and short locating excerpts."""
    owner = normalize_repo_name(repo.get("full_name"))
    return Evidence(
        evidence_id=evidence_id(KIND_EXAMPLE_FILE, html_url),
        kind=KIND_EXAMPLE_FILE,
        url=html_url,
        title=f"{owner} 示例文件 {path}",
        retrieved_at=retrieved_at,
        source_type=SOURCE_OFFICIAL,
        owner=owner,
        commit_sha=ref,
        sha256=sha256_text(text),
        locator=_with_route(f"{path} @ {ref[:12] if ref else 'HEAD'}", route),
        excerpt=clip(text, 600),
        notes=
            "示例目录下的具体文件；有代码不等于已经核验到启动步骤，是否可执行仍需人工复核。",
        extra={"path": path, "ref": ref, "lines_hint": len(text.splitlines())},
    )


WEB_KEYWORDS = (
    "install", "installation", "getting started", "quickstart", "quick start", "usage",
    "concept", "overview", "architecture", "dependency", "jdk", "maven", "jdbc",
    "pip install", "npm install", "uv add", "tool", "api", "安装", "入门", "用法",
)


def select_web_excerpt(text: str, *, head_chars: int = 500, window: int = 420, budget: int = 1400) -> str:
    # Preserve installation/mechanism text with head plus keyword windows within the same budget.
    body = normalize_whitespace(text)
    if len(body) <= budget:
        return body
    parts = [body[:head_chars].strip()]
    used = head_chars
    lowered = body.lower()
    for keyword in WEB_KEYWORDS:
        if used + 120 >= budget:
            break
        index = lowered.find(keyword)
        if index < 0:
            continue
        start = max(0, index - 80)
        chunk = body[start : start + window].strip()
        if chunk and chunk not in parts:
            parts.append(chunk)
            used += len(chunk)
    return "\n…（分段节选）…\n".join(parts) + "\n…（节选）"


def web_evidence(
    url: str,
    raw_html: str,
    *,
    retrieved_at: str,
    sha256: str,
    title: str = "",
    owner: str = "",
    notes_extra: str = "",
) -> Evidence:
    text = strip_html(raw_html)
    page_title = title or _guess_title(raw_html) or url
    notes = "第三方页面：只作线索与旁证，不直接充当已验证性能结论。"
    if notes_extra:
        notes = f"{notes}{notes_extra}"
    return Evidence(
        evidence_id=evidence_id(KIND_WEB, url),
        kind=KIND_WEB,
        url=url,
        title=page_title,
        retrieved_at=retrieved_at,
        source_type=SOURCE_THIRD_PARTY,
        owner=normalize_repo_name(owner),
        sha256=sha256,
        locator="网页正文（程序抽取文本）",
        excerpt=select_web_excerpt(text, budget=1400),
        notes=notes,
    )


def _guess_title(raw_html: str) -> str:
    match = re.search(r"(?is)<title[^>]*>(.*?)</title>", raw_html)
    return html.unescape(match.group(1)).strip() if match else ""


def dedupe(items: Iterable[Evidence]) -> list[Evidence]:
    seen: dict[str, Evidence] = {}
    for item in items:
        key = f"{item.kind}|{_norm_url(item.url)}"
        if key not in seen:
            seen[key] = item
    return list(seen.values())


def evidence_index(items: Iterable[Evidence]) -> dict[str, Evidence]:
    return {item.evidence_id: item for item in items}
