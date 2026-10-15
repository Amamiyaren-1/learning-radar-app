"""Restricted public HTTP(S) transport.
Reject private, loopback, link-local, reserved, and metadata addresses, including redirect targets.
Reject HTTPS downgrade; strip sensitive headers across origins.
Reject cross-origin redirects with POST/PUT/PATCH bodies to protect profiles.
allow_redirects=False rejects all redirects for model APIs.
Audit URL, status, elapsed time, and bytes; retry 429/5xx only within limits and Retry-After."""

from __future__ import annotations

import hashlib
import http.client
import ipaddress
import json
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

BLOCKED_HOSTS = {
    "localhost",
    "metadata",
    "metadata.google.internal",
    "instance-data",
}
BLOCKED_SUFFIXES = (".localhost", ".local", ".internal", ".home.arpa", ".localdomain")
REDIRECT_CODES = {301, 302, 303, 307, 308}
RETRYABLE_CODES = {408, 425, 429, 500, 502, 503, 504}
MAX_REDIRECTS = 5
BACKOFF_CAP = 20.0
SENSITIVE_HEADERS = {
    "authorization",
    "cookie",
    "proxy-authorization",
    "www-authenticate",
    "x-api-key",
    "api-key",
    "x-auth-token",
}
BODY_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


class FetchError(RuntimeError):
    """Fetch failed or was rejected by network safety rules."""


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class FetchResult:
    url: str
    final_url: str
    status: int
    headers: dict[str, str]
    body: bytes
    retrieved_at: str
    sha256: str
    content_type: str
    attempts: int
    redirects: int = 0

    @property
    def size(self) -> int:
        return len(self.body)

    def text(self, encoding: str = "utf-8") -> str:
        return self.body.decode(encoding, errors="replace")

    def json(self) -> Any:
        return json.loads(self.text())


