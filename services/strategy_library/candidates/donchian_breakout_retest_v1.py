"""Donchian channel breakout with a retest confirmation for research only."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Literal

from pydantic import Field

from services.strategy_library.canonical import canonical_hash
from services.strategy_library.context import FrozenContract, MarketContext
from services.strategy_library.proposals import EntryTrigger, InvalidationRule, StrategyProposal, TargetRule
from services.strategy_library.regime.scorer_v2 import RegimeScore

STRATEGY_ID = "donchian_breakout_retest_v1"
STRATEGY_VERSION = "1.0.0-generation-4"


class DonchianBreakoutRetestConfig(FrozenContract):
    channel_bars: int = Field(default=32, ge=20, le=60)
    retest_tolerance_atr: Decimal = Field(default=Decimal("0.25"), ge=Decimal("0.10"), le=Decimal("0.60"))
    stop_atr_buffer: Decimal = Field(default=Decimal("0.25"), ge=Decimal("0.15"), le=Decimal("0.35"))
    target_rr: Decimal = Field(default=Decimal("2.2"), ge=Decimal("1.5"), le=Decimal("3.0"))
    minimum_volume_ratio: Decimal = Field(default=Decimal("1.10"), ge=Decimal("0.80"), le=Decimal("2.50"))
    expected_round_trip_cost_bps: Decimal = Field(default=Decimal("12"), ge=Decimal("0"), le=Decimal("50"))
    max_price_drift_bps: Decimal = Field(default=Decimal("20"), ge=Decimal("0"), le=Decimal("80"))


def evaluate_donchian_breakout_retest(
    context: MarketContext, regime: RegimeScore, *, config: DonchianBreakoutRetestConfig | None = None
) -> StrategyProposal | None:
    config = config or DonchianBreakoutRetestConfig()
    if context.freshness.has_gaps or context.freshness.stale_timeframes or context.volatility.atr_14 is None:
        return None
    bars = context.bars_15m.bars
    if len(bars) < config.channel_bars + 4:
        return None
    atr = context.volatility.atr_14
    assert atr is not None and atr > 0
    channel = bars[-(config.channel_bars + 3) : -3]
    breakout, retest, confirmation = bars[-3:]
    high, low = max(bar.high for bar in channel), min(bar.low for bar in channel)
    average_volume = sum((bar.volume for bar in channel), Decimal("0")) / Decimal(len(channel))
    long = (
        breakout.close > high
        and breakout.volume >= average_volume * config.minimum_volume_ratio
        and retest.low <= high + atr * config.retest_tolerance_atr
        and retest.close > high
        and confirmation.close > retest.high
        and regime.trend_down < 0.55
    )
    short = (
        breakout.close < low
        and breakout.volume >= average_volume * config.minimum_volume_ratio
        and retest.high >= low - atr * config.retest_tolerance_atr
        and retest.close < low
        and confirmation.close < retest.low
        and regime.trend_up < 0.55
    )
    if long == short:
        return None
    side: Literal["long", "short"] = "long" if long else "short"
    entry = confirmation.close
    stop = retest.low - atr * config.stop_atr_buffer if long else retest.high + atr * config.stop_atr_buffer
    risk = abs(entry - stop)
    if risk <= 0:
        return None
    target = entry + risk * config.target_rr if long else entry - risk * config.target_rr
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
        setup_type="donchian_breakout_retest",
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
            extreme_price=retest.low if long else retest.high,
            reason="donchian_retest_failed",
        ),
        targets=(TargetRule(label=f"{config.target_rr}R", price=target, quantity_fraction=Decimal("1")),),
        regime_fit=max(regime.trend_up, regime.trend_down, regime.expansion),
        setup_quality=min(1.0, float(breakout.volume / max(average_volume, Decimal("1e-8"))) / 2),
        cost_adjusted_rr=cost_adjusted_rr,
        confidence_components={
            "channel_bars": float(config.channel_bars),
            "breakout_volume_ratio": float(breakout.volume / max(average_volume, Decimal("1e-8"))),
            "retest_distance_atr": float(abs(retest.close - (high if long else low)) / atr),
        },
        feature_snapshot_hash=canonical_hash(
            {"context": context.model_dump(mode="json"), "regime": regime.model_dump(mode="json"), "side": side}
        ),
        reasons=("donchian_channel_breakout", "retest_held", "continuation_confirmation"),
    )


__all__ = ["DonchianBreakoutRetestConfig", "STRATEGY_ID", "STRATEGY_VERSION", "evaluate_donchian_breakout_retest"]
