"""Point-in-time failed-breakout reversal proposal generator."""

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

STRATEGY_ID = "failed_breakout_reversal_v1"
STRATEGY_VERSION = "1.0.0-research"


class FailedBreakoutConfig(FrozenContract):
    structure_lookback: int = Field(default=24, ge=12, le=72)
    atr_buffer: Decimal = Field(default=Decimal("0.25"), ge=Decimal("0.15"), le=Decimal("0.35"))
    minimum_wick_ratio: Decimal = Field(default=Decimal("0.50"), gt=0, lt=1)
    maximum_sweep_atr: Decimal = Field(default=Decimal("2"), gt=0)
    expiry_bars: int = Field(default=2, ge=1, le=2)
    max_price_drift_bps: Decimal = Field(default=Decimal("20"), ge=0)
    expected_round_trip_cost_bps: Decimal = Field(default=Decimal("12"), ge=0)
    maximum_unstable_score: float = Field(default=0.8, ge=0, le=1)


def _feature_hash(
    *,
    context: MarketContext,
    regime: RegimeScore,
    config: FailedBreakoutConfig,
    side: Literal["long", "short"],
) -> str:
    payload = {
        "context": context.model_dump(mode="json"),
        "regime": regime.model_dump(mode="json"),
        "config": config.model_dump(mode="json"),
        "side": side,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _short_pattern(
    *,
    prior: tuple[ClosedBar, ...],
    sweep: ClosedBar,
    confirmation: ClosedBar,
    atr: Decimal,
    config: FailedBreakoutConfig,
) -> tuple[Decimal, Decimal, Decimal] | None:
    upper = max(bar.high for bar in prior)
    lower = min(bar.low for bar in prior)
    candle_range = sweep.high - sweep.low
    upper_wick = sweep.high - max(sweep.open, sweep.close)
    if (
        candle_range <= 0
        or sweep.high <= upper
        or sweep.close >= upper
        or upper_wick / candle_range < config.minimum_wick_ratio
        or (sweep.high - upper) / atr > config.maximum_sweep_atr
        or confirmation.low >= sweep.low
        or confirmation.close >= upper
    ):
        return None
    return upper, lower, upper_wick / candle_range


def _long_pattern(
    *,
    prior: tuple[ClosedBar, ...],
    sweep: ClosedBar,
    confirmation: ClosedBar,
    atr: Decimal,
    config: FailedBreakoutConfig,
) -> tuple[Decimal, Decimal, Decimal] | None:
    upper = max(bar.high for bar in prior)
    lower = min(bar.low for bar in prior)
    candle_range = sweep.high - sweep.low
    lower_wick = min(sweep.open, sweep.close) - sweep.low
    if (
        candle_range <= 0
        or sweep.low >= lower
        or sweep.close <= lower
        or lower_wick / candle_range < config.minimum_wick_ratio
        or (lower - sweep.low) / atr > config.maximum_sweep_atr
        or confirmation.high <= sweep.high
        or confirmation.close <= lower
    ):
        return None
    return upper, lower, lower_wick / candle_range


def _proposal(
    *,
    context: MarketContext,
    regime: RegimeScore,
    config: FailedBreakoutConfig,
    side: Literal["long", "short"],
    sweep: ClosedBar,
    confirmation: ClosedBar,
    upper: Decimal,
    lower: Decimal,
    wick_ratio: Decimal,
    atr: Decimal,
) -> StrategyProposal | None:
    entry = confirmation.close
    buffer = atr * config.atr_buffer
    stop = sweep.high + buffer if side == "short" else sweep.low - buffer
    risk = abs(entry - stop)
    midpoint = (upper + lower) / Decimal("2")
    two_r = entry - risk * 2 if side == "short" else entry + risk * 2
    opposite_boundary = lower if side == "short" else upper
    tp1 = midpoint
    tp2 = opposite_boundary
    if side == "short":
        tp1 = min(entry - risk * Decimal("0.5"), midpoint)
        tp2 = min(entry - risk, opposite_boundary)
    else:
        tp1 = max(entry + risk * Decimal("0.5"), midpoint)
        tp2 = max(entry + risk, opposite_boundary)
    cost = entry * config.expected_round_trip_cost_bps / Decimal("10000")
    cost_adjusted_rr = (abs(two_r - entry) - cost) / (risk + cost)
    if cost_adjusted_rr <= 0:
        return None
    signal_time = context.bars_15m.last_closed_at
    assert signal_time is not None
    regime_fit = max(regime.range, regime.trend_down if side == "short" else regime.trend_up)
    regime_fit *= 1 - regime.unstable
    volume_ratio = context.volume.volume_ratio or Decimal("0")
    setup_quality = min(1.0, float(wick_ratio) * 0.7 + min(1.0, float(volume_ratio) / 2) * 0.3)
    return StrategyProposal(
        proposal_id=f"{STRATEGY_ID}:{context.symbol}:{signal_time.isoformat()}:{side}",
        strategy_id=STRATEGY_ID,
        strategy_version=STRATEGY_VERSION,
        symbol=context.symbol,
        side=side,
        setup_type="failed_breakout_reversal",
        signal_bar_time=signal_time,
        expires_at=signal_time + timedelta(minutes=15 * config.expiry_bars),
        entry_trigger=EntryTrigger(
            entry_type="market_after_confirmation",
            reference_price=entry,
            confirmation_price=sweep.low if side == "short" else sweep.high,
            max_price_drift_bps=config.max_price_drift_bps,
        ),
        invalidation=InvalidationRule(
            stop_price=stop,
            extreme_price=sweep.high if side == "short" else sweep.low,
            reason="failed_breakout_extreme_breached",
        ),
        targets=(
            TargetRule(label="range_mid_or_half_r", price=tp1, quantity_fraction=Decimal("0.40")),
            TargetRule(label="opposite_boundary_or_one_r", price=tp2, quantity_fraction=Decimal("0.40")),
            TargetRule(label="two_r_runner", price=two_r, quantity_fraction=Decimal("0.20")),
        ),
        regime_fit=max(0.0, min(1.0, regime_fit)),
        setup_quality=setup_quality,
        cost_adjusted_rr=cost_adjusted_rr,
        confidence_components={
            "regime_fit": regime_fit,
            "wick_rejection": float(wick_ratio),
            "volume_exhaustion": min(1.0, float(volume_ratio) / 2),
        },
        feature_snapshot_hash=_feature_hash(context=context, regime=regime, config=config, side=side),
        reasons=("donchian_24_sweep_reclaimed", "next_closed_bar_confirmed"),
    )


def evaluate_failed_breakout_reversal(
    context: MarketContext,
    regime: RegimeScore,
    *,
    config: FailedBreakoutConfig | None = None,
) -> StrategyProposal | None:
    """Return one symmetric reversal proposal from closed 15m bars only."""

    config = config or FailedBreakoutConfig()
    window = context.bars_15m
    required = config.structure_lookback + 2
    if (
        len(window.bars) < required
        or window.gap_count
        or "15m" in context.freshness.stale_timeframes
        or window.last_closed_at is None
        or window.last_closed_at > context.decision_time
        or regime.unstable > config.maximum_unstable_score
    ):
        return None
    prior = window.bars[-required:-2]
    sweep, confirmation = window.bars[-2:]
    atr = context.volatility.atr_14
    if atr is None or atr <= 0:
        return None
    short = _short_pattern(prior=prior, sweep=sweep, confirmation=confirmation, atr=atr, config=config)
    long = _long_pattern(prior=prior, sweep=sweep, confirmation=confirmation, atr=atr, config=config)
    if short is not None and long is not None:
        return None
    if short is not None:
        return _proposal(
            context=context,
            regime=regime,
            config=config,
            side="short",
            sweep=sweep,
            confirmation=confirmation,
            upper=short[0],
            lower=short[1],
            wick_ratio=short[2],
            atr=atr,
        )
    if long is not None:
        return _proposal(
            context=context,
            regime=regime,
            config=config,
            side="long",
            sweep=sweep,
            confirmation=confirmation,
            upper=long[0],
            lower=long[1],
            wick_ratio=long[2],
            atr=atr,
        )
    return None
