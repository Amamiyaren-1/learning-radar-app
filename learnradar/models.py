"""模型适配层。

规则（规划第 5 节）：
- 模型名、推理参数、超时、单价、是否跟随重定向全部来自 config/settings.toml；
- 第一层只实现一种真实接入方式 + 一个离线夹具，不做多模型自动切换；
- 模型 API 请求默认拒绝重定向（`follow_redirects = false`）：跨源跳转会把密钥与档案正文
  转发给新主机，因此宁可失败并要求用户直接配置最终端点；
- 对外只暴露 complete()（一次有界的文本补全）与 verify_candidate()（接入核验）。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Protocol

from .config import CandidateModel, REPO_ROOT, Settings
from .net import FetchError, audit_summary, fetch_json, post_json

DEFAULT_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "offline_analysis.json"


class ModelError(RuntimeError):
    """模型调用失败。"""


@dataclass
class Usage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "reasoning_tokens": self.reasoning_tokens,
        }


@dataclass
class ModelResponse:
    text: str
    usage: Usage
    model_id: str
    provider: str
    latency_ms: int
    endpoint: str
    raw: dict[str, Any]
    fixture: bool = False
    finish_reason: str = ""

    @property
    def truncated(self) -> bool:
        """输出是否因为达到上限而被截断（用于区分"该换策略"和"该修 JSON"）。"""
        return self.finish_reason in ("length", "max_tokens", "max_output_tokens")

    def usage_summary(self, candidate: CandidateModel) -> dict[str, Any]:
        cost = candidate.cost(self.usage.input_tokens, self.usage.output_tokens)
        return {
            "provider": self.provider,
            "model_id": self.model_id,
            "fixture": self.fixture,
            "input_tokens": self.usage.input_tokens,
            "output_tokens": self.usage.output_tokens,
            "reasoning_tokens": self.usage.reasoning_tokens,
            "latency_ms": self.latency_ms,
            "cost_value": cost,
            "cost_currency": candidate.pricing_currency if cost is not None else None,
            "pricing_known": candidate.pricing_known(),
            "requested_max_tokens": candidate.max_output_tokens,
            "finish_reason": self.finish_reason,
        }


class ModelAdapter(Protocol):
    candidate: CandidateModel

    def complete(self, *, system: str, user: str, max_output_tokens: int | None = None) -> ModelResponse:
        ...


class _HttpAdapter:
    """chat_completions / responses 两种 HTTP 形态的公共部分。"""

    def __init__(self, candidate: CandidateModel, *, audit: list[dict[str, Any]] | None = None) -> None:
        self.candidate = candidate
        self.audit = audit if audit is not None else []
        self.request_budget = None

    def bind_budget(self, budget) -> None:
        self.request_budget = budget

    def _headers(self) -> dict[str, str]:
        key = self.candidate.api_key()
        if not key:
            raise ModelError(
                f"候选 {self.candidate.name} 缺少密钥：环境变量 {self.candidate.api_key_env or '(未配置)'} 为空"
            )
        return {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _endpoint(self, path: str) -> str:
        if not self.candidate.base_url:
            raise ModelError(f"候选 {self.candidate.name} 未配置 base_url")
        return f"{self.candidate.base_url.rstrip('/')}/{path.lstrip('/')}"

    def _post(self, endpoint: str, body: dict[str, Any]) -> tuple[Any, Any]:
        headers = self._headers()  # 缺凭据不算请求；持久计数只发生在实际请求边界。
        attempt = self.request_budget.consume() if self.request_budget else None
        try:
            result = post_json(
                endpoint,
                body,
                headers=headers,
                timeout=self.candidate.timeout_seconds,
                audit=self.audit,
                allow_redirects=self.candidate.follow_redirects,
                retries=0,  # 模型请求不自动重试：一次 complete 对应一次请求，便于按次数计预算
            )
        except FetchError as exc:
            if hasattr(self.request_budget, "finish_request"):
                self.request_budget.finish_request(attempt, "unknown")
            raise ModelError(str(exc)) from exc
        if hasattr(self.request_budget, "finish_request"):
            self.request_budget.finish_request(attempt, "response_received")
        return result

    def _common_body(self, *, max_output_tokens: int | None) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.candidate.model_id,
            "max_tokens": max_output_tokens or self.candidate.max_output_tokens,
        }
        if self.candidate.temperature is not None and self.candidate.temperature >= 0:
            body["temperature"] = self.candidate.temperature
        if self.candidate.reasoning_effort:
            body["reasoning_effort"] = self.candidate.reasoning_effort
        body.update(self.candidate.extra_body)
        return body


class ChatCompletionsAdapter(_HttpAdapter):
    """OpenAI 兼容的 /chat/completions 形态。"""

    def complete(self, *, system: str, user: str, max_output_tokens: int | None = None) -> ModelResponse:
        body = self._common_body(max_output_tokens=max_output_tokens)
        body["messages"] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        if self.candidate.json_mode:
            body.setdefault("response_format", {"type": "json_object"})
        endpoint = self._endpoint("chat/completions")
        started = time.monotonic()
        payload, result = self._post(endpoint, body)
        latency = int((time.monotonic() - started) * 1000)
        try:
            text = payload["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise ModelError(f"响应结构不符合 chat/completions 约定：{str(payload)[:300]}") from exc
        choices = payload.get("choices") or [{}]
        finish_reason = str((choices[0] or {}).get("finish_reason") or "")
        usage_raw = payload.get("usage") or {}
        details = usage_raw.get("completion_tokens_details") or {}
        return ModelResponse(
            text=text,
            usage=Usage(
                input_tokens=usage_raw.get("prompt_tokens"),
                output_tokens=usage_raw.get("completion_tokens"),
                reasoning_tokens=details.get("reasoning_tokens"),
                raw=usage_raw,
            ),
            model_id=str(payload.get("model") or self.candidate.model_id),
            provider=self.candidate.provider,
            latency_ms=latency,
            endpoint=result.final_url,
            raw=payload,
            finish_reason=finish_reason,
        )


class ResponsesAdapter(_HttpAdapter):
    """OpenAI /responses 形态。"""

    def complete(self, *, system: str, user: str, max_output_tokens: int | None = None) -> ModelResponse:
        body = self._common_body(max_output_tokens=max_output_tokens)
        body.pop("max_tokens", None)
        body["max_output_tokens"] = max_output_tokens or self.candidate.max_output_tokens
        body["input"] = [
            {"role": "system", "content": [{"type": "input_text", "text": system}]},
            {"role": "user", "content": [{"type": "input_text", "text": user}]},
        ]
        effort = self.candidate.reasoning_effort
        body.pop("reasoning_effort", None)
        if effort:
            body["reasoning"] = {"effort": effort}
        endpoint = self._endpoint("responses")
        started = time.monotonic()
        payload, result = self._post(endpoint, body)
        latency = int((time.monotonic() - started) * 1000)
        text = payload.get("output_text") or _extract_responses_text(payload)
        if not text:
            raise ModelError(f"响应中没有可读文本：{str(payload)[:300]}")
        status = str(payload.get("status") or "")
        incomplete = (payload.get("incomplete_details") or {}).get("reason") or ""
        finish_reason = "length" if status == "incomplete" else (str(incomplete) or status)
        usage_raw = payload.get("usage") or {}
        details = usage_raw.get("output_tokens_details") or {}
        return ModelResponse(
            text=text,
            usage=Usage(
                input_tokens=usage_raw.get("input_tokens"),
                output_tokens=usage_raw.get("output_tokens"),
                reasoning_tokens=details.get("reasoning_tokens"),
                raw=usage_raw,
            ),
            model_id=str(payload.get("model") or self.candidate.model_id),
            provider=self.candidate.provider,
            latency_ms=latency,
            endpoint=result.final_url,
            raw=payload,
            finish_reason=finish_reason,
        )


def _extract_responses_text(payload: dict[str, Any]) -> str:
    chunks: list[str] = []
    for item in payload.get("output") or []:
        for content in item.get("content") or []:
            if isinstance(content, dict) and content.get("text"):
                chunks.append(str(content["text"]))
    return "\n".join(chunks)


class OfflineFixtureAdapter:
    """离线夹具：读取本地 JSON，不产生任何真实调用。用于流程测试。"""

    def __init__(self, candidate: CandidateModel, fixture_path: Path | None = None) -> None:
        raw_path = candidate.extra_body.get("fixture_path")
        self.fixture_path = Path(raw_path) if raw_path else (fixture_path or DEFAULT_FIXTURE)
        self.candidate = candidate
        self.calls = 0

    def complete(self, *, system: str, user: str, max_output_tokens: int | None = None) -> ModelResponse:
        self.calls += 1
        if not self.fixture_path.exists():
            raise ModelError(f"离线夹具不存在：{self.fixture_path}")
        text = self.fixture_path.read_text(encoding="utf-8")
        return ModelResponse(
            text=text,
            usage=Usage(input_tokens=None, output_tokens=None, reasoning_tokens=None, raw={"fixture": True}),
            model_id=self.candidate.model_id,
            provider=self.candidate.provider,
            latency_ms=0,
            endpoint=f"fixture://{self.fixture_path.name}",
            raw={"fixture": str(self.fixture_path)},
            fixture=True,
        )


def build_adapter(
    candidate: CandidateModel,
    *,
    audit: list[dict[str, Any]] | None = None,
    fixture_path: Path | None = None,
) -> ModelAdapter:
    if candidate.provider == "chat_completions":
        return ChatCompletionsAdapter(candidate, audit=audit)
    if candidate.provider == "responses":
        return ResponsesAdapter(candidate, audit=audit)
    if candidate.provider == "offline_fixture":
        return OfflineFixtureAdapter(candidate, fixture_path=fixture_path)
    raise ModelError(f"未知的 provider：{candidate.provider}")


def verify_candidate(
    candidate: CandidateModel,
    settings: Settings,
    *,
    probe: bool | None = None,
) -> dict[str, Any]:
    """核验一个候选模型的实际可用接入，如实记录成功与失败。"""
    audit: list[dict[str, Any]] = []
    result: dict[str, Any] = {
        "name": candidate.name,
        "label": candidate.label,
        "provider": candidate.provider,
        "model_id": candidate.model_id,
        "base_url": candidate.base_url,
        "api_key_env": candidate.api_key_env,
        "api_key_present": bool(candidate.api_key()),
        "ok": False,
        "detail": "",
        "model_ids_seen": [],
        "model_id_in_list": None,
        "probe_ok": None,
        "probe_detail": "",
        "latency_ms": None,
    }
    timeout = float(settings.http.get("timeout_seconds", 30))

    if candidate.is_fixture:
        result.update(ok=True, detail="离线夹具：不产生真实模型调用，只用于流程与质量门槛测试")
        return result

    if not candidate.api_key():
        result["detail"] = f"缺少密钥：环境变量 {candidate.api_key_env or '(未配置)'} 为空"
        return result

    if candidate.base_url:
        try:
            payload, _ = fetch_json(
                f"{candidate.base_url.rstrip('/')}/models",
                headers={"Authorization": f"Bearer {candidate.api_key()}", "Accept": "application/json"},
                timeout=timeout,
                audit=audit,
            )
            items = payload.get("data") if isinstance(payload, dict) else None
            if isinstance(items, list):
                ids = [str(item.get("id")) for item in items if isinstance(item, dict) and item.get("id")]
            else:
                ids = [str(item) for item in (payload if isinstance(payload, list) else []) if item]
            result["model_ids_seen"] = ids
            result["model_id_in_list"] = candidate.model_id in ids
        except FetchError as exc:
            result["detail"] = f"/models 核验失败：{exc}"

    should_probe = candidate.probe_completion if probe is None else probe
    if should_probe:
        try:
            # 探针不使用 JSON 模式：各供应商对 response_format 支持不一致，
            # 探针只回答“这个 model_id 能否真的生成”。
            probe_candidate = replace(candidate, json_mode=False, temperature=-1.0)
            adapter = build_adapter(probe_candidate, audit=audit)
            response = adapter.complete(
                system="你是连通性测试端点，只回复 JSON。",
                user='只输出：{"ok": true}',
                max_output_tokens=32,
            )
            result["probe_ok"] = True
            result["probe_detail"] = f"返回 {len(response.text)} 字符；endpoint={response.endpoint}"
            result["latency_ms"] = response.latency_ms
        except (ModelError, FetchError) as exc:
            result["probe_ok"] = False
            result["probe_detail"] = str(exc)[:600]

    if result["probe_ok"] is True:
        result["ok"] = True
        result["detail"] = result["detail"] or "完成一次真实最小调用"
    elif result["probe_ok"] is False and should_probe:
        result["ok"] = False
    elif result["model_id_in_list"]:
        result["ok"] = True
        result["detail"] = result["detail"] or "模型 ID 存在于 /models 列表（未做生成调用核验）"
    else:
        result["ok"] = False
        result["detail"] = result["detail"] or "未能核验：没有密钥、列表查询失败且未开启生成调用核验"

    result["http"] = audit_summary(audit)
    result["http_log"] = audit
    return result
