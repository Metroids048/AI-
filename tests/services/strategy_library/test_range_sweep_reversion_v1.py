from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from services.strategy_library.candidates.range_sweep_reversion_v1 import evaluate_range_sweep_reversion
from services.strategy_library.context import (
    BarWindow,
    ClosedBar,
    DataFreshness,
    DerivativesFeatures,
    MarketContext,
    MomentumFeatures,
    SessionFeatures,
    StructureFeatures,
    VolatilityFeatures,
    VolumeFeatures,
)
from services.strategy_library.regime.scorer_v2 import RegimeScore
from shared.models import Exchange, Timeframe

DECISION_TIME = datetime(2026, 1, 3, 12, 0, tzinfo=UTC)


def _bar(index: int, *, open_price: str, high: str, low: str, close: str, volume: str = "10") -> ClosedBar:
    return ClosedBar(
        symbol="ETH/USDT",
        exchange=Exchange.BINANCE,
        timeframe=Timeframe.M15,
        time=DECISION_TIME - timedelta(minutes=15 * (26 - index)),
        open=Decimal(open_price),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=Decimal(volume),
    )


def _window(timeframe: str, bars: tuple[ClosedBar, ...] = ()) -> BarWindow:
    seconds = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400}[timeframe]
    return BarWindow(
        timeframe=timeframe,
        bars=bars,
        last_closed_at=DECISION_TIME if bars else None,
        expected_interval_seconds=seconds,
        gap_count=0,
    )


def _context(*, side: str, reclaimed: bool = True) -> MarketContext:
    prior = tuple(_bar(index, open_price="95", high="100", low="90", close="95") for index in range(24))
    if side == "long":
        sweep = _bar(24, open_price="91", high="92", low="87", close="91", volume="20")
        confirmation = _bar(
            25,
            open_price="91",
            high="93",
            low="86" if not reclaimed else "90",
            close="89" if not reclaimed else "92",
        )
    else:
        sweep = _bar(24, open_price="99", high="103", low="98", close="99", volume="20")
        confirmation = _bar(
            25,
            open_price="99",
            high="104" if not reclaimed else "100",
            low="97",
            close="101" if not reclaimed else "98",
        )
    return MarketContext(
        symbol="ETH/USDT",
        decision_time=DECISION_TIME,
        bars_1m=_window("1m"),
        bars_5m=_window("5m"),
        bars_15m=_window("15m", (*prior, sweep, confirmation)),
        bars_1h=_window("1h"),
        bars_4h=_window("4h"),
        structure=StructureFeatures(),
        momentum=MomentumFeatures(),
        volume=VolumeFeatures(latest_volume=Decimal("12"), mean_volume_20=Decimal("10"), volume_ratio=Decimal("1.2")),
        volatility=VolatilityFeatures(atr_14=Decimal("2"), true_range=Decimal("2")),
        derivatives=DerivativesFeatures(),
        session=SessionFeatures(utc_hour=12, utc_weekday=5),
        freshness=DataFreshness(age_seconds={}, stale_timeframes=(), has_gaps=False),
        source_ids=("history:test",),
        missing_features=[],
    )


def _regime() -> RegimeScore:
    return RegimeScore(
        trend_up=0.1,
        trend_down=0.1,
        range=0.8,
        compression=0.2,
        expansion=0.1,
        unstable=0.0,
        evidence={},
    )


def test_range_sweep_long_and_short_reclaim_are_symmetric() -> None:
    long = evaluate_range_sweep_reversion(_context(side="long"), _regime())
    short = evaluate_range_sweep_reversion(_context(side="short"), _regime())

    assert long is not None and long.side == "long"
    assert short is not None and short.side == "short"
    assert long.targets[0].price == Decimal("95")
    assert short.targets[0].price == Decimal("95")


def test_range_sweep_reclaim_rejects_continuing_breakdown_or_breakout() -> None:
    assert evaluate_range_sweep_reversion(_context(side="long", reclaimed=False), _regime()) is None
    assert evaluate_range_sweep_reversion(_context(side="short", reclaimed=False), _regime()) is None


def test_range_sweep_stop_is_beyond_liquidity_sweep_and_proposal_expires() -> None:
    proposal = evaluate_range_sweep_reversion(_context(side="long"), _regime())

    assert proposal is not None
    assert proposal.invalidation.stop_price == Decimal("86.50")
    assert proposal.expires_at == DECISION_TIME + timedelta(minutes=30)
