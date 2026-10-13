"""README 章节选取、学习入口识别与"入口里是否真有可执行示例"的检查。

固定截取开头会把"快速开始"这类真正决定能不能上手的材料丢掉，
所以这里按标题切分、按用途打分，在有界预算内挑选相关章节，并保留章节与行号定位。
另外：能打开一个页面不等于找到了可学步骤（例如打开 docs overview），
因此还要在页面里检查是否真的存在可执行示例，检查不到就如实标记。
"""

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
    """去掉徽章/图片/纯链接行：它们会占满 head 窗口，把真正的说明文字挤出摘录。"""
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
    """没有任何相关章节时，均匀取开头/中间/结尾三段，并标出字符位置。"""
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
    """返回 (摘录, 用到的章节列表)。章节列表带标题与行号，用于定位。"""
    if not text:
        return "", []
    sections = split_sections(text)
    scored = [section for section in sections if score_section(section) >= 3]
    scored.sort(key=lambda item: (-score_section(item), item.start_line))
    parts: list[str] = []
    used: list[dict[str, Any]] = []

    remaining = budget
    # head 窗口取"去掉徽章后的正文"，避免第一屏全是徽章；章节提取仍用原文以保留行号
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
    """检查页面里是否出现示例信号：代码块 + 安装命令或可直接运行的使用片段。

    注意：这只是"检测到示例信号"，不等于已经跑通，本层不实际运行任何示例。
    """
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
    """沿 README 的明确链接找示例/源码文件；**只接受与示例相关的候选**，不凑数量。

    得分大于零只是"与示例/样例/教程相关"的筛选条件，不代表已验证可运行。
    """
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
    # 按用途给入口链接分档：docs（入门/文档）与 examples（示例），并记录所属章节
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
    """按确定规则挑候选学习入口（越像"示例/快速开始"越靠前），取不到就返回仓库主页。"""
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
    """单入口版本：返回最优先的候选（没有就返回空字符串，不猜链接）。"""
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
    """只允许同站（含其子域），避免跳到站外。"""
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
    """把被拒绝的链接记下来（有上限，避免刷屏）。"""
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
    """从官方文档页提取"入门/核心机制"链接：仅同站、排除资源与锚点。"""
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
            # 例如文档站里未展开的 {{ meta.url | default(url) | safeUrl }}：
            # 既不是地址，也不该被编码成假地址去请求，直接跳过并记下原因。
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
