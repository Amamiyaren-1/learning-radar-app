"""Select README sections, learning entries, and executable-example signals.
Score relevant sections within bounded excerpts and preserve headings/line locators.
Opening an overview page alone is not an executable learning step.
Detect example signals explicitly and report their absence honestly."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import Any

from .net import unrequestable_reason

HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
FENCE = re.compile(r"^\s*(```|~~~)")
LINK = re.compile(r"\[([^\]\n]{1,80})\]\(\s*(https?://[^)\s]+)\s*\)")
HTML_CODE = re.compile(r"(?is)<(pre|code)[\s>]")
INSTALL_CMD = re.compile(
    r"(?i)\b(?:pip3?|uv|poetry|conda|npm|pnpm|yarn|bun|cargo|go|dotnet|brew|apt|apt-get|docker)\s+"
    r"(?:install|add|run|get|build)\b"
)
USAGE_HINT = re.compile(
    r"(?is)(?:^|\n)\s*(?:>>>|\$ |>\s)?(?:import\s+[A-Za-z_]|from\s+[A-Za-z_.]+\s+import|"
    r"python\s+-m\s|python3?\s+[\w./-]+\.py|node\s+[\w./-]+\.js|uv\s+run|npx\s)"
)

SECTION_WEIGHTS: list[tuple[re.Pattern[str], int]] = [
    (re.compile(r"quick\s*start|quickstart|getting\s*started|快速开始|快速上手|快速入门", re.I), 6),
    (re.compile(r"install|安装|部署|setup", re.I), 5),
    (re.compile(r"example|sample|demo|示例|样例", re.I), 5),
    (re.compile(r"usage|how\s*to\s*use|用法|使用|tutorial|教程|guide|指南", re.I), 4),
    (re.compile(r"requirement|prerequisit|dependency|要求|前置|依赖", re.I), 3),
    (re.compile(r"limitation|known\s*issue|faq|roadmap|限制|已知问题|常见问题", re.I), 3),
    (re.compile(r"next\s*steps?|下一步|后续", re.I), 3),
    (re.compile(r"introduction|overview|about|what\s*is|why\b|features?|core\s*concepts?", re.I), 3),
]

ENTRY_HINTS = re.compile(
    r"example|sample|quickstart|quick-start|get(?:ting)?[-_ ]?started|tutorials?|guide|docs?/|/docs|手册",
    re.I,
)

BADGE_LINE = re.compile(r"^\s*(\[!\[|<img|<div|</div|!\[|\[[^\]]*\]\([^)]*\)\s*$)", re.I)


def strip_badge_lines(text: str) -> str:
    """Remove badges, images, and link-only lines that crowd out informative head excerpts."""
    kept: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            kept.append(line)
            continue
        if BADGE_LINE.match(stripped):
            continue
        if stripped.startswith("<!--") or stripped.count("![") >= 2:
            continue
        kept.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(kept))


@dataclass
class Section:
    title: str
    level: int
    start_line: int
    end_line: int
    text: str


def _trim(text: str, limit: int) -> str:
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "…（节选）"


def split_sections(text: str) -> list[Section]:
    lines = text.splitlines()
    headings: list[tuple[int, int, str]] = []
    in_fence = False
    for index, line in enumerate(lines):
        if FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = HEADING.match(line.strip())
        if match:
            headings.append((index, len(match.group(1)), match.group(2).strip()))
    sections: list[Section] = []
    first_heading = headings[0][0] if headings else len(lines)
    if first_heading > 0:
        body = "\n".join(lines[:first_heading]).strip()
        if body:
            sections.append(Section("（前言）", 0, 1, first_heading, body))
    for position, (index, level, title) in enumerate(headings):
        end = headings[position + 1][0] if position + 1 < len(headings) else len(lines)
        body = "\n".join(lines[index:end]).strip()
        if body:
            sections.append(Section(title, level, index + 1, end, body))
    return sections


def score_section(section: Section) -> int:
    if section.level == 0:
        return 1
    best = 0
    for pattern, weight in SECTION_WEIGHTS:
        if pattern.search(section.title):
            best = max(best, weight)
    return best


def _windowed_fallback(text: str, budget: int) -> str:
    """When no relevant section exists, sample head/middle/tail with character positions."""
    span = max(budget // 3 - 40, 200)
    starts = [0, max(len(text) // 2 - span // 2, 0), max(len(text) - span, 0)]
    seen: set[int] = set()
    parts: list[str] = []
    for start in starts:
        if start in seen:
            continue
        seen.add(start)
        chunk = _trim(text[start : start + span], span)
        if chunk:
            parts.append(f"[片段：字符 {start}-{start + span}]\n{chunk}")
    return "\n\n".join(parts)


def select_excerpt(
    text: str,
    *,
    head_chars: int = 500,
    per_section: int = 900,
    max_sections: int = 4,
    budget: int = 3200,
) -> tuple[str, list[dict[str, Any]]]:
    """Return excerpt and selected section headings/line locators."""
    if not text:
        return "", []
    sections = split_sections(text)
    scored = [section for section in sections if score_section(section) >= 3]
    scored.sort(key=lambda item: (-score_section(item), item.start_line))
    parts: list[str] = []
    used: list[dict[str, Any]] = []

    remaining = budget
    # Use badge-free text for the head excerpt; retain original sections for line locators.
    head = _trim(strip_badge_lines(text)[:head_chars], head_chars)
    if head and remaining > 0:
        parts.append(head)
        remaining -= len(head)

    for section in scored[:max_sections]:
        if remaining <= 0:
            break
        chunk = _trim(section.text, min(per_section, remaining))
        if not chunk:
            continue
        parts.append(f"[章节：{section.title}（行 {section.start_line}-{section.end_line}）]\n{chunk}")
        used.append({"title": section.title, "start_line": section.start_line, "end_line": section.end_line})
        remaining -= len(chunk)

    if not used:
        return _windowed_fallback(strip_badge_lines(text), budget), []
    return "\n\n".join(parts), used


def section_locator(used: list[dict[str, Any]]) -> str:
    if not used:
        return "未匹配到快速开始/示例等章节，按开头/中间/结尾三段取样"
    return "章节：" + "；".join(
        f"{item['title']} 行 {item['start_line']}-{item['end_line']}" for item in used
    )


def extract_links(text: str) -> list[tuple[str, str]]:
    return [(match.group(1).strip(), match.group(2).strip()) for match in LINK.finditer(text or "")]


def analyze_example_content(text: str) -> dict[str, Any]:
    """Detect code blocks plus installation commands or runnable snippets.
