"""Structured-output LLM runtime boundaries for online agents."""

from __future__ import annotations

import json
from typing import Any, Protocol

import httpx


class StructuredLLMRuntime(Protocol):
    def generate_structured(self, *, agent_type: str, task_type: str, payload: dict[str, Any]) -> dict[str, Any]: ...


class UnavailableLLMRuntime:
    def generate_structured(self, *, agent_type: str, task_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError(f"llm runtime unavailable for {agent_type}/{task_type}")


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
            "raw_output": raw_output,
        }


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


def _build_prompt(*, agent_type: str, task_type: str, payload: dict[str, Any]) -> dict[str, str]:
    prompt_version = "v1"
    if task_type == "classify_event":
        return {
            "prompt_version": prompt_version,
            "system": (
                "You classify market-relevant events. "
                'Return JSON only with keys: severity, summary, risk_tags.'
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
        return {
            "prompt_version": prompt_version,
            "system": (
                "You are a strict execution veto agent. "
                "Never suggest direction, price, or position size. "
                'Return JSON only with keys: veto, veto_reason.'
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
    raise ValueError(f"unsupported llm task: {agent_type}/{task_type}")


def _extract_anthropic_text(body: dict[str, Any]) -> str:
    content = body.get("content", [])
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            text = block.get("text", "")
            if isinstance(text, str) and text.strip():
                return text
    raise ValueError("llm response did not include a text content block")


def _strip_code_fence(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
    return cleaned.strip()
