"""Structure breakout followed by a held retest and continuation confirmation."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Literal

from pydantic import Field

from services.strategy_library.canonical import canonical_hash
from services.strategy_library.context import ClosedBar, FrozenContract, MarketContext
from services.strategy_library.proposals import EntryTrigger, InvalidationRule, StrategyProposal, TargetRule
from services.strategy_library.regime.scorer_v2 import RegimeScore

STRATEGY_ID = "breakout_retest_v1"
STRATEGY_VERSION = "1.0.0-generation-1"


class BreakoutRetestConfig(FrozenContract):
    structure_lookback: int = Field(default=20, ge=12, le=40)
    breakout_buffer_atr: Decimal = Field(default=Decimal("0.15"), ge=Decimal("0.05"), le=Decimal("0.50"))
    retest_tolerance_atr: Decimal = Field(default=Decimal("0.35"), ge=Decimal("0.10"), le=Decimal("0.80"))
    stop_atr_buffer: Decimal = Field(default=Decimal("0.25"), ge=Decimal("0.15"), le=Decimal("0.35"))
    target_rr: Decimal = Field(default=Decimal("2.0"), ge=Decimal("1.5"), le=Decimal("3.0"))
    minimum_volume_ratio: Decimal = Field(default=Decimal("1.15"), ge=Decimal("0.80"), le=Decimal("2.50"))
    minimum_expansion: float = Field(default=0.08, ge=0, le=0.50)
    expected_round_trip_cost_bps: Decimal = Field(default=Decimal("12"), ge=Decimal("0"), le=Decimal("50"))
    max_price_drift_bps: Decimal = Field(default=Decimal("20"), ge=Decimal("0"), le=Decimal("80"))


def _feature_hash(*, context: MarketContext, regime: RegimeScore, config: BreakoutRetestConfig, side: str) -> str:
    return canonical_hash(
        {
            "context": context.model_dump(mode="json"),
            "regime": regime.model_dump(mode="json"),
            "config": config.model_dump(mode="json"),
            "side": side,
        }
    )


def _proposal(
    *,
    context: MarketContext,
    regime: RegimeScore,
    config: BreakoutRetestConfig,
    side: Literal["long", "short"],
    level: Decimal,
    breakout: ClosedBar,
    retest: ClosedBar,
    confirmation: ClosedBar,
    atr: Decimal,
) -> StrategyProposal | None:
    entry = confirmation.close
    stop = retest.low - atr * config.stop_atr_buffer if side == "long" else retest.high + atr * config.stop_atr_buffer
    risk = abs(entry - stop)
    if risk <= 0:
        return None
    target = entry + risk * config.target_rr if side == "long" else entry - risk * config.target_rr
    cost = entry * config.expected_round_trip_cost_bps / Decimal("10000")
    cost_adjusted_rr = (risk * config.target_rr - cost) / (risk + cost)
    if cost_adjusted_rr < Decimal("1.50"):
        return None
    signal_time = context.bars_15m.last_closed_at
    assert signal_time is not None
    return StrategyProposal(
        proposal_id=f"{STRATEGY_ID}:{context.symbol}:{signal_time.isoformat()}:{side}",
        strategy_id=STRATEGY_ID,
        strategy_version=STRATEGY_VERSION,
        symbol=context.symbol,
        side=side,
        setup_type="breakout_retest",
        signal_bar_time=signal_time,
        expires_at=signal_time + timedelta(minutes=30),
        entry_trigger=EntryTrigger(
            entry_type="limit_retest",
            reference_price=entry,
            confirmation_price=level,
            max_price_drift_bps=config.max_price_drift_bps,
        ),
        invalidation=InvalidationRule(
            stop_price=stop,
            extreme_price=retest.low if side == "long" else retest.high,
            reason="breakout_retest_failed",
        ),
        targets=(TargetRule(label=f"{config.target_rr}R", price=target, quantity_fraction=Decimal("1")),),
        regime_fit=max(regime.trend_up, regime.trend_down, regime.expansion),
        setup_quality=min(
            1.0,
            float(regime.expansion) * 0.5
            + min(0.5, float(breakout.volume / max(retest.volume, Decimal("0.00000001"))) / 2),
        ),
        cost_adjusted_rr=cost_adjusted_rr,
        confidence_components={
            "breakout_close_distance_atr": float(abs(breakout.close - level) / atr),
            "retest_hold_distance_atr": float(abs(retest.close - level) / atr),
            "volume_ratio": float(breakout.volume / max(retest.volume, Decimal("0.00000001"))),
            "expansion": regime.expansion,
        },
        feature_snapshot_hash=_feature_hash(context=context, regime=regime, config=config, side=side),
        reasons=("closed_structure_breakout", "retest_held", "continuation_confirmation"),
    )


def evaluate_breakout_retest(
    context: MarketContext, regime: RegimeScore, *, config: BreakoutRetestConfig | None = None
) -> StrategyProposal | None:
    config = config or BreakoutRetestConfig()
    if context.freshness.has_gaps or context.freshness.stale_timeframes or context.volatility.atr_14 is None:
        return None
    bars = context.bars_15m.bars
    bars_1h = context.bars_1h.bars
    bars_4h = context.bars_4h.bars
    if len(bars) < config.structure_lookback + 4 or len(bars_1h) < 20 or len(bars_4h) < 10:
        return None
    atr = context.volatility.atr_14
    assert atr is not None and atr > 0
    prior = bars[-(config.structure_lookback + 3) : -3]
    level_high = max(bar.high for bar in prior)
    level_low = min(bar.low for bar in prior)
    breakout, retest, confirmation = bars[-3:]
    average_volume = sum((bar.volume for bar in prior), Decimal("0")) / Decimal(len(prior))
    volume_ratio = breakout.volume / max(average_volume, Decimal("0.00000001"))
    if volume_ratio < config.minimum_volume_ratio or regime.expansion < config.minimum_expansion:
        return None
    long = (
        breakout.close > level_high + config.breakout_buffer_atr * atr
        and retest.low <= level_high + config.retest_tolerance_atr * atr
        and retest.close > level_high
        and confirmation.close > retest.high
        and regime.trend_down < 0.55
    )
    short = (
        breakout.close < level_low - config.breakout_buffer_atr * atr
        and retest.high >= level_low - config.retest_tolerance_atr * atr
        and retest.close < level_low
        and confirmation.close < retest.low
        and regime.trend_up < 0.55
    )
    if long == short:
        return None
    return _proposal(
        context=context,
        regime=regime,
        config=config,
        side="long" if long else "short",
        level=level_high if long else level_low,
        breakout=breakout,
        retest=retest,
        confirmation=confirmation,
        atr=atr,
    )


__all__ = ["BreakoutRetestConfig", "STRATEGY_ID", "STRATEGY_VERSION", "evaluate_breakout_retest"]
