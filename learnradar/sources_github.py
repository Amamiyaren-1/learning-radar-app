"""Official GitHub evidence adapter.
Search repositories and read metadata, README, commits, and releases from GitHub endpoints.
Audit every call for consumption inspection.
api uses REST core quota; optional read-only tokens raise limits.
public falls back to search metadata, raw README, and Atom commit/release feeds.
The fallback lacks release bodies and may pin README by HEAD/commit instead of API blob SHA."""

from __future__ import annotations

import base64
import re
import urllib.parse
from dataclasses import dataclass, field
from typing import Any

from .net import FetchError, fetch, fetch_json

API_ROOT = "https://api.github.com"
API_VERSION = "2022-11-28"
RAW_ROOT = "https://raw.githubusercontent.com"
README_CANDIDATES = ("README.md", "readme.md", "README.rst", "README.txt", "README")


def _route_of(payload: Any, fallback: str) -> str:
    if isinstance(payload, dict):
        return str(payload.get("_evidence_route") or fallback)
    return fallback


@dataclass
class GitHubClient:
    token: str = ""
    timeout: float = 30.0
    user_agent: str = "learning-radar/0.1"
    audit: list[dict[str, Any]] = field(default_factory=list)
    max_bytes: int = 2_000_000
    retries: int = 2
    mode: str = "api"
    notes: list[str] = field(default_factory=list)

    # ------------------------------------------------------------ Modes and quotas
    def preflight(self) -> dict[str, Any]:
        """Inspect /rate_limit to choose api or public evidence mode."""
        try:
            payload, _ = self._get("/rate_limit")
        except FetchError as exc:
            self.notes.append(f"限额预检失败，先按 api 模式尝试：{exc}")
            return {"mode": self.mode, "error": str(exc)}
        core = (payload.get("resources") or {}).get("core") or {}
        remaining = int(core.get("remaining") or 0)
        limit = int(core.get("limit") or 0)
        if remaining <= 0:
            self.mode = "public"
            self.notes.append(
                f"GitHub 核心限额已用尽（剩余 {remaining}/{limit}），改用 public 模式："
                "元数据走搜索接口、README 走 raw、提交与发布走 atom 订阅源"
            )
        return {"mode": self.mode, "core_limit": limit, "core_remaining": remaining}

    def _mark_exhausted(self, error: Exception) -> None:
        text = str(error).lower()
        if ("403" in text or "429" in text) and self.mode != "public":
            self.mode = "public"
            self.notes.append("核心接口返回 403/429，已切换到 public 模式继续取证")

    # ------------------------------------------------------------ HTTP requests
    def _headers(self, accept: str = "application/vnd.github+json") -> dict[str, str]:
        headers = {
            "Accept": accept,
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": self.user_agent,
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _get(self, path: str, params: dict[str, Any] | None = None, *, accept: str = "application/vnd.github+json"):
        url = f"{API_ROOT}{path}"
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        return fetch_json(
            url,
            headers=self._headers(accept),
            timeout=self.timeout,
            max_bytes=self.max_bytes,
            retries=self.retries,
            audit=self.audit,
        )

    def rate_limit(self) -> dict[str, Any]:
        payload, _ = self._get("/rate_limit")
        return payload

    # ------------------------------------------------------------ Search
    def search_repositories(self, query: str, *, per_page: int = 10, sort: str = "stars") -> list[dict[str, Any]]:
        payload, _ = self._get(
            "/search/repositories",
            {"q": query, "sort": sort, "order": "desc", "per_page": min(per_page, 50)},
        )
        items = list(payload.get("items") or [])
        for item in items:
            item["_evidence_route"] = "search-api:/search/repositories"
        return items

    def search_repository_metadata(self, full_name: str) -> dict[str, Any] | None:
        payload, _ = self._get("/search/repositories", {"q": f"repo:{full_name}", "per_page": 5})
        items = list(payload.get("items") or [])
        for item in items:
            item["_evidence_route"] = "search-api:repo:{owner}/{repo}"
            if str(item.get("full_name", "")).lower() == full_name.lower():
                return item
        if items:
            items[0]["_evidence_route"] = "search-api:repo:{owner}/{repo}（未精确匹配）"
            return items[0]
        return None

    # ------------------------------------------------------------ Repository metadata
    def get_repository(self, full_name: str) -> dict[str, Any]:
        if self.mode == "api":
            try:
                payload, _ = self._get(f"/repos/{full_name}")
                payload["_evidence_route"] = "core-api:/repos/{owner}/{repo}"
                return payload
            except FetchError as exc:
                self._mark_exhausted(exc)
        found = self.search_repository_metadata(full_name)
        if found is None:
            raise FetchError(f"无法取得 {full_name} 的仓库元数据（api 与搜索接口都失败）")
        return found

    # ------------------------------------------------------------ README
    def get_readme(self, full_name: str, *, ref: str | None = None) -> tuple[str, dict[str, Any]]:
        if self.mode == "api":
            try:
                return self._get_readme_api(full_name, ref)
            except FetchError as exc:
                self._mark_exhausted(exc)
        return self._get_readme_raw(full_name, ref)

    def _get_readme_api(self, full_name: str, ref: str | None) -> tuple[str, dict[str, Any]]:
        params = {"ref": ref} if ref else None
        payload, _ = self._get(f"/repos/{full_name}/readme", params)
        content = payload.get("content") or ""
        encoding = payload.get("encoding") or "base64"
        text = base64.b64decode(content).decode("utf-8", errors="replace") if encoding == "base64" else content
        payload["_evidence_route"] = "core-api:/repos/{owner}/{repo}/readme"
        return text, payload

    def _get_readme_raw(self, full_name: str, ref: str | None) -> tuple[str, dict[str, Any]]:
        revision = ref or "HEAD"
        last_error: Exception | None = None
        for name in README_CANDIDATES:
            url = f"{RAW_ROOT}/{full_name}/{revision}/{name}"
            try:
                result = fetch(
                    url,
                    headers={"User-Agent": self.user_agent, "Accept": "text/plain"},
                    timeout=self.timeout,
                    max_bytes=self.max_bytes,
                    retries=self.retries,
                    audit=self.audit,
                )
            except FetchError as exc:
                last_error = exc
                continue
            return result.text(), {
                "path": name,
                "html_url": f"https://github.com/{full_name}#readme",
                "download_url": url,
                "_evidence_route": f"raw.githubusercontent.com/{name}@{revision}",
            }
        raise FetchError(f"public 模式下没有取到 {full_name} 的 README：{last_error}")

    # ------------------------------------------------------------ Concrete files
    def fetch_repo_file(self, full_name: str, path: str, *, ref: str | None = None) -> str:
        """Read a concrete repository file through the raw endpoint pinned to a commit."""
        revision = ref or "HEAD"
        result = fetch(
            f"{RAW_ROOT}/{full_name}/{revision}/{path.lstrip('/')}",
            headers={"User-Agent": self.user_agent, "Accept": "text/plain"},
            timeout=self.timeout,
            max_bytes=self.max_bytes,
            retries=self.retries,
            audit=self.audit,
        )
        return result.text()

    # ------------------------------------------------------------ Commits
    def latest_commits(self, full_name: str, *, ref: str | None = None, per_page: int = 3) -> list[dict[str, Any]]:
        if self.mode == "api":
            try:
                return self._latest_commits_api(full_name, ref, per_page)
            except FetchError as exc:
                self._mark_exhausted(exc)
        return self._latest_commits_atom(full_name, ref)

    def _latest_commits_api(self, full_name: str, ref: str | None, per_page: int) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"per_page": per_page}
        if ref:
            params["sha"] = ref
        payload, _ = self._get(f"/repos/{full_name}/commits", params)
        commits = list(payload or [])
        for item in commits:
            item["_evidence_route"] = "core-api:/repos/{owner}/{repo}/commits"
        return commits

    def _latest_commits_atom(self, full_name: str, ref: str | None) -> list[dict[str, Any]]:
        branch = ref or "HEAD"
        result = fetch(
            f"https://github.com/{full_name}/commits/{branch}.atom",
            headers={"User-Agent": self.user_agent, "Accept": "application/atom+xml"},
            timeout=self.timeout,
            max_bytes=self.max_bytes,
            retries=self.retries,
            audit=self.audit,
        )
        entries: list[dict[str, Any]] = []
        for entry in re.findall(r"(?s)<entry>(.*?)</entry>", result.text()):
            sha_match = re.search(r"Commit/([0-9a-f]{7,40})", entry)
            if not sha_match:
                continue
            date_match = re.search(r"<updated>([^<]+)</updated>", entry)
            title_match = re.search(r"(?s)<title>(.*?)</title>", entry)
            entries.append(
                {
                    "sha": sha_match.group(1),
                    "commit": {
                        "committer": {"date": date_match.group(1) if date_match else None},
                        "message": title_match.group(1).strip() if title_match else "",
                    },
                    "_evidence_route": "github.com/{owner}/{repo}/commits/{branch}.atom",
                }
            )
        return entries

    # ------------------------------------------------------------ Releases
    def latest_release(self, full_name: str) -> dict[str, Any] | None:
        if self.mode == "api":
            try:
                payload, _ = self._get(f"/repos/{full_name}/releases", {"per_page": 5})
                return self._pick_release(list(payload or []))
            except FetchError as exc:
                self._mark_exhausted(exc)
        return self._latest_release_atom(full_name)

    def _pick_release(self, releases: list[dict[str, Any]]) -> dict[str, Any] | None:
        releases = [item for item in releases if not item.get("draft")]
        if not releases:
            return None
        releases.sort(key=lambda item: item.get("published_at") or "", reverse=True)
        release = releases[0]
        release["_evidence_route"] = "core-api:/repos/{owner}/{repo}/releases"
        return release

    def _latest_release_atom(self, full_name: str) -> dict[str, Any] | None:
        try:
            result = fetch(
                f"https://github.com/{full_name}/releases.atom",
                headers={"User-Agent": self.user_agent, "Accept": "application/atom+xml"},
                timeout=self.timeout,
                max_bytes=self.max_bytes,
                retries=self.retries,
                audit=self.audit,
            )
        except FetchError:
            return None
        match = re.search(r"(?s)<entry>(.*?)</entry>", result.text())
        if not match:
            return None
        block = match.group(1)
        title = re.search(r"(?s)<title>(.*?)</title>", block)
        updated = re.search(r"<updated>([^<]+)</updated>", block)
        link = re.search(r'<link[^>]*href="([^"]+)"', block)
        tag = title.group(1).strip() if title else ""
        return {
            "tag_name": tag,
            "name": tag,
            "published_at": updated.group(1) if updated else None,
            "prerelease": False,
            "draft": False,
            "body": "",
            "html_url": link.group(1) if link else f"https://github.com/{full_name}/releases",
            "_evidence_route": "github.com/{owner}/{repo}/releases.atom（未取发布说明正文）",
        }
