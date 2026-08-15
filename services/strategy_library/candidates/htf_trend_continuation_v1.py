"""Higher-timeframe trend continuation with a 15m recovery trigger."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Literal

from pydantic import Field

from services.strategy_library.canonical import canonical_hash
from services.strategy_library.context import ClosedBar, FrozenContract, MarketContext
from services.strategy_library.proposals import EntryTrigger, InvalidationRule, StrategyProposal, TargetRule
from services.strategy_library.regime.scorer_v2 import RegimeScore

STRATEGY_ID = "htf_trend_continuation_v1"
STRATEGY_VERSION = "1.0.0-generation-1"


class HTFTrendContinuationConfig(FrozenContract):
    structure_lookback: int = Field(default=12, ge=8, le=24)
    ema_fast_period: int = Field(default=20, ge=10, le=30)
    ema_slow_period: int = Field(default=50, ge=30, le=80)
    minimum_htf_score: float = Field(default=0.58, ge=0.45, le=0.85)
    stop_atr_buffer: Decimal = Field(default=Decimal("0.25"), ge=Decimal("0.15"), le=Decimal("0.35"))
    target_rr: Decimal = Field(default=Decimal("2.0"), ge=Decimal("1.5"), le=Decimal("3.0"))
    minimum_impulse_atr: Decimal = Field(default=Decimal("0.45"), ge=Decimal("0.20"), le=Decimal("1.20"))
    maximum_impulse_atr: Decimal = Field(default=Decimal("2.2"), ge=Decimal("1.20"), le=Decimal("4.0"))
    minimum_volume_ratio: Decimal = Field(default=Decimal("1.05"), ge=Decimal("0.80"), le=Decimal("2.0"))
    expected_round_trip_cost_bps: Decimal = Field(default=Decimal("12"), ge=Decimal("0"), le=Decimal("50"))
    max_price_drift_bps: Decimal = Field(default=Decimal("20"), ge=Decimal("0"), le=Decimal("80"))


def _ema(values: list[Decimal], period: int) -> Decimal:
    multiplier = Decimal("2") / Decimal(period + 1)
    result = values[0]
    for value in values[1:]:
        result = (value - result) * multiplier + result
    return result


def _structure_direction(bars: tuple[ClosedBar, ...], lookback: int) -> Literal["long", "short", "none"]:
    sample = bars[-lookback:]
    if len(sample) < lookback:
        return "none"
    midpoint = len(sample) // 2
    first, second = sample[:midpoint], sample[midpoint:]
    if max(bar.high for bar in second) > max(bar.high for bar in first) and min(bar.low for bar in second) > min(
        bar.low for bar in first
    ):
        return "long"
    if min(bar.low for bar in second) < min(bar.low for bar in first) and max(bar.high for bar in second) < max(
        bar.high for bar in first
    ):
        return "short"
    return "none"


def _feature_hash(*, context: MarketContext, regime: RegimeScore, config: HTFTrendContinuationConfig, side: str) -> str:
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
    config: HTFTrendContinuationConfig,
    side: Literal["long", "short"],
    impulse: ClosedBar,
    pullback: ClosedBar,
    confirmation: ClosedBar,
    atr: Decimal,
) -> StrategyProposal | None:
    entry = confirmation.close
    stop = (
        pullback.low - atr * config.stop_atr_buffer if side == "long" else pullback.high + atr * config.stop_atr_buffer
    )
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
        setup_type="htf_trend_continuation",
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
            extreme_price=pullback.low if side == "long" else pullback.high,
            reason="continuation_pullback_invalidated",
        ),
        targets=(TargetRule(label=f"{config.target_rr}R", price=target, quantity_fraction=Decimal("1")),),
        regime_fit=max(regime.trend_up, regime.trend_down),
        setup_quality=min(
            1.0,
            float(config.minimum_volume_ratio / max(config.minimum_volume_ratio, Decimal("1"))) * 0.5
            + min(
                0.5,
                float(impulse.close - impulse.open if side == "long" else impulse.open - impulse.close) / float(atr),
            ),
        ),
        cost_adjusted_rr=cost_adjusted_rr,
        confidence_components={
            "htf_structure": 1.0,
            "htf_trend_score": regime.trend_up if side == "long" else regime.trend_down,
            "impulse_atr": float(abs(impulse.close - impulse.open) / atr),
            "volume_ratio": float(impulse.volume / max(pullback.volume, Decimal("0.00000001"))),
        },
        feature_snapshot_hash=_feature_hash(context=context, regime=regime, config=config, side=side),
        reasons=("4h_structure", "1h_ema_alignment", "15m_impulse_pullback_recovery"),
    )


def evaluate_htf_trend_continuation(
    context: MarketContext, regime: RegimeScore, *, config: HTFTrendContinuationConfig | None = None
) -> StrategyProposal | None:
    config = config or HTFTrendContinuationConfig()
    if context.freshness.has_gaps or context.freshness.stale_timeframes or context.volatility.atr_14 is None:
        return None
    bars_4h = context.bars_4h.bars
    bars_1h = context.bars_1h.bars
    bars_15m = context.bars_15m.bars
    if len(bars_4h) < config.structure_lookback or len(bars_1h) < config.ema_slow_period + 5 or len(bars_15m) < 8:
        return None
    atr = context.volatility.atr_14
    assert atr is not None and atr > 0
    direction_4h = _structure_direction(bars_4h, config.structure_lookback)
    ema_fast = _ema([bar.close for bar in bars_1h[-(config.ema_slow_period + 5) :]], config.ema_fast_period)
    ema_slow = _ema([bar.close for bar in bars_1h[-(config.ema_slow_period + 5) :]], config.ema_slow_period)
    ema_fast_prev = _ema([bar.close for bar in bars_1h[-(config.ema_slow_period + 6) : -1]], config.ema_fast_period)
    htf_long = (
        direction_4h == "long"
        and ema_fast > ema_slow
        and ema_fast >= ema_fast_prev
        and regime.trend_up >= config.minimum_htf_score
    )
    htf_short = (
        direction_4h == "short"
        and ema_fast < ema_slow
        and ema_fast <= ema_fast_prev
        and regime.trend_down >= config.minimum_htf_score
    )
    impulse, pullback, confirmation = bars_15m[-4:]
    impulse_body = abs(impulse.close - impulse.open)
    impulse_ratio = impulse_body / atr
    volume_ratio = impulse.volume / max(
        sum((bar.volume for bar in bars_15m[-12:-4]), Decimal("0")) / Decimal("8"), Decimal("0.00000001")
    )
    if not (
        config.minimum_impulse_atr <= impulse_ratio <= config.maximum_impulse_atr
        and volume_ratio >= config.minimum_volume_ratio
    ):
        return None
    long = (
        htf_long
        and impulse.close > impulse.open
        and pullback.close <= impulse.close
        and pullback.low >= impulse.low - atr
        and confirmation.close > pullback.high
        and confirmation.close > confirmation.open
    )
    short = (
        htf_short
        and impulse.close < impulse.open
        and pullback.close >= impulse.close
        and pullback.high <= impulse.high + atr
        and confirmation.close < pullback.low
        and confirmation.close < confirmation.open
    )
    if long == short:
        return None
    return _proposal(
        context=context,
        regime=regime,
        config=config,
        side="long" if long else "short",
        impulse=impulse,
        pullback=pullback,
        confirmation=confirmation,
        atr=atr,
    )


__all__ = ["HTFTrendContinuationConfig", "STRATEGY_ID", "STRATEGY_VERSION", "evaluate_htf_trend_continuation"]
