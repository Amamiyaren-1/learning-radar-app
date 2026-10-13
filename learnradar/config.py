"""配置加载：模型名称、推理参数、价格、限额、模型角色全部来自配置文件，代码里不写死。

模型角色（role）把"业务流程里的位置"和"具体供应商/模型"解耦：
[models] 下除了数组 candidate 以外的每个字符串键就是一个角色，
例如 screening = "flash"、analysis = "pro"，值为候选名。
业务流程只按角色取模型，替换模型只需改配置。
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent

KNOWN_ROLES = ("screening", "analysis")


class ConfigError(RuntimeError):
    """配置缺失或非法。"""


def load_dotenv(path: Path, *, override: bool = False) -> dict[str, str]:
    """极简 .env 解析：只支持 KEY=VALUE 与 # 注释。默认不覆盖已存在的环境变量。"""
    loaded: dict[str, str] = {}
    if not path.exists():
        return loaded
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        loaded[key] = value
        if override or key not in os.environ:
            os.environ[key] = value
    return loaded


@dataclass
class CandidateModel:
    name: str
    label: str
    provider: str
    model_id: str
    base_url: str = ""
    api_key_env: str = ""
    reasoning_effort: str = ""
    temperature: float = -1.0
    max_output_tokens: int = 4096
    timeout_seconds: int = 180
    probe_completion: bool = False
    json_mode: bool = True
    follow_redirects: bool = False
    pricing_input_per_mtok: float | None = None
    pricing_output_per_mtok: float | None = None
    pricing_currency: str = "USD"
    extra_body: dict[str, Any] = field(default_factory=dict)
    source_file: str = ""

    @property
    def is_fixture(self) -> bool:
        return self.provider == "offline_fixture"

    def api_key(self) -> str:
        if not self.api_key_env:
            return ""
        return os.environ.get(self.api_key_env, "").strip()

    def pricing_known(self) -> bool:
        return bool(self.pricing_input_per_mtok or self.pricing_output_per_mtok)

    def cost(self, input_tokens: int | None, output_tokens: int | None) -> float | None:
        """按配置单价估算费用；单价未知时返回 None（显示“费用未知”，不显示 0）。"""
        if not self.pricing_known():
            return None
        cost = 0.0
        if input_tokens and self.pricing_input_per_mtok:
            cost += input_tokens / 1_000_000 * self.pricing_input_per_mtok
        if output_tokens and self.pricing_output_per_mtok:
            cost += output_tokens / 1_000_000 * self.pricing_output_per_mtok
        return round(cost, 6)

    def describe(self) -> str:
        return f"{self.label} [{self.provider}:{self.model_id}]"


@dataclass
class Settings:
    root: Path
    app: dict[str, Any]
    models: dict[str, CandidateModel]
    roles: dict[str, str]
    limits: dict[str, Any]
    selection: dict[str, Any]
    http: dict[str, Any]
    schedule: dict[str, Any]
    topics: list[dict[str, Any]]
    profile_path: Path
    profile_example_path: Path
    prompts_dir: Path
    raw_path: Path
    discovery: dict[str, Any] = field(default_factory=dict)

    @property
    def data_dir(self) -> Path:
        directory = Path(self.app.get("data_dir", "data"))
        return directory if directory.is_absolute() else (self.root / directory)

    def model_for(self, role: str) -> CandidateModel:
        """按角色取模型；角色未配置或指向不存在的候选时明确报错，不做静默替换。"""
        name = self.roles.get(role)
        if not name:
            raise ConfigError(
                f"没有配置模型角色 {role!r}；请在 settings.toml 的 [models] 下写 {role} = \"<候选名>\""
            )
        candidate = self.models.get(name)
        if candidate is None:
            raise ConfigError(
                f"角色 {role!r} 指向的候选 {name!r} 不存在；可选：{', '.join(sorted(self.models))}"
            )
        return candidate

    @property
    def analysis(self) -> CandidateModel:
        """向后兼容：analysis 角色的模型。"""
        return self.model_for("analysis")

    def role_of(self, candidate_name: str) -> list[str]:
        return sorted(role for role, name in self.roles.items() if name == candidate_name)

    def user_agent(self) -> str:
        return str(self.app.get("user_agent", "learning-radar/0.1"))

    def limit(self, key: str, default: int) -> int:
        value = self.limits.get(key, default)
        try:
            return int(value)
        except (TypeError, ValueError):
            return default


