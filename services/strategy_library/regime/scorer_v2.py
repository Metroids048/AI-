"""Probability-like market regime scores without global timeframe vetoes."""

from __future__ import annotations

from decimal import Decimal

from pydantic import Field

from services.strategy_library.context import BarWindow, FrozenContract, MarketContext

SCORER_VERSION = "regime_scorer_v2.0.0"
DIRECTION_WEIGHTS = {
    "15m": Decimal("0.50"),
    "1h": Decimal("0.30"),
    "4h": Decimal("0.20"),
}


class RegimeScore(FrozenContract):
    trend_up: float = Field(ge=0, le=1)
    trend_down: float = Field(ge=0, le=1)
    range: float = Field(ge=0, le=1)
    compression: float = Field(ge=0, le=1)
    expansion: float = Field(ge=0, le=1)
    unstable: float = Field(ge=0, le=1)
    evidence: dict[str, float]


def _clamp(value: Decimal | float, minimum: float = 0.0, maximum: float = 1.0) -> float:
    return max(minimum, min(maximum, float(value)))


def _direction(window: BarWindow) -> Decimal:
    if len(window.bars) < 2:
        return Decimal("0")
    first = window.bars[0].close
    if first <= 0:
        return Decimal("0")
    total_return = (window.bars[-1].close - first) / first
    scaled = total_return / Decimal("0.03")
    return max(Decimal("-1"), min(Decimal("1"), scaled))


def _true_ranges(window: BarWindow) -> list[Decimal]:
    ranges: list[Decimal] = []
    previous_close: Decimal | None = None
    for bar in window.bars:
        reference = previous_close if previous_close is not None else bar.open
        ranges.append(max(bar.high - bar.low, abs(bar.high - reference), abs(bar.low - reference)))
        previous_close = bar.close
    return ranges


def _volatility_scores(window: BarWindow) -> tuple[float, float, float]:
    ranges = _true_ranges(window)
    if len(ranges) < 10:
        return 0.0, 0.0, 0.0
    recent = sum(ranges[-5:], Decimal("0")) / Decimal("5")
    baseline_sample = ranges[-20:-5] or ranges[:-5]
    baseline = sum(baseline_sample, Decimal("0")) / Decimal(len(baseline_sample))
    if baseline <= 0:
        return 0.0, 0.0, 0.0
    ratio = recent / baseline
    compression = _clamp((Decimal("1") - ratio) / Decimal("0.50"))
    expansion = _clamp((ratio - Decimal("1")) / Decimal("1.00"))
    shock = _clamp((ranges[-1] / baseline - Decimal("2")) / Decimal("2"))
    return compression, expansion, shock


class RegimeScorerV2:
    """Score regime fit; higher-timeframe conflict is a soft contribution only."""

    def score(self, context: MarketContext) -> RegimeScore:
        windows = {
            "15m": context.bars_15m,
            "1h": context.bars_1h,
            "4h": context.bars_4h,
        }
        directions = {timeframe: _direction(window) for timeframe, window in windows.items()}
        trend_up = sum(
            DIRECTION_WEIGHTS[timeframe] * max(Decimal("0"), direction) for timeframe, direction in directions.items()
        )
        trend_down = sum(
            DIRECTION_WEIGHTS[timeframe] * max(Decimal("0"), -direction) for timeframe, direction in directions.items()
        )
        compression, expansion, shock = _volatility_scores(context.bars_15m)
        missing_fraction = (
            sum(
                1
                for timeframe in ("1m", "5m", "15m", "1h", "4h")
                if f"bars_{timeframe}:missing" in context.missing_features
            )
            / 5
        )
        stale_fraction = len(context.freshness.stale_timeframes) / 5
        gap_penalty = 1.0 if context.freshness.has_gaps else 0.0
        unstable = _clamp(missing_fraction * 0.60 + stale_fraction * 0.25 + gap_penalty * 0.25 + shock * 0.35)
        dominant_trend = max(_clamp(trend_up), _clamp(trend_down))
        range_score = _clamp((1.0 - dominant_trend) * (1.0 - expansion * 0.5))
        return RegimeScore(
            trend_up=_clamp(trend_up),
            trend_down=_clamp(trend_down),
            range=range_score,
            compression=compression,
            expansion=expansion,
            unstable=unstable,
            evidence={
                "direction_15m": float(directions["15m"]),
                "direction_1h": float(directions["1h"]),
                "direction_4h": float(directions["4h"]),
                "missing_timeframe_fraction": missing_fraction,
                "stale_timeframe_fraction": stale_fraction,
                "gap_penalty": gap_penalty,
                "volatility_shock": shock,
            },
        )
