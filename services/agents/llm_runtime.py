"""Structured-output LLM runtime boundaries for online agents."""

from __future__ import annotations

import json
import time
from typing import Any, Protocol

import httpx

from services.agents.rag_context import collect_rag_snippets


class StructuredLLMRuntime(Protocol):
    def generate_structured(self, *, agent_type: str, task_type: str, payload: dict[str, Any]) -> dict[str, Any]: ...


class UnavailableLLMRuntime:
    def generate_structured(self, *, agent_type: str, task_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError(f"llm runtime unavailable for {agent_type}/{task_type}")


class LLMProviderUnavailable(RuntimeError):
    """Provider quota/auth/network failure that may be retried on a fallback candidate."""


class AnthropicStructuredLLMRuntime:
    """Anthropic Messages API runtime with strict JSON-only output parsing."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = "https://api.anthropic.com",
        timeout_seconds: float = 20.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    def generate_structured(self, *, agent_type: str, task_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        prompt = _build_prompt(agent_type=agent_type, task_type=task_type, payload=payload)
        request_payload = {
            "model": self.model,
            "max_tokens": 400,
            "system": prompt["system"],
            "messages": [{"role": "user", "content": prompt["user"]}],
        }
        try:
            with httpx.Client(
                base_url=self.base_url,
                timeout=self.timeout_seconds,
                transport=self.transport,
            ) as client:
                response = client.post(
                    "/v1/messages",
                    headers={
                        "x-api-key": self.api_key,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json=request_payload,
                )
                response.raise_for_status()
                body = response.json()
        except httpx.HTTPStatusError as exc:
            if _is_fallback_status(exc.response.status_code):
                raise LLMProviderUnavailable(
                    f"anthropic/{self.model} unavailable: HTTP {exc.response.status_code}"
                ) from exc
            raise
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            raise LLMProviderUnavailable(f"anthropic/{self.model} unavailable: {exc}") from exc
        raw_text = _extract_anthropic_text(body)
        try:
            raw_output = json.loads(_strip_code_fence(raw_text))
        except json.JSONDecodeError as exc:
            raise ValueError("llm response is not valid JSON") from exc
        if not isinstance(raw_output, dict):
            raise ValueError("llm response JSON must be an object")
        return {
            "provider": "anthropic",
            "model": self.model,
            "prompt_version": prompt["prompt_version"],
            "usage": {
                "prompt_tokens": int((body.get("usage") or {}).get("input_tokens") or 0),
                "completion_tokens": int((body.get("usage") or {}).get("output_tokens") or 0),
                "total_tokens": int((body.get("usage") or {}).get("input_tokens") or 0)
                + int((body.get("usage") or {}).get("output_tokens") or 0),
            },
            "raw_output": raw_output,
        }


class OpenAICompatibleStructuredLLMRuntime:
    """OpenAI chat-completions compatible runtime for OpenRouter and GitHub Models."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str,
        provider_label: str,
        timeout_seconds: float = 20.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.provider_label = provider_label
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    def generate_structured(self, *, agent_type: str, task_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        prompt = _build_prompt(agent_type=agent_type, task_type=task_type, payload=payload)
        request_payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": prompt["system"]},
                {"role": "user", "content": prompt["user"]},
            ],
            "max_tokens": 400,
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }
        if self.provider_label == "openrouter":
            # Free reasoning models (e.g. nvidia/nemotron-3-*) can spend the entire
            # max_tokens budget on the hidden reasoning trace, then dump that
            # incomplete trace verbatim into `content` on finish_reason="length" --
            # a non-JSON response that looks like a model failure but is actually a
            # truncation artifact. Excluding reasoning output keeps the full budget
            # for the actual JSON answer and removes this failure mode entirely
            # (verified across a heavier real-signal payload with 0/4 failures vs.
            # 2/4 without it).
            request_payload["reasoning"] = {"exclude": True}
        try:
            with httpx.Client(
                base_url=self.base_url,
                timeout=self.timeout_seconds,
                transport=self.transport,
            ) as client:
                response = client.post(
                    "/chat/completions",
                    headers={
                        "authorization": f"Bearer {self.api_key}",
                        "content-type": "application/json",
                    },
                    json=request_payload,
                )
                response.raise_for_status()
                body = response.json()
        except httpx.HTTPStatusError as exc:
            if _is_fallback_status(exc.response.status_code):
                raise LLMProviderUnavailable(
                    f"{self.provider_label}/{self.model} unavailable: HTTP {exc.response.status_code}"
                ) from exc
            raise
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            raise LLMProviderUnavailable(f"{self.provider_label}/{self.model} unavailable: {exc}") from exc
        raw_text = _extract_openai_chat_text(body)
        try:
            raw_output = json.loads(_strip_code_fence(raw_text))
        except json.JSONDecodeError as exc:
            raise ValueError("llm response is not valid JSON") from exc
        if not isinstance(raw_output, dict):
            raise ValueError("llm response JSON must be an object")
        return {
            "provider": self.provider_label,
            "model": self.model,
            "prompt_version": prompt["prompt_version"],
            "usage": {
                "prompt_tokens": int((body.get("usage") or {}).get("prompt_tokens") or 0),
                "completion_tokens": int((body.get("usage") or {}).get("completion_tokens") or 0),
                "total_tokens": int((body.get("usage") or {}).get("total_tokens") or 0),
            },
            "raw_output": raw_output,
        }


