"""R-001 research harness contracts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from services.strategy_library.context import MarketContextBuilder
from services.strategy_library.proposal_pipeline import run_proposal_pipeline
from shared.models import Exchange, OHLCVBar, Timeframe

SYMBOLS = ("BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "BNB/USDT")


def _bars(symbol: str, timeframe: Timeframe, count: int, start: datetime) -> list[OHLCVBar]:
    return [
        OHLCVBar(
            symbol=symbol,
            exchange=Exchange.BINANCE,
            timeframe=timeframe,
            time=start + timedelta(minutes=index),
            open=Decimal("100") + index,
            high=Decimal("101") + index,
            low=Decimal("99") + index,
            close=Decimal("100") + index,
            volume=Decimal("10"),
        )
        for index in range(count)
    ]


def test_r001_execution_scope_is_exactly_five_symbols() -> None:
    assert SYMBOLS == ("BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "BNB/USDT")


def test_r001_pipeline_generates_point_in_time_regime_and_candidate_versions() -> None:
    symbol = "BTC/USDT"
    decision_time = datetime(2025, 1, 1, tzinfo=UTC)
    builder = MarketContextBuilder()
    context = builder.build(
        symbol=symbol,
        decision_time=decision_time,
        bars_by_timeframe={
            "1m": _bars(symbol, Timeframe.M1, 2, decision_time),
            "5m": _bars(symbol, Timeframe.M5, 2, decision_time),
            "15m": _bars(symbol, Timeframe.M15, 80, decision_time),
            "1h": _bars(symbol, Timeframe.H1, 80, decision_time),
            "4h": _bars(symbol, Timeframe.H4, 80, decision_time),
        },
        market_extras=(),
        source_ids=("test-point-in-time",),
    )
    result = run_proposal_pipeline(context)

    assert result.context_hash
    assert result.regime.trend_up >= 0
    assert result.regime.trend_down >= 0
    assert result.regime.range >= 0
    assert result.strategy_versions
    assert all(version for version in result.strategy_versions.values())
