from __future__ import annotations

import json

import httpx

from services.agents.llm_runtime import AnthropicStructuredLLMRuntime


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
