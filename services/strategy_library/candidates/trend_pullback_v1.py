"""Research-only deterministic scoring contract for trend_pullback_v1."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import ConfigDict, Field

from shared.models import MarketRegime, PlatformModel, PositionSide, StrategySignal


class TrendPullbackFeatures(PlatformModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    symbol: str
    candle_close_time: datetime
    regime: MarketRegime
    trend_direction: PositionSide
    trend_quality: Decimal = Field(ge=0, le=35)
    pullback_quality: Decimal = Field(ge=0, le=25)
    macd_recovery_quality: Decimal = Field(ge=0, le=20)
    volume_quality: Decimal = Field(ge=0, le=10)
    relative_strength_quality: Decimal = Field(ge=0, le=10)


def evaluate_trend_pullback(features: TrendPullbackFeatures) -> StrategySignal | None:
    expected_side = {
        MarketRegime.BULL: PositionSide.LONG,
        MarketRegime.BEAR: PositionSide.SHORT,
    }.get(features.regime)
    if expected_side is None or features.trend_direction is not expected_side:
        return None
    components = {
        "trend_quality": features.trend_quality,
        "pullback_quality": features.pullback_quality,
        "macd_recovery_quality": features.macd_recovery_quality,
        "volume_quality": features.volume_quality,
        "relative_strength_quality": features.relative_strength_quality,
    }
    score = sum(components.values(), Decimal("0"))
    if score < Decimal("70"):
        return None
    return StrategySignal(
        decision_id=(f"trend_pullback_v1:{features.symbol}:{features.candle_close_time.isoformat()}"),
        symbol=features.symbol,
        side=expected_side,
        score=score,
        score_components=components,
        regime=features.regime,
        signal_candle_close_time=features.candle_close_time,
        strategy_id="trend_pullback_v1",
        strategy_version="1.0.0-research",
    )
