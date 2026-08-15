"""Multi-bar momentum continuation candidate for the final research generation."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Literal

from pydantic import Field

from services.strategy_library.canonical import canonical_hash
from services.strategy_library.context import FrozenContract, MarketContext
from services.strategy_library.proposals import EntryTrigger, InvalidationRule, StrategyProposal, TargetRule
from services.strategy_library.regime.scorer_v2 import RegimeScore

STRATEGY_ID = "momentum_continuation_v1"
STRATEGY_VERSION = "1.0.0-generation-5"


class MomentumContinuationConfig(FrozenContract):
    momentum_bars: int = Field(default=3, ge=2, le=5)
    minimum_move_atr: Decimal = Field(default=Decimal("0.90"), ge=Decimal("0.40"), le=Decimal("2.0"))
    stop_atr_buffer: Decimal = Field(default=Decimal("0.25"), ge=Decimal("0.15"), le=Decimal("0.35"))
    target_rr: Decimal = Field(default=Decimal("2.0"), ge=Decimal("1.5"), le=Decimal("3.0"))
    expected_round_trip_cost_bps: Decimal = Field(default=Decimal("12"), ge=Decimal("0"), le=Decimal("50"))
    max_price_drift_bps: Decimal = Field(default=Decimal("20"), ge=Decimal("0"), le=Decimal("80"))


def evaluate_momentum_continuation(
    context: MarketContext, regime: RegimeScore, *, config: MomentumContinuationConfig | None = None
) -> StrategyProposal | None:
    config = config or MomentumContinuationConfig()
    if context.freshness.has_gaps or context.freshness.stale_timeframes or context.volatility.atr_14 is None:
        return None
    bars = context.bars_15m.bars
    if len(bars) < 12 or len(context.bars_1h.bars) < 55:
        return None
    atr = context.volatility.atr_14
    assert atr is not None and atr > 0
    momentum = bars[-(config.momentum_bars + 1) : -1]
    confirmation = bars[-1]
    move = momentum[-1].close - momentum[0].open
    avg_volume = sum((bar.volume for bar in bars[-12:-4]), Decimal("0")) / Decimal("8")
    fast = sum((bar.close for bar in context.bars_1h.bars[-20:]), Decimal("0")) / Decimal("20")
    slow = sum((bar.close for bar in context.bars_1h.bars[-50:]), Decimal("0")) / Decimal("50")
    long = move >= atr * config.minimum_move_atr and confirmation.close > momentum[-1].high and fast > slow
    short = move <= -atr * config.minimum_move_atr and confirmation.close < momentum[-1].low and fast < slow
    if long == short or confirmation.volume < avg_volume:
        return None
    side: Literal["long", "short"] = "long" if long else "short"
    entry = confirmation.close
    stop = (
        min(bar.low for bar in momentum) - atr * config.stop_atr_buffer
        if long
        else max(bar.high for bar in momentum) + atr * config.stop_atr_buffer
    )
    risk = abs(entry - stop)
    cost = entry * config.expected_round_trip_cost_bps / Decimal("10000")
    cost_adjusted_rr = (risk * config.target_rr - cost) / (risk + cost)
    if risk <= 0 or cost_adjusted_rr < Decimal("1.50"):
        return None
    target = entry + risk * config.target_rr if long else entry - risk * config.target_rr
    signal_time = context.bars_15m.last_closed_at
    assert signal_time is not None
    return StrategyProposal(
        proposal_id=f"{STRATEGY_ID}:{context.symbol}:{signal_time.isoformat()}:{side}",
        strategy_id=STRATEGY_ID,
        strategy_version=STRATEGY_VERSION,
        symbol=context.symbol,
        side=side,
        setup_type="momentum_continuation",
        signal_bar_time=signal_time,
        expires_at=signal_time + timedelta(minutes=30),
        entry_trigger=EntryTrigger(
            entry_type="market_after_confirmation",
            reference_price=entry,
            confirmation_price=confirmation.close,
            max_price_drift_bps=config.max_price_drift_bps,
        ),
        invalidation=InvalidationRule(
            stop_price=stop,
            extreme_price=min(bar.low for bar in momentum) if long else max(bar.high for bar in momentum),
            reason="momentum_continuation_failed",
        ),
        targets=(TargetRule(label=f"{config.target_rr}R", price=target, quantity_fraction=Decimal("1")),),
        regime_fit=max(regime.trend_up, regime.trend_down, regime.expansion),
        setup_quality=min(1.0, float(abs(move) / atr) / 2),
        cost_adjusted_rr=cost_adjusted_rr,
        confidence_components={
            "momentum_atr": float(abs(move) / atr),
            "volume_ratio": float(confirmation.volume / max(avg_volume, Decimal("1e-8"))),
        },
        feature_snapshot_hash=canonical_hash(
            {"context": context.model_dump(mode="json"), "regime": regime.model_dump(mode="json"), "side": side}
        ),
        reasons=("multi_bar_momentum", "higher_timeframe_alignment", "continuation_confirmation"),
    )


__all__ = ["MomentumContinuationConfig", "STRATEGY_ID", "STRATEGY_VERSION", "evaluate_momentum_continuation"]
