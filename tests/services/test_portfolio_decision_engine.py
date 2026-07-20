from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from services.execution.decision_engine import DecisionEngine
from services.execution.portfolio_decision import PortfolioCandidate, resolve_correlation_conflicts
from shared.models import (
    ExchangeSide,
    MarketRegime,
    PortfolioDecision,
    PositionSide,
    ProtectionPolicy,
    RuntimeMode,
    StrategySignal,
    TradeAction,
)


def test_highly_correlated_opposite_candidate_rejects_weaker_signal() -> None:
    decisions = resolve_correlation_conflicts(
        candidates=[
            PortfolioCandidate(symbol="BTC/USDT", side=PositionSide.LONG, score=Decimal("82")),
            PortfolioCandidate(symbol="ETH/USDT", side=PositionSide.SHORT, score=Decimal("71")),
        ],
        aligned_closes={
            "BTC/USDT": [100, 101, 102, 103, 104],
            "ETH/USDT": [200, 202, 204, 206, 208],
        },
        correlation_threshold=Decimal("0.90"),
    )

    assert decisions["BTC/USDT"].accepted is True
    assert decisions["ETH/USDT"].accepted is False
    assert decisions["ETH/USDT"].final_side is PositionSide.FLAT


def test_replay_and_shadow_emit_same_execution_intent_bytes() -> None:
    signal = StrategySignal(
        decision_id="decision-1",
        symbol="BTC/USDT:USDT",
        side=PositionSide.LONG,
        score=Decimal("80"),
        regime=MarketRegime.BULL,
        signal_candle_close_time=datetime(2026, 7, 20, 7, 0, tzinfo=UTC),
        strategy_id="trend_pullback_v1",
        strategy_version="1.0.0-research",
    )
    portfolio = PortfolioDecision(
        decision_id="decision-1",
        symbol=signal.symbol,
        raw_side=PositionSide.LONG,
        final_side=PositionSide.LONG,
        accepted=True,
    )
    engine = DecisionEngine()
    common = {
        "cycle_id": "cycle-1",
        "signal": signal,
        "portfolio": portfolio,
        "config_snapshot_id": "config-1",
        "config_hash": "sha256:config",
        "quantity": Decimal("0.003"),
        "reference_price": Decimal("65000"),
        "protection": ProtectionPolicy(stop_price=Decimal("64000"), take_profit_price=Decimal("67000")),
        "action": TradeAction.OPEN,
        "exchange_side": ExchangeSide.BUY,
    }

    replay = engine.build_intent(adapter_mode=RuntimeMode.REPLAY, **common)
    shadow = engine.build_intent(adapter_mode=RuntimeMode.SHADOW, **common)

    assert engine.execution_intent_bytes(replay) == engine.execution_intent_bytes(shadow)
