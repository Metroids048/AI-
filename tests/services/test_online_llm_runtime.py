from __future__ import annotations

import json

import httpx
import pytest

from services.agents.llm_runtime import (
    AnthropicStructuredLLMRuntime,
    FallbackChainStructuredLLMRuntime,
    LLMProviderUnavailable,
    OpenAICompatibleStructuredLLMRuntime,
)


def test_online_llm_runtime_parses_structured_json_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == httpx.URL("https://api.anthropic.com/v1/messages")
        payload = json.loads(request.content.decode("utf-8"))
        assert payload["model"] == "claude-sonnet-test"
        return httpx.Response(
            200,
            json={
                "content": [
                    {
                        "type": "text",
                        "text": '{"severity":"high","summary":"ETF headline risk","risk_tags":["news"]}',
                    }
                ]
            },
        )

    runtime = AnthropicStructuredLLMRuntime(
        api_key="test-key",
        model="claude-sonnet-test",
        transport=httpx.MockTransport(handler),
    )

    result = runtime.generate_structured(
        agent_type="news_agent",
        task_type="classify_event",
        payload={"headline": "ETF headline", "body": "volatility is rising"},
    )

    assert result["provider"] == "anthropic"
    assert result["model"] == "claude-sonnet-test"
    assert result["raw_output"]["severity"] == "high"


def test_online_llm_runtime_rejects_non_json_response() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"content": [{"type": "text", "text": "not-json"}]})

    runtime = AnthropicStructuredLLMRuntime(
        api_key="test-key",
        model="claude-sonnet-test",
        transport=httpx.MockTransport(handler),
    )

    try:
        runtime.generate_structured(
            agent_type="decision_veto_agent",
            task_type="pre_execution_veto_llm",
            payload={"headline": "exchange outage"},
        )
    except ValueError as exc:
        assert "valid JSON" in str(exc)
    else:  # pragma: no cover - explicit failure branch
        raise AssertionError("runtime should reject non-JSON responses")


def test_openai_compatible_runtime_parses_structured_json_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == httpx.URL("https://models.inference.ai.azure.com/chat/completions")
        payload = json.loads(request.content.decode("utf-8"))
        assert payload["model"] == "github-free-model"
        assert payload["response_format"] == {"type": "json_object"}
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": '{"veto":false,"veto_reason":"no material event risk"}',
                        }
                    }
                ]
            },
        )

    runtime = OpenAICompatibleStructuredLLMRuntime(
        api_key="github-models-token",
        model="github-free-model",
        base_url="https://models.inference.ai.azure.com",
        provider_label="github_models",
        transport=httpx.MockTransport(handler),
    )

    result = runtime.generate_structured(
        agent_type="decision_veto_agent",
        task_type="pre_execution_veto_llm",
        payload={"symbol": "BTC/USDT"},
    )

    assert result["provider"] == "github_models"
    assert result["model"] == "github-free-model"
    assert result["raw_output"]["veto"] is False


def test_fallback_chain_switches_after_provider_limit() -> None:
    class LimitedRuntime:
        def generate_structured(self, *, agent_type: str, task_type: str, payload: dict) -> dict:
            raise LLMProviderUnavailable("openrouter/free-model limited")

    class WorkingRuntime:
        def generate_structured(self, *, agent_type: str, task_type: str, payload: dict) -> dict:
            return {
                "provider": "github_models",
                "model": "github-free-model",
                "raw_output": {"severity": "medium", "summary": "ok", "risk_tags": ["macro"]},
            }

    chain = FallbackChainStructuredLLMRuntime([LimitedRuntime(), WorkingRuntime()])

    result = chain.generate_structured(
        agent_type="news_agent",
        task_type="classify_event",
        payload={"headline": "macro surprise"},
    )

    assert result["provider"] == "github_models"


def test_fallback_chain_raises_after_all_candidates_exhausted() -> None:
    class LimitedRuntime:
        def generate_structured(self, *, agent_type: str, task_type: str, payload: dict) -> dict:
            raise LLMProviderUnavailable("candidate exhausted")

    chain = FallbackChainStructuredLLMRuntime([LimitedRuntime(), LimitedRuntime()])

    with pytest.raises(LLMProviderUnavailable):
        chain.generate_structured(agent_type="news_agent", task_type="classify_event", payload={})
