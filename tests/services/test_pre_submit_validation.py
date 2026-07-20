from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from services.execution.pre_submit_validation import PreSubmitPolicy, validate_pre_submit
from shared.models import (
    BlockCode,
    ExchangeSide,
    PositionSide,
    ProtectionPolicy,
    RuntimeMode,
    TradeAction,
    TradeIntent,
    ValidatedMarketSnapshot,
)


def _intent() -> TradeIntent:
    now = datetime(2026, 7, 20, 7, 0, tzinfo=UTC)
    return TradeIntent(
        intent_id="intent-1",
        cycle_id="cycle-1",
        decision_id="decision-1",
        strategy_id="strategy-1",
        strategy_version="v1",
        config_snapshot_id="config-1",
        config_hash="sha256:config",
        runtime_mode=RuntimeMode.PAPER,
        symbol="BTC/USDT:USDT",
        action=TradeAction.OPEN,
        position_side=PositionSide.LONG,
        exchange_side=ExchangeSide.BUY,
        target_quantity=Decimal("0.003"),
        signal_reference_price=Decimal("100"),
        protection=ProtectionPolicy(stop_price=Decimal("98"), take_profit_price=Decimal("104")),
        signal_candle_close_time=now,
        created_at=now,
    )


def test_pre_submit_rejects_stale_or_deviated_quote() -> None:
    snapshot = ValidatedMarketSnapshot(
        symbol="BTC/USDT:USDT",
        bid_price=Decimal("102.9"),
        ask_price=Decimal("103"),
        mark_price=Decimal("103"),
        exchange_time=datetime(2026, 7, 20, 7, 0, 10, tzinfo=UTC),
        received_at=datetime(2026, 7, 20, 7, 0, tzinfo=UTC),
    )
    decision = validate_pre_submit(
        intent=_intent(),
        snapshot=snapshot,
        now=datetime(2026, 7, 20, 7, 0, 10, tzinfo=UTC),
        policy=PreSubmitPolicy(
            max_quote_age=timedelta(seconds=2),
            max_signal_deviation_fraction=Decimal("0.01"),
            max_spread_fraction=Decimal("0.01"),
            minimum_reward_risk=Decimal("1.5"),
            round_trip_cost_fraction=Decimal("0.001"),
        ),
    )

    assert decision.accepted is False
    assert BlockCode.DATA_STALE in decision.block_codes
    assert BlockCode.PRICE_DEVIATION_EXCEEDED in decision.block_codes
