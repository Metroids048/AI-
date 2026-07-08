from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import patch

import httpx
import pytest

from services.agents.llm_factory import build_configured_llm_runtime
from services.agents.llm_runtime import (
    AnthropicStructuredLLMRuntime,
    FallbackChainStructuredLLMRuntime,
    LLMProviderUnavailable,
    OpenAICompatibleStructuredLLMRuntime,
)
from services.execution.paper_signal import PaperSignalGenerator
from shared.config import settings
from shared.models import (
    BacktestRun,
    FundingArbitrageSignal,
    GateDecision,
    OHLCVBar,
    PaperRun,
    PaperRunStepRequest,
    StrategyCreate,
)


def test_anthropic_runtime_raises_provider_unavailable_on_rate_limit() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": "rate limited"})

    runtime = AnthropicStructuredLLMRuntime(
        api_key="test-key",
        model="claude-sonnet-test",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(LLMProviderUnavailable):
        runtime.generate_structured(
            agent_type="news_agent",
            task_type="classify_event",
            payload={"headline": "macro"},
        )


def test_anthropic_fallback_to_openrouter_in_chain() -> None:
    def anthropic_handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": "rate limited"})

    def openrouter_handler(request: httpx.Request) -> httpx.Response:
        assert request.url == httpx.URL("https://openrouter.ai/api/v1/chat/completions")
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": '{"veto":false,"veto_reason":"ok"}',
                        }
                    }
                ]
            },
        )

    chain = FallbackChainStructuredLLMRuntime(
        [
            AnthropicStructuredLLMRuntime(
                api_key="test-key",
                model="claude-sonnet-test",
                transport=httpx.MockTransport(anthropic_handler),
            ),
            OpenAICompatibleStructuredLLMRuntime(
                api_key="openrouter-key",
                model="meta-llama/llama-3.1-8b-instruct:free",
                base_url="https://openrouter.ai/api/v1",
                provider_label="openrouter",
                transport=httpx.MockTransport(openrouter_handler),
            ),
        ]
    )

    result = chain.generate_structured(
        agent_type="decision_veto_agent",
        task_type="pre_execution_veto_llm",
        payload={"symbol": "BTC/USDT", "strategy": {"core_thesis": "macd trend", "entry_rules": {}}},
    )

    assert result["provider"] == "openrouter"
    assert result["raw_output"]["veto"] is False


def test_build_configured_llm_runtime_uses_free_model_chain_without_claude(monkeypatch) -> None:
    monkeypatch.setattr(settings, "claude_api_key", "")
    monkeypatch.setattr(settings, "openrouter_api_key", "or-key")
    monkeypatch.setattr(settings, "openrouter_free_models", "openrouter/free-model")
    monkeypatch.setattr(settings, "github_models_token", "gh-key")
    monkeypatch.setattr(settings, "github_models_free_models", "github/free-model")

    runtime = build_configured_llm_runtime()
    assert isinstance(runtime, FallbackChainStructuredLLMRuntime)
    assert len(runtime.runtimes) == 2


def test_carry_decision_rejects_negative_net_edge(db_session) -> None:
    from services.strategy_library import PaperRunRepository, StrategyRepository, ValidationRepository

    strategy = StrategyRepository(db_session).create_strategy(
        StrategyCreate(
            strategy_key="carry_admission_test",
            source="test",
            core_thesis="Funding carry admission test",
            rules={
                "entry_rules": {
                    "funding_threshold_bps": 0.5,
                    "min_estimated_net_edge_bps": 10.0,
                    "requires_positive_funding": True,
                },
                "exit_rules": {},
                "stoploss_rules": {"fixed_bps": 250},
                "takeprofit_rules": {"risk_reward": 2.0},
                "position_rules": {"notional_usdt": 100},
            },
        )
    )
    backtest = ValidationRepository(db_session).create_backtest_run(
        BacktestRun(
            strategy_id=strategy.strategy_id,
            execution_engine="test",
            eligibility_result=GateDecision(
                strategy_id=strategy.strategy_id,
                passed=True,
                decision_status="accepted",
                reason="test",
            ),
        )
    )
    paper_run = PaperRunRepository(db_session).create_paper_run(
        PaperRun(
            strategy_id=strategy.strategy_id,
            gate_decision_ref=backtest.backtest_run_id,
            execution_profile={"strategy_lane": "carry"},
            paper_status="running",
        )
    )
    db_session.commit()

    now = datetime.now(UTC)
    bar = OHLCVBar(
        symbol="BTC/USDT",
        timeframe="1h",
        timestamp=now,
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100"),
        volume=Decimal("1"),
    )
    perp_bar = OHLCVBar(
        symbol="BTC/USDT:USDT",
        timeframe="1h",
        timestamp=now,
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100.1"),
        volume=Decimal("1"),
    )
    from services.data.repository import DataRepository

    data_repo = DataRepository(db_session)
    data_repo.store_ohlcv_bars([bar, perp_bar])
    db_session.commit()

    rejected_signal = FundingArbitrageSignal(
        symbol="BTC/USDT",
        perp_symbol="BTC/USDT:USDT",
        funding_rate=Decimal("0.0001"),
        funding_bps=1.0,
        basis_bps=2.0,
        fee_bps=8.0,
        slippage_bps=6.0,
        estimated_net_edge_bps=-11.0,
        should_enter_paper=False,
        rejection_reasons=["negative_net_edge"],
    )

    generator = PaperSignalGenerator(data_repo=data_repo)
    with patch("services.execution.paper_signal.MarketQueryService.get_funding_arbitrage_signal", return_value=rejected_signal):
        order = generator.generate_order(
            paper_run=paper_run,
            strategy=strategy,
            request=PaperRunStepRequest(symbol="BTC/USDT", timeframe="1h", enable_decision_veto=False),
            positions=[],
        )

    trace = order.entry_context["decision_pipeline"]
    assert trace["pipeline_status"] == "funding_arbitrage_rejected"
    assert order.entry_context["paper_order_should_trade"] is False
    assert "negative_net_edge" in trace["rejection_reasons"]
