"""Point-in-time range liquidity-sweep reversion proposal generator."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Literal

from pydantic import Field

from services.strategy_library.canonical import canonical_hash
from services.strategy_library.context import ClosedBar, FrozenContract, MarketContext
from services.strategy_library.proposals import EntryTrigger, InvalidationRule, StrategyProposal, TargetRule
from services.strategy_library.regime.scorer_v2 import RegimeScore

STRATEGY_ID = "range_sweep_reversion_v1"
STRATEGY_VERSION = "1.0.0-research"


class RangeSweepConfig(FrozenContract):
    structure_lookback: int = Field(default=24, ge=12, le=72)
    minimum_boundary_touches: int = Field(default=2, ge=2)
    atr_buffer: Decimal = Field(default=Decimal("0.25"), ge=Decimal("0.15"), le=Decimal("0.35"))
    minimum_range_score: float = Field(default=0.60, ge=0, le=1)
    maximum_trend_score: float = Field(default=0.50, ge=0, le=1)
    maximum_expansion_score: float = Field(default=0.70, ge=0, le=1)
    expiry_bars: int = Field(default=2, ge=1, le=2)
    max_price_drift_bps: Decimal = Field(default=Decimal("20"), ge=0)
    expected_round_trip_cost_bps: Decimal = Field(default=Decimal("12"), ge=0)


def _feature_hash(
    *, context: MarketContext, regime: RegimeScore, config: RangeSweepConfig, side: Literal["long", "short"]
) -> str:
    payload = {
        "context": context.model_dump(mode="json"),
        "regime": regime.model_dump(mode="json"),
        "config": config.model_dump(mode="json"),
        "side": side,
    }
    return canonical_hash(payload)


def _proposal(
    *,
    context: MarketContext,
    regime: RegimeScore,
    config: RangeSweepConfig,
    side: Literal["long", "short"],
    sweep: ClosedBar,
    confirmation: ClosedBar,
    upper: Decimal,
    lower: Decimal,
    atr: Decimal,
) -> StrategyProposal | None:
    entry = confirmation.close
    stop = sweep.low - atr * config.atr_buffer if side == "long" else sweep.high + atr * config.atr_buffer
    midpoint = (upper + lower) / Decimal("2")
    range_width = upper - lower
    runner = upper + range_width * Decimal("0.5") if side == "long" else lower - range_width * Decimal("0.5")
    cost = entry * config.expected_round_trip_cost_bps / Decimal("10000")
    risk = abs(entry - stop)
    opposite = upper if side == "long" else lower
    cost_adjusted_rr = (abs(opposite - entry) - cost) / (risk + cost)
    if cost_adjusted_rr <= 0:
        return None
    signal_time = context.bars_15m.last_closed_at
    assert signal_time is not None
    return StrategyProposal(
        proposal_id=f"{STRATEGY_ID}:{context.symbol}:{signal_time.isoformat()}:{side}",
        strategy_id=STRATEGY_ID,
        strategy_version=STRATEGY_VERSION,
        symbol=context.symbol,
        side=side,
        setup_type="range_liquidity_sweep_reversion",
        signal_bar_time=signal_time,
        expires_at=signal_time + timedelta(minutes=15 * config.expiry_bars),
        entry_trigger=EntryTrigger(
            entry_type="market_after_confirmation",
            reference_price=entry,
            confirmation_price=sweep.high if side == "long" else sweep.low,
            max_price_drift_bps=config.max_price_drift_bps,
        ),
        invalidation=InvalidationRule(
            stop_price=stop,
            extreme_price=sweep.low if side == "long" else sweep.high,
            reason="range_sweep_extreme_breached",
        ),
        targets=(
            TargetRule(label="range_midpoint", price=midpoint, quantity_fraction=Decimal("0.40")),
            TargetRule(label="opposite_range_boundary", price=opposite, quantity_fraction=Decimal("0.40")),
            TargetRule(label="confirmed_range_break_runner", price=runner, quantity_fraction=Decimal("0.20")),
        ),
        regime_fit=regime.range * (1 - regime.expansion),
        setup_quality=min(1.0, regime.range + min(0.2, float(context.volume.volume_ratio or Decimal("0")) / 10)),
        cost_adjusted_rr=cost_adjusted_rr,
        confidence_components={
            "range_score": regime.range,
            "trend_penalty": 1 - max(regime.trend_up, regime.trend_down),
            "expansion_penalty": 1 - regime.expansion,
        },
        feature_snapshot_hash=_feature_hash(context=context, regime=regime, config=config, side=side),
        reasons=("donchian_24_range_sweep_reclaimed", "next_closed_bar_reversal_confirmed"),
    )


def evaluate_range_sweep_reversion(
    context: MarketContext,
    regime: RegimeScore,
    *,
    config: RangeSweepConfig | None = None,
) -> StrategyProposal | None:
    """Return a bounded symmetric reversion proposal from closed 15m bars."""

    config = config or RangeSweepConfig()
    window = context.bars_15m
    required = config.structure_lookback + 2
    if (
        len(window.bars) < required
        or window.gap_count
        or "15m" in context.freshness.stale_timeframes
        or window.last_closed_at is None
        or window.last_closed_at > context.decision_time
        or regime.range < config.minimum_range_score
        or max(regime.trend_up, regime.trend_down) > config.maximum_trend_score
        or regime.expansion > config.maximum_expansion_score
    ):
        return None
    atr = context.volatility.atr_14
    if atr is None or atr <= 0:
        return None
    prior = window.bars[-required:-2]
    sweep, confirmation = window.bars[-2:]
    upper = max(bar.high for bar in prior)
    lower = min(bar.low for bar in prior)
    upper_touches = sum(bar.high == upper for bar in prior)
    lower_touches = sum(bar.low == lower for bar in prior)
    if upper_touches < config.minimum_boundary_touches or lower_touches < config.minimum_boundary_touches:
        return None
    long = sweep.low < lower and sweep.close > lower and confirmation.high > sweep.high and confirmation.close > lower
    short = sweep.high > upper and sweep.close < upper and confirmation.low < sweep.low and confirmation.close < upper
    if long == short:
        return None
    return _proposal(
        context=context,
        regime=regime,
        config=config,
        side="long" if long else "short",
        sweep=sweep,
        confirmation=confirmation,
        upper=upper,
        lower=lower,
        atr=atr,
    )
