from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from services.strategy_library.candidates.trend_pullback_v2 import evaluate_trend_pullback_v2
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

DECISION_TIME = datetime(2026, 1, 2, 12, 0, tzinfo=UTC)


def _bar(index: int, *, open_price: Decimal, high: Decimal, low: Decimal, close: Decimal, volume: Decimal) -> ClosedBar:
    return ClosedBar(
        symbol="BTC/USDT",
        exchange=Exchange.BINANCE,
        timeframe=Timeframe.M15,
        time=DECISION_TIME - timedelta(minutes=15 * (52 - index)),
        open=open_price,
        high=high,
        low=low,
        close=close,
        volume=volume,
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


def _context(*, side: str, overextended: bool = False) -> MarketContext:
    bars: list[ClosedBar] = []
    for index in range(50):
        close = Decimal("90") + Decimal(index) * Decimal("0.20")
        if side == "short":
            close = Decimal("110") - Decimal(index) * Decimal("0.20")
        bars.append(
            _bar(
                index,
                open_price=close - Decimal("0.10"),
                high=close + Decimal("0.40"),
                low=close - Decimal("0.40"),
                close=close,
                volume=Decimal("20"),
            )
        )
    if side == "long":
        bars.extend(
            [
                _bar(
                    50,
                    open_price=Decimal("99.8"),
                    high=Decimal("100"),
                    low=Decimal("97"),
                    close=Decimal("98.5"),
                    volume=Decimal("8"),
                ),
                _bar(
                    51,
                    open_price=Decimal("98.5"),
                    high=Decimal("105") if overextended else Decimal("100.8"),
                    low=Decimal("98.4"),
                    close=Decimal("104.5") if overextended else Decimal("100.5"),
                    volume=Decimal("16"),
                ),
            ]
        )
    else:
        bars.extend(
            [
                _bar(
                    50,
                    open_price=Decimal("100.2"),
                    high=Decimal("103"),
                    low=Decimal("100"),
                    close=Decimal("101.5"),
                    volume=Decimal("8"),
                ),
                _bar(
                    51,
                    open_price=Decimal("101.5"),
                    high=Decimal("101.6"),
                    low=Decimal("95") if overextended else Decimal("99.2"),
                    close=Decimal("95.5") if overextended else Decimal("99.5"),
                    volume=Decimal("16"),
                ),
            ]
        )
    bars_15m = tuple(bars)
    return MarketContext(
        symbol="BTC/USDT",
        decision_time=DECISION_TIME,
        bars_1m=_window("1m"),
        bars_5m=_window("5m"),
        bars_15m=_window("15m", bars_15m),
        bars_1h=_window("1h"),
        bars_4h=_window("4h"),
        structure=StructureFeatures(),
        momentum=MomentumFeatures(),
        volume=VolumeFeatures(latest_volume=Decimal("16"), mean_volume_20=Decimal("18"), volume_ratio=Decimal("0.9")),
        volatility=VolatilityFeatures(atr_14=Decimal("1"), true_range=Decimal("1")),
        derivatives=DerivativesFeatures(),
        session=SessionFeatures(utc_hour=12, utc_weekday=4),
        freshness=DataFreshness(age_seconds={}, stale_timeframes=(), has_gaps=False),
        source_ids=("history:test",),
        missing_features=[],
    )


def _regime(*, side: str, direction_4h: float = 1.0) -> RegimeScore:
    return RegimeScore(
        trend_up=0.8 if side == "long" else 0.1,
        trend_down=0.8 if side == "short" else 0.1,
        range=0.1,
        compression=0.1,
        expansion=0.2,
        unstable=0.0,
        evidence={"direction_4h": direction_4h},
    )


def test_trend_pullback_long_and_short_are_symmetric() -> None:
    long = evaluate_trend_pullback_v2(_context(side="long"), _regime(side="long"))
    short = evaluate_trend_pullback_v2(_context(side="short"), _regime(side="short"))

    assert long is not None and long.side == "long"
    assert short is not None and short.side == "short"
    assert long.invalidation.stop_price < long.entry_trigger.reference_price
    assert short.invalidation.stop_price > short.entry_trigger.reference_price


def test_trend_pullback_rejects_overextended_entry() -> None:
    assert evaluate_trend_pullback_v2(_context(side="long", overextended=True), _regime(side="long")) is None
    assert evaluate_trend_pullback_v2(_context(side="short", overextended=True), _regime(side="short")) is None


def test_4h_disagreement_reduces_score_but_does_not_hard_veto() -> None:
    aligned = evaluate_trend_pullback_v2(_context(side="long"), _regime(side="long", direction_4h=1.0))
    conflicting = evaluate_trend_pullback_v2(_context(side="long"), _regime(side="long", direction_4h=-1.0))

    assert aligned is not None and conflicting is not None
    assert 0 < conflicting.regime_fit < aligned.regime_fit


def test_trend_pullback_proposal_expires_after_two_closed_bars() -> None:
    proposal = evaluate_trend_pullback_v2(_context(side="long"), _regime(side="long"))

    assert proposal is not None
    assert proposal.expires_at == DECISION_TIME + timedelta(minutes=30)
