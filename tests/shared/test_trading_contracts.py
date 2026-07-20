from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from shared.models.trading import (
    ConfigSnapshot,
    ExchangeSide,
    PositionSide,
    ProtectionPolicy,
    RuntimeMode,
    TradeAction,
    TradeIntent,
)


def test_config_snapshot_hash_is_canonical_and_immutable() -> None:
    first = ConfigSnapshot.create(
        paper_run_id="run-1",
        config={"risk": {"risk_fraction": "0.05"}, "symbols": ["BTC/USDT", "ETH/USDT"]},
        created_by="operator",
        effective_cycle_id="cycle-2",
    )
    second = ConfigSnapshot.create(
        paper_run_id="run-1",
        config={"symbols": ["BTC/USDT", "ETH/USDT"], "risk": {"risk_fraction": "0.05"}},
        created_by="operator",
        effective_cycle_id="cycle-2",
    )

    assert first.config_hash == second.config_hash
    with pytest.raises(ValidationError):
        first.config_hash = "changed"  # type: ignore[misc]


def test_config_snapshot_rejects_credentials_recursively() -> None:
    with pytest.raises(ValueError, match="secret-bearing"):
        ConfigSnapshot.create(
            paper_run_id="run-1",
            config={"execution": {"api_key": "must-not-persist"}},
            created_by="operator",
            effective_cycle_id="cycle-2",
        )


def test_trade_intent_keeps_direction_action_quantity_and_units_explicit() -> None:
    intent = TradeIntent(
        intent_id="intent-1",
        cycle_id="cycle-1",
        decision_id="decision-1",
        strategy_id="trend_pullback_v1",
        strategy_version="1.0.0-research",
        config_snapshot_id="config-1",
        config_hash="sha256:abc",
        runtime_mode=RuntimeMode.SHADOW,
        symbol="BTC/USDT:USDT",
        action=TradeAction.OPEN,
        position_side=PositionSide.LONG,
        exchange_side=ExchangeSide.BUY,
        target_quantity=Decimal("0.003"),
        signal_reference_price=Decimal("65000.10"),
        protection=ProtectionPolicy(
            stop_price=Decimal("64000"),
            take_profit_price=Decimal("67000.20"),
        ),
        signal_candle_close_time=datetime(2026, 7, 20, 7, 0, tzinfo=UTC),
        created_at=datetime(2026, 7, 20, 7, 0, 1, tzinfo=UTC),
    )

    assert intent.target_quantity == Decimal("0.003")
    assert intent.action is TradeAction.OPEN
    assert intent.position_side is PositionSide.LONG
