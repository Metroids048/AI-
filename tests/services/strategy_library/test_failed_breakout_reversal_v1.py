from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from services.strategy_library.candidates.failed_breakout_reversal_v1 import (
    FailedBreakoutConfig,
    evaluate_failed_breakout_reversal,
)
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

DECISION_TIME = datetime(2026, 1, 1, 6, 30, tzinfo=UTC)


def _bar(
    index: int,
    *,
    open_price: str = "95",
    high: str = "100",
    low: str = "90",
    close: str = "95",
    volume: str = "10",
) -> ClosedBar:
    return ClosedBar(
        symbol="BTC/USDT",
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


def _context(*, side: str, continuation: bool = False) -> MarketContext:
    prior = tuple(_bar(index) for index in range(24))
    if side == "short":
        sweep = _bar(24, open_price="99", high="103", low="98", close="99", volume="20")
        confirmation = _bar(
            25,
            open_price="99",
            high="104" if continuation else "100",
            low="99" if continuation else "97",
            close="103" if continuation else "98",
            volume="12",
        )
    else:
        sweep = _bar(24, open_price="91", high="92", low="87", close="91", volume="20")
        confirmation = _bar(
            25,
            open_price="91",
            high="91" if continuation else "93",
            low="86" if continuation else "90",
            close="87" if continuation else "92",
            volume="12",
        )
    bars_15m = (*prior, sweep, confirmation)
    return MarketContext(
        symbol="BTC/USDT",
        decision_time=DECISION_TIME,
        bars_1m=_window("1m"),
        bars_5m=_window("5m"),
        bars_15m=_window("15m", bars_15m),
        bars_1h=_window("1h"),
        bars_4h=_window("4h"),
        structure=StructureFeatures(recent_high=Decimal("103"), recent_low=Decimal("87")),
        momentum=MomentumFeatures(),
        volume=VolumeFeatures(latest_volume=Decimal("12"), mean_volume_20=Decimal("10"), volume_ratio=Decimal("1.2")),
        volatility=VolatilityFeatures(atr_14=Decimal("2"), true_range=Decimal("3")),
        derivatives=DerivativesFeatures(),
        session=SessionFeatures(utc_hour=6, utc_weekday=3),
        freshness=DataFreshness(age_seconds={}, stale_timeframes=(), has_gaps=False),
        source_ids=("history:test",),
        missing_features=["funding_rate:missing", "open_interest:missing"],
    )


def _regime() -> RegimeScore:
    return RegimeScore(
        trend_up=0.2,
        trend_down=0.2,
        range=0.7,
        compression=0.1,
        expansion=0.2,
        unstable=0.0,
        evidence={},
    )


def test_failed_breakout_short_matches_sweep_reclaim_pattern() -> None:
    proposal = evaluate_failed_breakout_reversal(_context(side="short"), _regime())

    assert proposal is not None
    assert proposal.side == "short"
    assert proposal.setup_type == "failed_breakout_reversal"
    assert proposal.entry_trigger.reference_price == Decimal("98")
    assert proposal.invalidation.stop_price > Decimal("103")
    assert proposal.expires_at == DECISION_TIME + timedelta(minutes=30)
    assert proposal.reasons[0] == "donchian_24_sweep_reclaimed"


def test_failed_breakout_long_is_directionally_symmetric() -> None:
    proposal = evaluate_failed_breakout_reversal(_context(side="long"), _regime())

    assert proposal is not None
    assert proposal.side == "long"
    assert proposal.invalidation.stop_price < Decimal("87")
    assert proposal.targets[0].price > proposal.entry_trigger.reference_price


def test_failed_breakout_rejects_true_breakout_continuation() -> None:
    assert evaluate_failed_breakout_reversal(_context(side="short", continuation=True), _regime()) is None
    assert evaluate_failed_breakout_reversal(_context(side="long", continuation=True), _regime()) is None


def test_failed_breakout_stop_is_beyond_sweep_extreme() -> None:
    config = FailedBreakoutConfig(atr_buffer=Decimal("0.25"))
    short = evaluate_failed_breakout_reversal(_context(side="short"), _regime(), config=config)
    long = evaluate_failed_breakout_reversal(_context(side="long"), _regime(), config=config)

    assert short is not None and short.invalidation.stop_price == Decimal("103.50")
    assert long is not None and long.invalidation.stop_price == Decimal("86.50")