def _ip_blocked(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast:
        return True
    if ip.is_reserved or ip.is_unspecified:
        return True
    if ip.version == 4:
        for net in ("100.64.0.0/10", "192.0.0.0/24", "198.18.0.0/15"):
            if ip in ipaddress.ip_network(net):
                return True
    elif ip.ipv4_mapped and _ip_blocked(ip.ipv4_mapped):
        return True
    return False


# Unexpanded templates, whitespace, and control characters are not requestable URLs.
# Reject placeholders instead of URL-encoding them into fabricated addresses.
TEMPLATE_MARKER = re.compile(r"\{\{|\}\}|\{%|%\}|\$\{|<%")
_CONTROL_CHARS = re.compile(r"[\x00-\x20\x7f]")


def unrequestable_reason(url: str) -> str:
    """Return a rejection reason or an empty string; inspect the address without requesting it."""
    text = str(url or "")
    if not text.strip():
        return "地址为空"
    if TEMPLATE_MARKER.search(text):
        return "地址里含未展开的模板占位符（例如 {{…}}）"
    if _CONTROL_CHARS.search(text):
        return "地址里含空格或控制字符"
    return ""

def assert_public_url(url: str) -> str:
    """Require a URL resolving to public addresses or raise FetchError."""
    reason = unrequestable_reason(url)
    if reason:
        raise FetchError(f"{reason}，已拒绝请求：{str(url)[:120]}")
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise FetchError(f"只允许 http/https：{url}")
    host = (parts.hostname or "").lower()
    if not host:
        raise FetchError(f"URL 缺少主机名：{url}")
    if host in BLOCKED_HOSTS or host.endswith(BLOCKED_SUFFIXES):
        raise FetchError(f"拒绝访问内网/元数据主机：{host}")
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        if _ip_blocked(literal):
            raise FetchError(f"拒绝访问非公网 IP：{host}")
        return url
    port = parts.port or (443 if parts.scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise FetchError(f"域名解析失败：{host}（{exc}）") from exc
    if not infos:
        raise FetchError(f"域名无解析结果：{host}")
    for info in infos:
        address = info[4][0]
        try:
            ip = ipaddress.ip_address(address)
        except ValueError as exc:
            raise FetchError(f"无法解析地址：{address}") from exc
        if _ip_blocked(ip):
            raise FetchError(f"域名 {host} 解析到非公网地址 {address}，已拒绝")
    return url


def origin_of(url: str) -> tuple[str, str, int]:
    parts = urllib.parse.urlsplit(url)
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    port = parts.port or (443 if scheme == "https" else 80)
    return scheme, host, port


def is_same_origin(left: str, right: str) -> bool:
    return origin_of(left) == origin_of(right)


def _strip_sensitive(headers: dict[str, str]) -> tuple[dict[str, str], list[str]]:
    kept: dict[str, str] = {}
    dropped: list[str] = []
    for key, value in headers.items():
        if key.lower() in SENSITIVE_HEADERS:
            dropped.append(key)
            continue
        kept[key] = value
    return kept, dropped


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def fetch(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: float = 30.0,
    max_bytes: int = 2_000_000,
    retries: int = 2,
    audit: list[dict[str, Any]] | None = None,
    method: str = "GET",
    body: bytes | None = None,
    allow_redirects: bool = True,
) -> FetchResult:
    """Read a public URL; return FetchResult or raise FetchError."""
    attempts = 0
    redirects = 0
    current = url
    current_headers = dict(headers or {})
    current_method = method.upper()
    current_body = body
    while True:
        attempts += 1
        assert_public_url(current)
        try:
            request = urllib.request.Request(current, method=current_method, data=current_body)
        except (http.client.InvalidURL, ValueError, UnicodeError) as exc:
            # Request-construction failures remain explicit fetch failures even after URL checks.
            # Do not let an uncaught construction error abort the whole task.
            raise FetchError(f"无法构造请求（地址不合法）：{exc}") from exc
        header_map = {"User-Agent": "learning-radar/0.1", **current_headers}
        for key, value in header_map.items():
            request.add_header(key, value)
        started = time.monotonic()
        record: dict[str, Any] = {"ts": now_iso(), "method": current_method, "url": current}
        try:
            with _OPENER.open(request, timeout=timeout) as response:
                raw = response.read(max_bytes + 1)
                status = int(response.status)
                response_headers = dict(response.headers.items())
                final_url = response.geturl()
        except http.client.InvalidURL as exc:
            # http.client can reject whitespace/control characters during request transmission.
            # This explicit URL failure is not retryable and must not be sent.
            record.update({"status": 0, "ms": 0, "bytes": 0, "error": f"InvalidURL: {exc}"})
            if audit is not None:
                audit.append(record)
            raise FetchError(f"地址不合法，已拒绝请求：{exc}") from exc
        except (ValueError, UnicodeError) as exc:
            record.update({"status": 0, "ms": 0, "bytes": 0, "error": f"{type(exc).__name__}: {exc}"})
            if audit is not None:
                audit.append(record)
            raise FetchError(f"地址不合法，已拒绝请求：{exc}") from exc
        except urllib.error.HTTPError as exc:
            status = int(exc.code)
            response_headers = dict(exc.headers.items()) if exc.headers else {}
            final_url = current
            try:
                raw = exc.read(max_bytes + 1) or b""
            except Exception:  # pragma: no cover - failure to read an error body does not change classification
                raw = b""
            exc.close()
        except (urllib.error.URLError, socket.timeout, OSError) as exc:
            elapsed = int((time.monotonic() - started) * 1000)
            record.update({"status": 0, "ms": elapsed, "bytes": 0, "error": f"{type(exc).__name__}: {exc}"})
            if audit is not None:
                audit.append(record)
            if attempts <= retries:
                time.sleep(min(2 ** (attempts - 1), BACKOFF_CAP))
                continue
            raise FetchError(f"读取 {current} 失败：{exc}") from exc

        elapsed = int((time.monotonic() - started) * 1000)
        record.update({"status": status, "ms": elapsed, "bytes": len(raw)})

        if status in REDIRECT_CODES:
            location = response_headers.get("Location") or response_headers.get("location")
            if not location:
                if audit is not None:
                    audit.append({**record, "error": f"redirect {status} 无 Location"})
                raise FetchError(f"{current} 返回 {status} 但没有 Location")
            new_url = urllib.parse.urljoin(current, location)
            if audit is not None:
                audit.append({**record, "error": f"redirect {status} -> {new_url}"})
            if not allow_redirects:
                raise FetchError(
                    f"{current} 返回 {status}，要求跳转到 {new_url}；"
                    "该请求被配置为不跟随重定向，请直接配置最终端点"
                )
            redirects += 1
            if redirects > MAX_REDIRECTS:
                raise FetchError(f"重定向次数过多：{url}")
            old_scheme, _, _ = origin_of(current)
            new_scheme, _, _ = origin_of(new_url)
            if old_scheme == "https" and new_scheme == "http":
                raise FetchError(f"拒绝 HTTPS 降级重定向：{current} -> {new_url}")
            if not is_same_origin(current, new_url):
                if current_method in BODY_METHODS:
                    raise FetchError(
                        f"跨源重定向 {current} -> {new_url} 会转发请求正文与凭据，已拒绝；"
                        "请直接配置最终端点"
                    )
                current_headers, dropped = _strip_sensitive(current_headers)
                if dropped and audit is not None:
                    audit.append(
                        {
                            "ts": now_iso(),
                            "method": current_method,
                            "url": new_url,
                            "status": status,
                            "ms": 0,
                            "bytes": 0,
                            "error": f"跨源重定向：已移除敏感头 {', '.join(dropped)}",
                        }
                    )
            if status == 303 or (status in (301, 302) and current_method in BODY_METHODS):
                current_method = "GET"
                current_body = None
            current = new_url
            attempts -= 1
            continue

        if status in RETRYABLE_CODES and attempts <= retries:
            if audit is not None:
                audit.append({**record, "error": f"retryable {status}"})
            delay = _retry_after_seconds(
                response_headers.get("Retry-After") or response_headers.get("retry-after")
            )
            time.sleep(delay if delay is not None else min(2 ** (attempts - 1), BACKOFF_CAP))
            continue

        if audit is not None:
            audit.append(record)

        if status >= 400:
            raise FetchError(f"{current} 返回 HTTP {status}")

        if len(raw) > max_bytes:
            raw = raw[:max_bytes]
        return FetchResult(
            url=url,
            final_url=final_url,
            status=status,
            headers=response_headers,
            body=raw,
            retrieved_at=now_iso(),
            sha256=hashlib.sha256(raw).hexdigest(),
            content_type=response_headers.get("Content-Type", response_headers.get("content-type", "")),
            attempts=attempts,
            redirects=redirects,
        )


def _retry_after_seconds(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, min(float(value), BACKOFF_CAP))
    except ValueError:
        return None


def fetch_json(url: str, **kwargs: Any) -> tuple[Any, FetchResult]:
    result = fetch(url, **kwargs)
    try:
        return result.json(), result
    except json.JSONDecodeError as exc:
        raise FetchError(f"{url} 返回的不是合法 JSON：{exc}") from exc


def post_json(
    url: str,
    payload: dict[str, Any],
    *,
    headers: dict[str, str] | None = None,
    timeout: float = 60.0,
    audit: list[dict[str, Any]] | None = None,
    retries: int = 0,
    allow_redirects: bool = False,
) -> tuple[Any, FetchResult]:
    """POST JSON without redirects by default; model callers configure the final endpoint explicitly."""
    merged = {"Content-Type": "application/json", **(headers or {})}
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    result = fetch(
        url,
        headers=merged,
        timeout=timeout,
        method="POST",
        body=body,
        audit=audit,
        retries=retries,
        allow_redirects=allow_redirects,
    )
    try:
        return result.json(), result
    except json.JSONDecodeError as exc:
        raise FetchError(
            f"{url} 返回的不是合法 JSON（前 300 字节：{result.text()[:300]!r}）"
        ) from exc


def audit_summary(audit: list[dict[str, Any]]) -> dict[str, Any]:
    ok = [item for item in audit if item.get("status") and int(item["status"]) < 400]
    return {
        "requests": len(audit),
        "ok": len(ok),
        "failed": len(audit) - len(ok),
        "bytes": sum(int(item.get("bytes") or 0) for item in audit),
    }