An example signal does not prove successful execution; this layer runs no examples."""
    body = text or ""
    fences = len(re.findall(r"(?m)^\s*(?:```|~~~)", body))
    html_blocks = len(HTML_CODE.findall(body))
    code_blocks = fences // 2 + html_blocks
    has_install = bool(INSTALL_CMD.search(body))
    has_usage = bool(USAGE_HINT.search(body))
    signals: list[str] = []
    if code_blocks:
        signals.append(f"代码块 {code_blocks} 处")
    if has_install:
        signals.append("安装命令")
    if has_usage:
        signals.append("可运行的使用片段")
    return {
        "example_signal": bool(code_blocks > 0 and (has_install or has_usage)),
        "signals": signals,
        "code_blocks": code_blocks,
    }


def analyze_readme_entry(text: str, repo: str) -> dict[str, Any]:
    """Signals in the retained README excerpt, never an installation result."""
    result = analyze_example_content(text)
    if result['example_signal']:
        return result
    # Colab tutorials can provide numbered setup/run steps and inline install
    # commands without fenced code. Require a notebook in this same repository,
    # multiple actual steps, setup prerequisites and an explicit cell-run action.
    prefix = 'https://colab.research.google.com/github/' + repo.lower() + '/blob/'
    notebooks = re.findall(r'https://colab\.research\.google\.com/github/[^\s)<>]+\.ipynb', text, re.I)
    steps = len(re.findall(r'(?m)^\s*\d+[.)]\s+\S', text))
    run_cells = re.search(r'run\s+(?:[^\n]{0,50})?(?:top\s+to\s+bottom|all\s+(?:the\s+)?cells)', text, re.I)
    prerequisites = re.search(r'API\s+key|secret|prerequisite|前置条件|密钥', text, re.I)
    if (any(url.lower().startswith(prefix) for url in notebooks)
            and steps >= 2 and run_cells and prerequisites):
        result['example_signal'] = True
        result['signals'].append('同仓库Colab notebook链接与编号前置/运行步骤（未运行）')
    return result


GITHUB_BLOB = re.compile(
    r"https?://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/(?:blob|tree)/[^/]+/(?P<path>[^\s)#?]+)",
    re.I,
)
CODE_EXT = (
    ".py", ".ts", ".js", ".tsx", ".jsx", ".java", ".cs", ".go", ".rs", ".rb", ".kt",
    ".md", ".txt", ".json", ".toml", ".yaml", ".yml", ".sh", ".ipynb",
)
EXAMPLE_PATH = re.compile(r"example|sample|demo|quickstart|getting-started|tutorial", re.I)


NOISE_FILE = re.compile(
    r"(?:^|/)(contributing|code_of_conduct|code-of-conduct|changelog|changes|history|license|licence|"
    r"security|support|authors|notice|governance|roadmap|sponsors?)(?:\.[a-z]+)?$",
    re.I,
)


def example_file_links(
    readme_text: str, *, limit: int = 2, skipped: list[dict[str, str]] | None = None
) -> list[tuple[str, str]]:
    """Find concrete example/source files through explicit README links.
