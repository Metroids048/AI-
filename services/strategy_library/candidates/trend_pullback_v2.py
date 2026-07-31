"""Point-in-time trend-pullback proposal generator."""

from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from decimal import Decimal
from typing import Literal

from pydantic import Field

from services.strategy_library.context import ClosedBar, FrozenContract, MarketContext
from services.strategy_library.proposals import EntryTrigger, InvalidationRule, StrategyProposal, TargetRule
from services.strategy_library.regime.scorer_v2 import RegimeScore

STRATEGY_ID = "trend_pullback_v2"
STRATEGY_VERSION = "2.0.0-research"


class TrendPullbackConfig(FrozenContract):
    ema_fast_period: int = Field(default=20, ge=10, le=30)
    ema_slow_period: int = Field(default=50, ge=30, le=80)
    stop_atr_buffer: Decimal = Field(default=Decimal("0.25"), ge=Decimal("0.15"), le=Decimal("0.35"))
    maximum_entry_distance_atr: Decimal = Field(default=Decimal("3"), gt=0)
    minimum_trend_score: float = Field(default=0.60, ge=0, le=1)
    expiry_bars: int = Field(default=2, ge=1, le=2)
    max_price_drift_bps: Decimal = Field(default=Decimal("20"), ge=0)
    expected_round_trip_cost_bps: Decimal = Field(default=Decimal("12"), ge=0)


def _ema(values: list[Decimal], period: int) -> Decimal:
    multiplier = Decimal("2") / Decimal(period + 1)
    result = values[0]
    for value in values[1:]:
        result = (value - result) * multiplier + result
    return result


def _feature_hash(
    *, context: MarketContext, regime: RegimeScore, config: TrendPullbackConfig, side: Literal["long", "short"]
) -> str:
    payload = {
        "context": context.model_dump(mode="json"),
        "regime": regime.model_dump(mode="json"),
        "config": config.model_dump(mode="json"),
        "side": side,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _proposal(
    *,
    context: MarketContext,
    regime: RegimeScore,
    config: TrendPullbackConfig,
    side: Literal["long", "short"],
    pullback: ClosedBar,
    confirmation: ClosedBar,
    atr: Decimal,
    fast_ema: Decimal,
) -> StrategyProposal | None:
    entry = confirmation.close
    stop = (
        pullback.low - atr * config.stop_atr_buffer if side == "long" else pullback.high + atr * config.stop_atr_buffer
    )
    risk = abs(entry - stop)
    tp1 = entry + risk if side == "long" else entry - risk
    tp2 = entry + risk * Decimal("1.8") if side == "long" else entry - risk * Decimal("1.8")
    tp3 = entry + risk * Decimal("2.5") if side == "long" else entry - risk * Decimal("2.5")
    cost = entry * config.expected_round_trip_cost_bps / Decimal("10000")
    cost_adjusted_rr = (abs(tp2 - entry) - cost) / (risk + cost)
    if cost_adjusted_rr <= 0:
        return None
    signal_time = context.bars_15m.last_closed_at
    assert signal_time is not None
    higher_direction = regime.evidence.get("direction_4h", 0.0)
    higher_conflicts = (side == "long" and higher_direction < 0) or (side == "short" and higher_direction > 0)
    regime_fit = (regime.trend_up if side == "long" else regime.trend_down) * (0.75 if higher_conflicts else 1.0)
    volume_ratio = pullback.volume / (
        sum((bar.volume for bar in context.bars_15m.bars[-12:-2]), Decimal("0")) / Decimal("10")
    )
    return StrategyProposal(
        proposal_id=f"{STRATEGY_ID}:{context.symbol}:{signal_time.isoformat()}:{side}",
        strategy_id=STRATEGY_ID,
        strategy_version=STRATEGY_VERSION,
        symbol=context.symbol,
        side=side,
        setup_type="trend_pullback_reclaim",
        signal_bar_time=signal_time,
        expires_at=signal_time + timedelta(minutes=15 * config.expiry_bars),
        entry_trigger=EntryTrigger(
            entry_type="market_after_confirmation",
            reference_price=entry,
            confirmation_price=pullback.high if side == "long" else pullback.low,
            max_price_drift_bps=config.max_price_drift_bps,
        ),
        invalidation=InvalidationRule(
            stop_price=stop,
            extreme_price=pullback.low if side == "long" else pullback.high,
            reason="pullback_structure_invalidated",
        ),
        targets=(
            TargetRule(label="one_r", price=tp1, quantity_fraction=Decimal("0.35")),
            TargetRule(label="one_point_eight_r", price=tp2, quantity_fraction=Decimal("0.40")),
            TargetRule(label="trend_runner", price=tp3, quantity_fraction=Decimal("0.25")),
        ),
        regime_fit=regime_fit,
        setup_quality=min(1.0, 0.6 + min(0.4, float(Decimal("1") - volume_ratio) * 0.4)),
        cost_adjusted_rr=cost_adjusted_rr,
        confidence_components={
            "trend_score": regime.trend_up if side == "long" else regime.trend_down,
            "higher_timeframe_alignment": 0.75 if higher_conflicts else 1.0,
            "pullback_volume_contraction": max(0.0, min(1.0, float(Decimal("1") - volume_ratio))),
            "ema_reclaim": float(confirmation.close / fast_ema),
        },
        feature_snapshot_hash=_feature_hash(context=context, regime=regime, config=config, side=side),
        reasons=("ema_pullback_volume_contraction", "next_closed_bar_breakout_confirmed"),
    )


def evaluate_trend_pullback_v2(
    context: MarketContext,
    regime: RegimeScore,
    *,
    config: TrendPullbackConfig | None = None,
) -> StrategyProposal | None:
    """Return a symmetric, non-chasing pullback proposal from closed 15m bars."""

    config = config or TrendPullbackConfig()
    window = context.bars_15m
    if (
        len(window.bars) < config.ema_slow_period + 2
        or window.gap_count
        or "15m" in context.freshness.stale_timeframes
        or window.last_closed_at is None
        or window.last_closed_at > context.decision_time
    ):
        return None
    atr = context.volatility.atr_14
    if atr is None or atr <= 0:
        return None
    pullback, confirmation = window.bars[-2:]
    prior = window.bars[-(config.ema_slow_period + 2) : -2]
    closes = [bar.close for bar in prior]
    fast_ema = _ema(closes, config.ema_fast_period)
    slow_ema = _ema(closes, config.ema_slow_period)
    previous_volumes = [bar.volume for bar in window.bars[-12:-2]]
    mean_volume = sum(previous_volumes, Decimal("0")) / Decimal(len(previous_volumes))
    if mean_volume <= 0 or pullback.volume >= mean_volume:
        return None
    long = (
        regime.trend_up >= config.minimum_trend_score
        and fast_ema > slow_ema
        and pullback.low <= fast_ema
        and pullback.close >= slow_ema
        and confirmation.high > pullback.high
        and confirmation.close > pullback.high
        and confirmation.close - fast_ema <= atr * config.maximum_entry_distance_atr
    )
    short = (
        regime.trend_down >= config.minimum_trend_score
        and fast_ema < slow_ema
        and pullback.high >= fast_ema
        and pullback.close <= slow_ema
        and confirmation.low < pullback.low
        and confirmation.close < pullback.low
        and fast_ema - confirmation.close <= atr * config.maximum_entry_distance_atr
    )
    if long == short:
        return None
    return _proposal(
        context=context,
        regime=regime,
        config=config,
        side="long" if long else "short",
        pullback=pullback,
        confirmation=confirmation,
        atr=atr,
        fast_ema=fast_ema,
    )
