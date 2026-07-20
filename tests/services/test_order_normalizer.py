from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from services.execution.order_normalizer import (
    CcxtMarketRulesLoader,
    OrderNormalizationError,
    OrderNormalizer,
)
from shared.models import (
    ExchangeSide,
    MarketRulesSnapshot,
    PositionSide,
    ProtectionPolicy,
    RuntimeMode,
    TradeAction,
    TradeIntent,
)


def _rules(*, position_mode: str = "ONE_WAY") -> MarketRulesSnapshot:
    return MarketRulesSnapshot(
        rules_snapshot_id="rules-1",
        symbol="BTC/USDT:USDT",
        market_status="TRADING",
        position_mode=position_mode,
        margin_mode="CROSS",
        leverage=Decimal("3"),
        tick_size=Decimal("0.10"),
        step_size=Decimal("0.001"),
        min_quantity=Decimal("0.001"),
        max_quantity=Decimal("100"),
        min_notional=Decimal("20"),
        loaded_at=datetime(2026, 7, 20, 7, 0, tzinfo=UTC),
    )


def _intent(*, action: TradeAction, position_side: PositionSide, side: ExchangeSide) -> TradeIntent:
    return TradeIntent(
        intent_id=f"intent-{action}-{position_side}",
        cycle_id="cycle-1",
        decision_id="decision-1",
        strategy_id="strategy-1",
        strategy_version="v1",
        config_snapshot_id="config-1",
        config_hash="sha256:abc",
        runtime_mode=RuntimeMode.SHADOW,
        symbol="BTC/USDT:USDT",
        action=action,
        position_side=position_side,
        exchange_side=side,
        target_quantity=Decimal("0.0039"),
        signal_reference_price=Decimal("65000.17"),
        protection=ProtectionPolicy(stop_price=Decimal("64000.09"), take_profit_price=Decimal("67000.29")),
        signal_candle_close_time=datetime(2026, 7, 20, 6, 59, 59, tzinfo=UTC),
        created_at=datetime(2026, 7, 20, 7, 0, tzinfo=UTC),
    )


def test_one_way_close_long_uses_sell_reduce_only() -> None:
    order = OrderNormalizer().normalize(
        _intent(action=TradeAction.CLOSE, position_side=PositionSide.LONG, side=ExchangeSide.SELL),
        _rules(),
    )

    assert order.side is ExchangeSide.SELL
    assert order.position_side == "BOTH"
    assert order.reduce_only is True
    assert order.quantity == Decimal("0.003")
    assert order.stop_price == Decimal("64000.0")


def test_hedge_close_short_omits_reduce_only_and_caps_to_confirmed_position() -> None:
    order = OrderNormalizer().normalize(
        _intent(action=TradeAction.CLOSE, position_side=PositionSide.SHORT, side=ExchangeSide.BUY),
        _rules(position_mode="HEDGE"),
        confirmed_position_quantity=Decimal("0.0024"),
    )

    assert order.side is ExchangeSide.BUY
    assert order.position_side == "SHORT"
    assert order.reduce_only is None
    assert order.quantity == Decimal("0.002")


def test_hedge_close_requires_confirmed_position_quantity() -> None:
    with pytest.raises(OrderNormalizationError, match="confirmed position"):
        OrderNormalizer().normalize(
            _intent(action=TradeAction.CLOSE, position_side=PositionSide.LONG, side=ExchangeSide.SELL),
            _rules(position_mode="HEDGE"),
        )


def test_unknown_market_rules_fail_closed() -> None:
    rules = _rules().model_copy(update={"market_status": "UNKNOWN"})

    with pytest.raises(OrderNormalizationError, match="market status"):
        OrderNormalizer().normalize(
            _intent(action=TradeAction.OPEN, position_side=PositionSide.LONG, side=ExchangeSide.BUY),
            rules,
        )


def test_market_rules_loader_uses_exchange_metadata_without_static_fallback() -> None:
    class Client:
        def load_markets(self):
            return {
                "BTC/USDT:USDT": {
                    "active": True,
                    "limits": {
                        "amount": {"min": 0.001, "max": 100},
                        "cost": {"min": 20, "max": None},
                    },
                    "info": {
                        "status": "TRADING",
                        "filters": [
                            {"filterType": "PRICE_FILTER", "tickSize": "0.10"},
                            {"filterType": "LOT_SIZE", "stepSize": "0.001"},
                            {"filterType": "MIN_NOTIONAL", "notional": "20"},
                        ],
                    },
                }
            }

    rules = CcxtMarketRulesLoader(Client()).load(
        symbol="BTC/USDT:USDT",
        position_mode="ONE_WAY",
        margin_mode="CROSS",
        leverage=Decimal("3"),
        loaded_at=datetime(2026, 7, 20, 7, 0, tzinfo=UTC),
    )

    assert rules.tick_size == Decimal("0.10")
    assert rules.step_size == Decimal("0.001")
    assert rules.min_notional == Decimal("20")