class FallbackChainStructuredLLMRuntime:
    """Try structured LLM candidates in order for quota/auth/server/transient failures."""

    def __init__(self, runtimes: list[StructuredLLMRuntime]) -> None:
        self.runtimes = runtimes

    def generate_structured(self, *, agent_type: str, task_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.runtimes:
            raise LLMProviderUnavailable("no llm fallback candidates configured")
        failures: list[str] = []
        for runtime in self.runtimes:
            try:
                return runtime.generate_structured(agent_type=agent_type, task_type=task_type, payload=payload)
            except LLMProviderUnavailable as exc:
                failures.append(str(exc))
                continue
            except ValueError as exc:
                # A single candidate returning a malformed/non-JSON body (e.g. a
                # free model truncated mid-response) must not abort the whole
                # chain -- try the remaining candidates instead of failing closed
                # on the first bad response.
                failures.append(f"malformed response: {exc}")
                continue
        raise LLMProviderUnavailable("; ".join(failures) or "all llm fallback candidates exhausted")


class ConfiguredStructuredLLMRuntime:
    """Select provider/model per agent while preserving a single runtime boundary."""

    def __init__(
        self,
        *,
        anthropic_api_key: str,
        default_model: str,
        provider_by_agent: dict[str, str] | None = None,
        model_by_agent: dict[str, str] | None = None,
        anthropic_base_url: str = "https://api.anthropic.com",
        timeout_seconds: float = 20.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.anthropic_api_key = anthropic_api_key
        self.default_model = default_model
        self.provider_by_agent = provider_by_agent or {}
        self.model_by_agent = model_by_agent or {}
        self.anthropic_base_url = anthropic_base_url
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    def generate_structured(self, *, agent_type: str, task_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        provider = self.provider_by_agent.get(agent_type, "anthropic")
        model = self.model_by_agent.get(agent_type, self.default_model)
        if provider != "anthropic":
            raise RuntimeError(f"unsupported llm provider configured for {agent_type}: {provider}")
        runtime = AnthropicStructuredLLMRuntime(
            api_key=self.anthropic_api_key,
            model=model,
            base_url=self.anthropic_base_url,
            timeout_seconds=self.timeout_seconds,
            transport=self.transport,
        )
        return runtime.generate_structured(agent_type=agent_type, task_type=task_type, payload=payload)


_MODEL_CATALOG_CACHE: dict[str, tuple[float, list[str]]] = {}


def parse_model_override(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def discover_openrouter_free_models(
    *,
    api_key: str,
    cache_seconds: int = 21600,
    transport: httpx.BaseTransport | None = None,
) -> list[str]:
    return _cached_catalog(
        cache_key="openrouter",
        cache_seconds=cache_seconds,
        fetch=lambda: _fetch_openrouter_free_models(api_key=api_key, transport=transport),
        seeds=[
            "meta-llama/llama-3.1-8b-instruct:free",
            "google/gemma-3-27b-it:free",
        ],
    )


def discover_github_models_free_models(
    *,
    token: str,
    cache_seconds: int = 21600,
    transport: httpx.BaseTransport | None = None,
) -> list[str]:
    return _cached_catalog(
        cache_key="github_models",
        cache_seconds=cache_seconds,
        fetch=lambda: _fetch_github_models(token=token, transport=transport),
        seeds=[
            "openai/gpt-4.1-nano",
            "meta/Meta-Llama-3.1-8B-Instruct",
        ],
    )


def _build_prompt(*, agent_type: str, task_type: str, payload: dict[str, Any]) -> dict[str, str]:
    prompt_version = "v1"
    if task_type == "classify_event":
        return {
            "prompt_version": prompt_version,
            "system": (
                "You classify market-relevant events. Return JSON only with keys: severity, summary, risk_tags."
            ),
            "user": json.dumps(
                {
                    "agent_type": agent_type,
                    "task_type": task_type,
                    "payload": payload,
                    "schema": {
                        "severity": "low|medium|high|critical",
                        "summary": "string",
                        "risk_tags": ["string"],
                    },
                },
                ensure_ascii=True,
            ),
        }
    if task_type == "pre_execution_veto_llm":
        enriched = dict(payload)
        if "rag_snippets" not in enriched:
            strategy = enriched.get("strategy", {})
            if isinstance(strategy, dict):
                enriched["rag_snippets"] = collect_rag_snippets(
                    core_thesis=str(strategy.get("core_thesis", "")),
                    entry_rules=strategy.get("entry_rules") if isinstance(strategy.get("entry_rules"), dict) else {},
                    symbol=str(enriched.get("symbol", "")),
                )
        payload = enriched
        return {
            "prompt_version": prompt_version,
            "system": (
                "You are a strict execution veto agent. "
                "Never suggest direction, price, or position size. "
                "Return JSON only with keys: veto, veto_reason."
            ),
            "user": json.dumps(
                {
                    "agent_type": agent_type,
                    "task_type": task_type,
                    "payload": payload,
                    "schema": {"veto": "boolean", "veto_reason": "string"},
                },
                ensure_ascii=True,
            ),
        }
    if task_type in {"market_review_llm", "trade_review_llm"}:
        review_scope = (
            "market-wide BTC/ETH USDT-M context"
            if task_type == "market_review_llm"
            else "a single candidate trade decision"
        )
        return {
            "prompt_version": prompt_version,
            "system": (
                f"You are an advisory review agent for {review_scope}. "
                "Do not create candidates, sizes, leverage, or absolute SL/TP. "
                "Return JSON only with keys: bias, confidence, risk_flags, summary."
            ),
            "user": json.dumps(
                {
                    "agent_type": agent_type,
                    "task_type": task_type,
                    "payload": payload,
                    "schema": {
                        "bias": "support|neutral|oppose",
                        "confidence": "number 0..1",
                        "risk_flags": ["string"],
                        "summary": "string",
                    },
                },
                ensure_ascii=True,
            ),
        }
    raise ValueError(f"unsupported llm task: {agent_type}/{task_type}")


def _extract_anthropic_text(body: dict[str, Any]) -> str:
    content = body.get("content", [])
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            text = block.get("text", "")
            if isinstance(text, str) and text.strip():
                return text
    raise ValueError("llm response did not include a text content block")


def _extract_openai_chat_text(body: dict[str, Any]) -> str:
    choices = body.get("choices", [])
    for choice in choices:
        if not isinstance(choice, dict):
            continue
        message = choice.get("message", {})
        if isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, str) and content.strip():
                return content
    raise ValueError("llm response did not include a chat message content block")


def _strip_code_fence(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
    return cleaned.strip()


def _is_fallback_status(status_code: int) -> bool:
    return status_code in {401, 403, 429} or status_code >= 500


def _cached_catalog(
    *,
    cache_key: str,
    cache_seconds: int,
    fetch,
    seeds: list[str],
) -> list[str]:
    now = time.time()
    cached = _MODEL_CATALOG_CACHE.get(cache_key)
    if cached is not None and now - cached[0] < cache_seconds:
        return cached[1]
    try:
        models = fetch()
    except Exception:
        models = []
    if not models:
        models = seeds
    _MODEL_CATALOG_CACHE[cache_key] = (now, models)
    return models


def _fetch_openrouter_free_models(
    *,
    api_key: str,
    transport: httpx.BaseTransport | None = None,
) -> list[str]:
    with httpx.Client(base_url="https://openrouter.ai/api/v1", timeout=10.0, transport=transport) as client:
        response = client.get("/models", headers={"authorization": f"Bearer {api_key}"})
        response.raise_for_status()
        body = response.json()
    data = body.get("data", [])
    models: list[str] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        model_id = str(item.get("id", ""))
        pricing = item.get("pricing", {})
        prompt_price = str(pricing.get("prompt", "")) if isinstance(pricing, dict) else ""
        completion_price = str(pricing.get("completion", "")) if isinstance(pricing, dict) else ""
        explicitly_free = prompt_price in {"0", "0.0"} and completion_price in {"0", "0.0"}
        if model_id and (model_id.endswith(":free") or explicitly_free):
            models.append(model_id)
    return models


def _fetch_github_models(
    *,
    token: str,
    transport: httpx.BaseTransport | None = None,
) -> list[str]:
    with httpx.Client(base_url="https://models.github.ai", timeout=10.0, transport=transport) as client:
        response = client.get("/catalog/models", headers={"authorization": f"Bearer {token}"})
        response.raise_for_status()
        body = response.json()
    data = body.get("models", body if isinstance(body, list) else [])
    models: list[str] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        model_id = str(item.get("id") or item.get("name") or "")
        if model_id:
            models.append(model_id)
    return models
