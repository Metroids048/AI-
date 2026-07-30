from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from services.strategy_library.context import (
    TIMEFRAME_DELTAS,
    MarketContextBuilder,
)
from shared.models import Exchange, MarketExtras, OHLCVBar, Timeframe

TIMEFRAMES = ("1m", "5m", "15m", "1h", "4h")


def _bar(
    timeframe: str,
    opened_at: datetime,
    close: str = "100",
    *,
    symbol: str = "BTC/USDT",
) -> OHLCVBar:
    return OHLCVBar(
        symbol=symbol,
        exchange=Exchange.BINANCE,
        timeframe=Timeframe(timeframe),
        time=opened_at,
        open=Decimal("99"),
        high=Decimal("102"),
        low=Decimal("98"),
        close=Decimal(close),
        volume=Decimal("10"),
    )


def _windows(decision_time: datetime) -> dict[str, list[OHLCVBar]]:
    windows: dict[str, list[OHLCVBar]] = {}
    for timeframe in TIMEFRAMES:
        delta = TIMEFRAME_DELTAS[timeframe]
        windows[timeframe] = [
            _bar(timeframe, decision_time - delta * 2, "100"),
            _bar(timeframe, decision_time - delta, "101"),
            _bar(timeframe, decision_time, "999"),
        ]
    return windows


def test_market_context_uses_only_point_in_time_closed_bars() -> None:
    decision_time = datetime(2026, 7, 30, 4, 0, tzinfo=UTC)

    context = MarketContextBuilder().build(
        symbol="BTC/USDT",
        decision_time=decision_time,
        bars_by_timeframe=_windows(decision_time),
        source_ids=["binance:ohlcv"],
    )

    for timeframe in TIMEFRAMES:
        window = getattr(context, f"bars_{timeframe}")
        assert len(window.bars) == 2
        assert window.bars[-1].close == Decimal("101")
        assert window.last_closed_at == decision_time
        assert all(bar.timestamp + TIMEFRAME_DELTAS[timeframe] <= decision_time for bar in window.bars)


def test_missing_funding_is_not_replaced_with_zero() -> None:
    decision_time = datetime(2026, 7, 30, 4, 0, tzinfo=UTC)

    context = MarketContextBuilder().build(
        symbol="BTC/USDT",
        decision_time=decision_time,
        bars_by_timeframe=_windows(decision_time),
        market_extras=None,
        source_ids=["binance:ohlcv"],
    )

    assert context.derivatives.funding_rate is None
    assert context.derivatives.open_interest is None
    assert context.missing_features == ["funding_rate:missing", "open_interest:missing"]


def test_market_context_uses_latest_point_in_time_derivatives_snapshot() -> None:
    decision_time = datetime(2026, 7, 30, 4, 0, tzinfo=UTC)
    extras = [
        MarketExtras(
            symbol="BTC/USDT",
            time=decision_time - timedelta(hours=1),
            funding_rate=Decimal("0.0001"),
            open_interest=Decimal("1234"),
        ),
        MarketExtras(
            symbol="BTC/USDT",
            time=decision_time + timedelta(minutes=1),
            funding_rate=Decimal("0.999"),
            open_interest=Decimal("9999"),
        ),
    ]

    context = MarketContextBuilder().build(
        symbol="BTC/USDT",
        decision_time=decision_time,
        bars_by_timeframe=_windows(decision_time),
        market_extras=extras,
        source_ids=["binance:ohlcv", "binance:premium-index"],
    )

    assert context.derivatives.funding_rate == Decimal("0.0001")
    assert context.derivatives.open_interest == Decimal("1234")
    assert context.derivatives.observed_at == decision_time - timedelta(hours=1)
    assert context.missing_features == []


def test_market_context_contract_is_frozen_and_forbids_unknown_fields() -> None:
    decision_time = datetime(2026, 7, 30, 4, 0, tzinfo=UTC)
    context = MarketContextBuilder().build(
        symbol="BTC/USDT",
        decision_time=decision_time,
        bars_by_timeframe=_windows(decision_time),
        source_ids=["binance:ohlcv"],
    )

    with pytest.raises(ValidationError):
        context.symbol = "ETH/USDT"
    with pytest.raises(ValidationError):
        context.bars_15m.bars[-1].close = Decimal("0")
    with pytest.raises(ValidationError):
        type(context)(**{**context.model_dump(), "unexpected": True})


def test_missing_timeframe_is_explicit_and_stale() -> None:
    decision_time = datetime(2026, 7, 30, 4, 0, tzinfo=UTC)
    windows = _windows(decision_time)
    windows["5m"] = []

    context = MarketContextBuilder().build(
        symbol="BTC/USDT",
        decision_time=decision_time,
        bars_by_timeframe=windows,
        source_ids=["binance:ohlcv"],
    )

    assert context.bars_5m.bars == ()
    assert "bars_5m:missing" in context.missing_features
    assert "5m" in context.freshness.stale_timeframes


def test_market_context_rejects_cross_symbol_bar_contamination() -> None:
    decision_time = datetime(2026, 7, 30, 4, 0, tzinfo=UTC)
    windows = _windows(decision_time)
    windows["15m"].append(
        _bar(
            "15m",
            decision_time - timedelta(minutes=15),
            "999",
            symbol="ETH/USDT",
        )
    )

    context = MarketContextBuilder().build(
        symbol="BTC/USDT",
        decision_time=decision_time,
        bars_by_timeframe=windows,
        source_ids=["binance:ohlcv"],
    )

    assert all(bar.symbol == "BTC/USDT" for bar in context.bars_15m.bars)
    assert context.bars_15m.bars[-1].close == Decimal("101")
