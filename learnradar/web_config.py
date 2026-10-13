"""Explicit loopback development or HTTPS proxy policy; never inferred from headers."""
from dataclasses import dataclass
import ipaddress
import os
from pathlib import Path
import tomllib
from urllib.parse import urlsplit

from .config import ConfigError


@dataclass(frozen=True)
class WebConfig:
    mode: str
    public_origin: str = ""
    trusted_proxies: tuple[str, ...] = ()
    absolute_seconds: int = 43200
    idle_seconds: int = 1800
    prelogin_seconds: int = 600

    @property
    def secure(self):
        return self.mode == "https"

    @property
    def cookie_name(self):
        return "__Host-lr_session" if self.secure else "lr_session"

    def validate(self):
        if self.mode not in ("loopback", "https"):
            raise ConfigError("网页必须显式配置 loopback 或 https 模式")
        if not (60 <= self.idle_seconds <= self.absolute_seconds <= 604800) or not (60 <= self.prelogin_seconds <= 1800):
            raise ConfigError("网页会话有效期配置无效")
        if self.mode == "loopback":
            if self.public_origin or self.trusted_proxies:
                raise ConfigError("loopback 模式不能配置外部地址或可信代理")
        else:
            parsed = urlsplit(self.public_origin)
            if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
                raise ConfigError("https 模式需要不含路径的外部 HTTPS origin")
            try:
                parsed.port
                if not self.trusted_proxies or any(not ipaddress.ip_address(ip).is_loopback for ip in self.trusted_proxies):
                    raise ValueError()
            except ValueError:
                raise ConfigError("本轮只支持显式配置的回环可信代理") from None
        return self


def load_web_config(settings=None, *, mode=None, demo=False):
    if demo:
        if mode not in (None, "loopback"):
            raise ConfigError("fixture 网页只支持隔离回环演示")
        return WebConfig("loopback").validate()
    external = os.environ.get("LEARNRADAR_WEB_CONFIG", "")
    path = Path(external) if external else settings.root / "config/web.toml" if settings else None
    if external and not path.is_file():
        raise ConfigError("显式的网页安全配置文件缺失")
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8")).get("web", {}) if path and path.exists() else {}
        if not isinstance(raw, dict):
            raise ValueError()
    except (ValueError, OSError):
        raise ConfigError("网页安全配置无法读取或格式无效") from None
    configured_mode = mode or raw.get("mode", "")
    if not configured_mode:
        raise ConfigError("缺少网页安全配置；本机使用 --web-mode loopback，HTTPS 使用 config/web.toml")
    try:
        result = WebConfig(
            str(configured_mode), str(raw.get("public_origin", "")),
            tuple(raw.get("trusted_proxies", [])),
            int(raw.get("absolute_seconds", 43200)), int(raw.get("idle_seconds", 1800)),
            int(raw.get("prelogin_seconds", 600)),
        )
        return result.validate()
    except (TypeError, ValueError, tomllib.TOMLDecodeError):
        raise ConfigError("网页安全配置无效") from None
