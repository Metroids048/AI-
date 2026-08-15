"""Compression-to-expansion continuation candidate for Generation 3 research."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Literal

from pydantic import Field

from services.strategy_library.canonical import canonical_hash
from services.strategy_library.context import FrozenContract, MarketContext
from services.strategy_library.proposals import EntryTrigger, InvalidationRule, StrategyProposal, TargetRule
from services.strategy_library.regime.scorer_v2 import RegimeScore

STRATEGY_ID = "volatility_expansion_v1"
STRATEGY_VERSION = "1.0.0-generation-3"


class VolatilityExpansionConfig(FrozenContract):
    compression_bars: int = Field(default=6, ge=4, le=12)
    compression_ratio: Decimal = Field(default=Decimal("0.80"), ge=Decimal("0.50"), le=Decimal("1.0"))
    breakout_body_atr: Decimal = Field(default=Decimal("0.80"), ge=Decimal("0.40"), le=Decimal("2.0"))
    breakout_buffer_atr: Decimal = Field(default=Decimal("0.10"), ge=Decimal("0.05"), le=Decimal("0.50"))
    minimum_volume_ratio: Decimal = Field(default=Decimal("1.20"), ge=Decimal("0.80"), le=Decimal("3.0"))
    stop_atr_buffer: Decimal = Field(default=Decimal("0.25"), ge=Decimal("0.15"), le=Decimal("0.35"))
    target_rr: Decimal = Field(default=Decimal("2.0"), ge=Decimal("1.5"), le=Decimal("3.0"))
    expected_round_trip_cost_bps: Decimal = Field(default=Decimal("12"), ge=Decimal("0"), le=Decimal("50"))
    max_price_drift_bps: Decimal = Field(default=Decimal("20"), ge=Decimal("0"), le=Decimal("80"))


def _feature_hash(*, context: MarketContext, regime: RegimeScore, config: VolatilityExpansionConfig, side: str) -> str:
    return canonical_hash(
        {
            "context": context.model_dump(mode="json"),
            "regime": regime.model_dump(mode="json"),
            "config": config.model_dump(mode="json"),
            "side": side,
        }
    )


def evaluate_volatility_expansion(
    context: MarketContext, regime: RegimeScore, *, config: VolatilityExpansionConfig | None = None
) -> StrategyProposal | None:
    config = config or VolatilityExpansionConfig()
    if context.freshness.has_gaps or context.freshness.stale_timeframes or context.volatility.atr_14 is None:
        return None
    bars = context.bars_15m.bars
    if len(bars) < config.compression_bars + 4 or len(context.bars_1h.bars) < 20 or len(context.bars_4h.bars) < 10:
        return None
    atr = context.volatility.atr_14
    assert atr is not None and atr > 0
    compression = bars[-(config.compression_bars + 3) : -3]
    breakout, confirmation = bars[-2], bars[-1]
    compressed_range = max(bar.high for bar in compression) - min(bar.low for bar in compression)
    if compressed_range > atr * config.compression_bars * config.compression_ratio:
        return None
    level_high, level_low = max(bar.high for bar in compression), min(bar.low for bar in compression)
    body = abs(breakout.close - breakout.open)
    average_volume = sum((bar.volume for bar in compression), Decimal("0")) / Decimal(len(compression))
    if body < atr * config.breakout_body_atr or breakout.volume < average_volume * config.minimum_volume_ratio:
        return None
    htf_direction = "none"
    bars4h = context.bars_4h.bars[-10:]
    if max(bar.high for bar in bars4h[5:]) > max(bar.high for bar in bars4h[:5]) and min(
        bar.low for bar in bars4h[5:]
    ) > min(bar.low for bar in bars4h[:5]):
        htf_direction = "long"
    elif min(bar.low for bar in bars4h[5:]) < min(bar.low for bar in bars4h[:5]) and max(
        bar.high for bar in bars4h[5:]
    ) < max(bar.high for bar in bars4h[:5]):
        htf_direction = "short"
    long = (
        breakout.close > level_high + atr * config.breakout_buffer_atr
        and confirmation.close > breakout.close
        and htf_direction != "short"
        and regime.trend_down < 0.60
    )
    short = (
        breakout.close < level_low - atr * config.breakout_buffer_atr
        and confirmation.close < breakout.close
        and htf_direction != "long"
        and regime.trend_up < 0.60
    )
    if long == short:
        return None
    side: Literal["long", "short"] = "long" if long else "short"
    entry = confirmation.close
    stop = breakout.low - atr * config.stop_atr_buffer if long else breakout.high + atr * config.stop_atr_buffer
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
        setup_type="volatility_expansion",
        signal_bar_time=signal_time,
        expires_at=signal_time + timedelta(minutes=30),
        entry_trigger=EntryTrigger(
            entry_type="market_after_confirmation",
            reference_price=entry,
            confirmation_price=breakout.close,
            max_price_drift_bps=config.max_price_drift_bps,
        ),
        invalidation=InvalidationRule(
            stop_price=stop,
            extreme_price=breakout.low if long else breakout.high,
            reason="volatility_expansion_failed",
        ),
        targets=(TargetRule(label=f"{config.target_rr}R", price=target, quantity_fraction=Decimal("1")),),
        regime_fit=max(regime.expansion, regime.trend_up, regime.trend_down),
        setup_quality=min(
            1.0, float(body / atr) / 2 + float(breakout.volume / max(average_volume, Decimal("1e-8"))) / 4
        ),
        cost_adjusted_rr=cost_adjusted_rr,
        confidence_components={
            "compression_ratio": float(compressed_range / max(atr * config.compression_bars, Decimal("1e-8"))),
            "breakout_body_atr": float(body / atr),
            "volume_ratio": float(breakout.volume / max(average_volume, Decimal("1e-8"))),
            "expansion": regime.expansion,
        },
        feature_snapshot_hash=_feature_hash(context=context, regime=regime, config=config, side=side),
        reasons=("compressed_structure", "closed_volatility_breakout", "continuation_confirmation"),
    )


__all__ = ["STRATEGY_ID", "STRATEGY_VERSION", "VolatilityExpansionConfig", "evaluate_volatility_expansion"]
