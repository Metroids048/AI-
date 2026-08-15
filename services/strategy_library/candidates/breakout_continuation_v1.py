"""Closed-bar compression to expansion breakout continuation proposal."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Literal

from pydantic import Field

from services.strategy_library.canonical import canonical_hash
from services.strategy_library.context import FrozenContract, MarketContext
from services.strategy_library.proposals import EntryTrigger, InvalidationRule, StrategyProposal, TargetRule
from services.strategy_library.regime.scorer_v2 import RegimeScore

STRATEGY_ID = "breakout_continuation_v1"
STRATEGY_VERSION = "1.0.0-research"


class BreakoutContinuationConfig(FrozenContract):
    structure_lookback: int = Field(default=20, ge=10, le=80)
    minimum_volume_ratio: Decimal = Field(default=Decimal("1.25"), ge=Decimal("1"))
    minimum_body_range_ratio: Decimal = Field(default=Decimal("0.55"), gt=0, le=1)
    minimum_compression_score: float = Field(default=0.30, ge=0, le=1)
    minimum_expansion_score: float = Field(default=0.35, ge=0, le=1)
    minimum_breakout_atr: Decimal = Field(default=Decimal("0.10"), gt=0)
    stop_atr_buffer: Decimal = Field(default=Decimal("0.25"), gt=0)
    max_price_drift_bps: Decimal = Field(default=Decimal("20"), ge=0)
    expiry_bars: int = Field(default=2, ge=1, le=2)
    expected_round_trip_cost_bps: Decimal = Field(default=Decimal("12"), ge=0)


def evaluate_breakout_continuation(
    context: MarketContext,
    regime: RegimeScore,
    *,
    config: BreakoutContinuationConfig | None = None,
) -> StrategyProposal | None:
    config = config or BreakoutContinuationConfig()
    window = context.bars_15m
    required = config.structure_lookback + 1
    if (
        len(window.bars) < required
        or window.gap_count
        or "15m" in context.freshness.stale_timeframes
        or window.last_closed_at is None
        or window.last_closed_at > context.decision_time
    ):
        return None
    breakout = window.bars[-1]
    prior = window.bars[-required:-1]
    upper = max(bar.high for bar in prior)
    lower = min(bar.low for bar in prior)
    atr = context.volatility.atr_14
    mean_volume = sum((bar.volume for bar in prior), Decimal("0")) / Decimal(len(prior))
    candle_range = breakout.high - breakout.low
    body_ratio = abs(breakout.close - breakout.open) / candle_range if candle_range > 0 else Decimal("0")
    volume_ratio = breakout.volume / mean_volume if mean_volume > 0 else Decimal("0")
    if atr is None or atr <= 0 or candle_range <= 0:
        return None
    side: Literal["long", "short"] | None = None
    if breakout.close > upper and (breakout.close - upper) / atr >= config.minimum_breakout_atr:
        side = "long"
        if regime.evidence.get("direction_4h", 0.0) < -0.25:
            return None
    elif breakout.close < lower and (lower - breakout.close) / atr >= config.minimum_breakout_atr:
        side = "short"
        if regime.evidence.get("direction_4h", 0.0) > 0.25:
            return None
    if side is None or volume_ratio < config.minimum_volume_ratio or body_ratio < config.minimum_body_range_ratio:
        return None
    if regime.compression < config.minimum_compression_score or regime.expansion < config.minimum_expansion_score:
        return None
    entry = breakout.close
    stop = (
        breakout.low - atr * config.stop_atr_buffer if side == "long" else breakout.high + atr * config.stop_atr_buffer
    )
    risk = abs(entry - stop)
    targets = tuple(
        TargetRule(
            label=label,
            price=entry + risk * multiple if side == "long" else entry - risk * multiple,
            quantity_fraction=fraction,
        )
        for label, multiple, fraction in (
            ("one_r", Decimal("1"), Decimal("0.25")),
            ("two_point_two_r", Decimal("2.2"), Decimal("0.50")),
            ("three_r", Decimal("3"), Decimal("0.25")),
        )
    )
    signal_time = window.last_closed_at
    assert signal_time is not None
    return StrategyProposal(
        proposal_id=f"{STRATEGY_ID}:{context.symbol}:{signal_time.isoformat()}:{side}",
        strategy_id=STRATEGY_ID,
        strategy_version=STRATEGY_VERSION,
        symbol=context.symbol,
        side=side,
        setup_type="compression_breakout_continuation",
        signal_bar_time=signal_time,
        expires_at=signal_time + timedelta(minutes=15 * config.expiry_bars),
        entry_trigger=EntryTrigger(
            entry_type="market_after_confirmation",
            reference_price=entry,
            max_price_drift_bps=config.max_price_drift_bps,
        ),
        invalidation=InvalidationRule(
            stop_price=stop,
            extreme_price=breakout.low if side == "long" else breakout.high,
            reason="breakout_invalidation",
        ),
        targets=targets,
        regime_fit=max(regime.trend_up, regime.trend_down),
        setup_quality=min(1.0, float(body_ratio) * 0.5 + min(1.0, float(volume_ratio) / 2) * 0.5),
        cost_adjusted_rr=(risk * Decimal("2.2") - entry * config.expected_round_trip_cost_bps / Decimal("10000"))
        / (risk + entry * config.expected_round_trip_cost_bps / Decimal("10000")),
        confidence_components={
            "volume_expansion": min(1.0, float(volume_ratio) / 2),
            "body_quality": float(body_ratio),
            "regime_expansion": regime.expansion,
        },
        feature_snapshot_hash=canonical_hash(
            {
                "context": context.model_dump(mode="json"),
                "regime": regime.model_dump(mode="json"),
                "config": config.model_dump(mode="json"),
                "side": side,
            }
        ),
        reasons=("compression_release", "closed_bar_structure_break", "volume_confirmation"),
    )