Positive relevance is a filter, not verification that a candidate runs."""
    found: list[tuple[int, int, str, str]] = []
    seen: set[str] = set()
    for order, (label, url) in enumerate(extract_links(readme_text)):
        reason = unrequestable_reason(url)
        if reason:
            _record_skipped(skipped, url, reason)
            continue
        match = GITHUB_BLOB.search(url)
        if not match:
            continue
        path = match.group("path").strip("/")
        if not path or path in seen or not path.lower().endswith(CODE_EXT):
            continue
        if NOISE_FILE.search(path):
            continue
        seen.add(path)
        score = 0
        if EXAMPLE_PATH.search(path):
            score += 3
        if EXAMPLE_PATH.search(label or ""):
            score += 2
        if EXAMPLE_PATH.search(match.group("repo")):
            score += 1
        if score <= 0:
            continue
        found.append((-score, order, path, url))
    found.sort()
    return [(item[2], item[3]) for item in found[:limit]]


DOC_SECTION = re.compile(
    r"getting\s*started|documentation|docs?\b|usage|tutorials?|guides?|install|入门|文档|教程|使用",
    re.I,
)
EXAMPLE_SECTION = re.compile(r"examples?|samples?|demos?|示例|样例", re.I)
NAV_PATH = re.compile(r"/(category|tags?|search|archive)(/|$)", re.I)


def pick_entry_candidates(
    repo, readme_text, *, limit: int = 4, skipped: list[dict[str, str]] | None = None
):
    # Classify docs and examples entry links and record their containing sections.
    homepage = str(repo.get("homepage") or "").strip()
    sections = split_sections(readme_text)

    def section_of(url: str) -> str:
        for section in sections:
            if url in section.text:
                return section.title
        return ""

    candidates = []
    seen = set()
    for order, (label, url) in enumerate(extract_links(readme_text)):
        reason = unrequestable_reason(url)
        if reason:
            _record_skipped(skipped, url, reason)
            continue
        if url in seen:
            continue
        seen.add(url)
        title = section_of(url)
        score = 0
        if ENTRY_HINTS.search(url):
            score += 3
        if ENTRY_HINTS.search(label or ""):
            score += 2
        if homepage and url.startswith(homepage.rstrip("/")):
            score += 2
        if score <= 0:
            continue
        context = f"{title} {label} {url}"
        if NAV_PATH.search(url):
            score -= 2
        if DOC_SECTION.search(context):
            purpose = "docs"
        elif EXAMPLE_SECTION.search(context):
            purpose = "examples"
        else:
            purpose = "other"
        candidates.append(
            {"url": url, "label": label, "section": title, "purpose": purpose,
             "score": -score, "order": order}
        )
    candidates.sort(key=lambda item: (item["score"], item["order"]))
    result = candidates[:limit]
    if homepage.startswith("http") and not any(item["url"] == homepage for item in result):
        result = result[: max(0, limit - 1)] + [
            {"url": homepage, "label": "仓库主页", "section": "", "purpose": "docs",
             "score": 0, "order": 10_000}
        ]
    return result


def pick_learning_entries(repo: dict[str, Any], readme_text: str, *, limit: int = 3) -> list[str]:
    """Rank entry candidates by quick-start/example relevance, falling back to the repository page."""
    homepage = str(repo.get("homepage") or "").strip()
    scored: list[tuple[int, int, str]] = []
    for order, (label, url) in enumerate(extract_links(readme_text)):
        score = 0
        if ENTRY_HINTS.search(url):
            score += 3
        if ENTRY_HINTS.search(label):
            score += 2
        if re.search(r"example|quickstart|getting-started|tutorial", url, re.I):
            score += 2
        if homepage and url.startswith(homepage.rstrip("/")):
            score += 2
        if score:
            scored.append((-score, order, url))
    scored.sort()
    urls: list[str] = []
    for _, _, url in scored:
        if url not in urls:
            urls.append(url)
    if homepage.startswith("http") and homepage not in urls:
        urls.append(homepage)
    return urls[:limit]


def pick_learning_entry(repo: dict[str, Any], readme_text: str) -> str:
    """Return the highest-priority entry or an empty string without inventing links."""
    entries = pick_learning_entries(repo, readme_text, limit=1)
    return entries[0] if entries else ""


ANCHOR_RAW = re.compile(r'(?is)<a\s[^>]*href="([^"]+)"[^>]*>(.*?)</a>')
DOC_KEYWORDS = re.compile(
    r"get(?:ting)?[-_ ]?started|quickstart|quick[-_ ]?start|tutorials?|guides?|concepts?|"
    r"overview|architecture|introduction|install|文档|入门|教程",
    re.I,
)
ASSET_EXT = (".png", ".jpg", ".jpeg", ".svg", ".gif", ".css", ".js", ".ico", ".webp")


def _same_site(left: str, right: str) -> bool:
    """Allow the same site and its subdomains only."""
    import urllib.parse as _u

    host_left = _u.urlsplit(left).netloc.lower()
    host_right = _u.urlsplit(right).netloc.lower()
    if not host_left or not host_right:
        return False
    return (
        host_left == host_right
        or host_left.endswith("." + host_right)
        or host_right.endswith("." + host_left)
    )


def _record_skipped(
    skipped: list[dict[str, str]] | None, url: str, reason: str
) -> None:
    """Record rejected links within a bounded diagnostic list."""
    if skipped is None or len(skipped) >= 20:
        return
    skipped.append({"url": str(url)[:200], "reason": reason})


def docs_followup_links(
    raw_html: str,
    base_url: str,
    *,
    limit: int = 1,
    skipped: list[dict[str, str]] | None = None,
) -> list[tuple[str, str]]:
    """Extract same-site introduction/mechanism links from official docs, excluding assets and anchors."""
    import urllib.parse as _u

    found: list[tuple[int, int, str, str]] = []
    seen: set[str] = set()
    for order, match in enumerate(ANCHOR_RAW.finditer(raw_html or "")):
        href = (match.group(1) or "").strip()
        label = re.sub(r"(?s)<[^>]+>", " ", match.group(2) or "")
        label = html.unescape(label).strip()
        if not href or href.startswith(("#", "mailto:", "javascript:")):
            continue
        url = _u.urljoin(base_url, href)
        reason = unrequestable_reason(url)
        if reason:
            # Documentation may contain unexpanded template expressions.
            # Skip and record them instead of constructing fabricated URLs.
            _record_skipped(skipped, url, reason)
            continue
        parts = _u.urlsplit(url)
        if parts.scheme not in ("http", "https"):
            continue
        if parts.path.lower().endswith(ASSET_EXT):
            continue
        if not _same_site(base_url, url):
            continue
        if url in seen or url.rstrip("/") == base_url.rstrip("/"):
            continue
        score = 0
        if DOC_KEYWORDS.search(parts.path):
            score += 3
        if DOC_KEYWORDS.search(label):
            score += 2
        if score <= 0:
            continue
        if NAV_PATH.search(parts.path):
            score -= 2
        if parts.path.count('/') >= 3:
            score += 1
        seen.add(url)
        found.append((-score, order, url, label))
    found.sort()
    return [(item[2], item[3]) for item in found[:limit]]