def _candidate_from(raw: dict[str, Any], source_file: str) -> CandidateModel:
    known = {
        "name", "label", "provider", "model_id", "base_url", "api_key_env",
        "reasoning_effort", "temperature", "max_output_tokens", "timeout_seconds",
        "probe_completion", "json_mode", "follow_redirects", "pricing_input_per_mtok",
        "pricing_output_per_mtok", "pricing_currency",
    }
    extra = {key: value for key, value in raw.items() if key not in known}
    pricing_in = raw.get("pricing_input_per_mtok") or None
    pricing_out = raw.get("pricing_output_per_mtok") or None
    return CandidateModel(
        name=str(raw["name"]),
        label=str(raw.get("label", raw["name"])),
        provider=str(raw.get("provider", "chat_completions")),
        model_id=str(raw.get("model_id", "")),
        base_url=str(raw.get("base_url", "")).rstrip("/"),
        api_key_env=str(raw.get("api_key_env", "")),
        reasoning_effort=str(raw.get("reasoning_effort", "")),
        temperature=float(raw.get("temperature", -1.0)),
        max_output_tokens=int(raw.get("max_output_tokens", 4096)),
        timeout_seconds=int(raw.get("timeout_seconds", 180)),
        probe_completion=bool(raw.get("probe_completion", False)),
        json_mode=bool(raw.get("json_mode", True)),
        follow_redirects=bool(raw.get("follow_redirects", False)),
        pricing_input_per_mtok=float(pricing_in) if pricing_in else None,
        pricing_output_per_mtok=float(pricing_out) if pricing_out else None,
        pricing_currency=str(raw.get("pricing_currency", "USD")),
        extra_body=extra,
        source_file=source_file,
    )


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _roles_from(models_table: dict[str, Any]) -> dict[str, str]:
    return {
        key: str(value)
        for key, value in models_table.items()
        if key != "candidate" and isinstance(value, str)
    }


def load_settings(root: Path | None = None, *, local_overrides: bool = True) -> Settings:
    root = Path(root or REPO_ROOT).resolve()
    config_dir = root / "config"
    settings_path = config_dir / "settings.toml"
    external_local = os.environ.get("LEARNRADAR_SETTINGS_LOCAL", "") if local_overrides else ""
    local_path = Path(external_local) if external_local else config_dir / "settings.local.toml"

    if local_overrides:
        load_dotenv(root / ".env")
        if external_local and not local_path.is_file():
            raise ConfigError("显式的本地配置文件缺失")

    if not settings_path.exists():
        raise ConfigError(f"缺少配置文件：{settings_path}")

    raw: dict[str, Any] = tomllib.loads(settings_path.read_text(encoding="utf-8"))
    if local_overrides and local_path.exists():
        raw = _deep_merge(raw, tomllib.loads(local_path.read_text(encoding="utf-8")))

    models_table = raw.get("models", {})
    models: dict[str, CandidateModel] = {}
    for item in models_table.get("candidate", []):
        candidate = _candidate_from(item, str(settings_path))
        models[candidate.name] = candidate
    if not models:
        raise ConfigError("settings.toml 里没有定义任何 [[models.candidate]]")

    roles = _roles_from(models_table)
    if not roles:
        raise ConfigError(
            "settings.toml 的 [models] 里没有配置模型角色；"
            '至少需要 screening = "<候选名>" 与 analysis = "<候选名>"'
        )

    topics: list[dict[str, Any]] = []
    topics_path = config_dir / "topics.toml"
    if topics_path.exists():
        topics = tomllib.loads(topics_path.read_text(encoding="utf-8")).get("topic", [])

    return Settings(
        root=root,
        app=raw.get("app", {}),
        models=models,
        roles=roles,
        limits=raw.get("limits", {}),
        selection=raw.get("selection", {}),
        http=raw.get("http", {}),
        schedule=raw.get("schedule", {}),
        topics=topics,
        profile_path=Path(os.environ["LEARNRADAR_PROFILE"]) if local_overrides and os.environ.get("LEARNRADAR_PROFILE") else config_dir / "profile.toml",
        profile_example_path=config_dir / "profile.example.toml",
        prompts_dir=root / "prompts",
        raw_path=settings_path,
        discovery=raw.get('discovery', {}),
    )


def load_profile(settings: Settings) -> tuple[dict[str, Any], bool, Path]:
    """返回 (档案, 是否为示例占位, 路径)。真实档案缺失时回退示例并标记未确认。"""
    if settings.profile_path.exists():
        return tomllib.loads(settings.profile_path.read_text(encoding="utf-8")), False, settings.profile_path
    return (
        tomllib.loads(settings.profile_example_path.read_text(encoding="utf-8")),
        True,
        settings.profile_example_path,
    )
