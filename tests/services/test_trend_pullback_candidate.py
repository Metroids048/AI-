from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from services.strategy_library.candidates.trend_pullback_v1 import (
    TrendPullbackFeatures,
    evaluate_trend_pullback,
)
from shared.models import MarketRegime, PositionSide


def _features(*, regime: MarketRegime, direction: PositionSide) -> TrendPullbackFeatures:
    return TrendPullbackFeatures(
        symbol="BTC/USDT",
        candle_close_time=datetime(2026, 7, 20, 7, 0, tzinfo=UTC),
        regime=regime,
        trend_direction=direction,
        trend_quality=Decimal("35"),
        pullback_quality=Decimal("25"),
        macd_recovery_quality=Decimal("20"),
        volume_quality=Decimal("10"),
        relative_strength_quality=Decimal("10"),
    )


def test_trend_pullback_candidate_is_directionally_symmetric() -> None:
    long_signal = evaluate_trend_pullback(_features(regime=MarketRegime.BULL, direction=PositionSide.LONG))
    short_signal = evaluate_trend_pullback(_features(regime=MarketRegime.BEAR, direction=PositionSide.SHORT))

    assert long_signal is not None and long_signal.side is PositionSide.LONG
    assert short_signal is not None and short_signal.side is PositionSide.SHORT
    assert long_signal.score == short_signal.score == Decimal("100")


def test_trend_pullback_candidate_does_not_open_in_range() -> None:
    assert evaluate_trend_pullback(_features(regime=MarketRegime.RANGE, direction=PositionSide.LONG)) is None
