"""Leakage-resistant Event -> Outcome -> Quality Gate research primitives.

This module is deliberately research-only.  It produces immutable event records
and applies a gate learned from a prior training slice; it does not create orders
or alter the V2 execution plane.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from math import sqrt
from statistics import median


@dataclass(frozen=True)
class EventBar:
    time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class EdgeEvent:
    event_id: str
    event_type: str
    symbol: str
    side: str
    event_time: datetime
    entry_time: datetime
    entry: float
    stop: float
    target: float
    atr: float
    volume_ratio: float
    breakout_distance_atr: float
    atr_percentile: float
    trend_age: int
    chop_score: float
    retest_depth_atr: float
    regime_4h: str
    trend_strength_1h: float
    outcome: str
    outcome_time: datetime
    outcome_r: float
    cost_r: float
    mfe_r: float
    mae_r: float

    @property
    def net_r(self) -> float:
        return self.outcome_r - self.cost_r


@dataclass(frozen=True)
class QualityGate:
    event_type: str
    side: str
    min_volume_ratio: float
    min_breakout_distance_atr: float
    min_trend_age: int
    max_chop_score: float
    min_atr_percentile: float
    max_atr_percentile: float

    def accepts(self, event: EdgeEvent) -> bool:
        return (
            (self.event_type == "ANY" or event.event_type == self.event_type)
            and (self.side == "ANY" or event.side == self.side)
            and event.volume_ratio >= self.min_volume_ratio
            and event.breakout_distance_atr >= self.min_breakout_distance_atr
            and event.trend_age >= self.min_trend_age
            and event.chop_score <= self.max_chop_score
            and self.min_atr_percentile <= event.atr_percentile <= self.max_atr_percentile
        )


@dataclass(frozen=True)
class GateMetrics:
    trades: int
    wins: int
    losses: int
    win_rate: float
    avg_win_r: float
    avg_loss_r: float
    payoff: float
    profit_factor: float
    expectancy: float
    max_drawdown_r: float
    max_drawdown_pct: float
    expectancy_lcb95: float


def _atr(bars: Sequence[EventBar], period: int = 14) -> float:
    if len(bars) < 2:
        return 0.0
    sample = bars[-(period + 1) :]
    ranges = []
    for previous, current in zip(sample[:-1], sample[1:], strict=True):
        ranges.append(
            max(current.high - current.low, abs(current.high - previous.close), abs(current.low - previous.close))
        )
    return sum(ranges[-period:]) / max(1, len(ranges[-period:]))


def _ema(values: Sequence[float], period: int) -> float:
    if not values:
        return 0.0
    alpha = 2.0 / (period + 1)
    value = values[0]
    for current in values[1:]:
        value += alpha * (current - value)
    return value


def _true_range(previous: EventBar, current: EventBar) -> float:
    return max(current.high - current.low, abs(current.high - previous.close), abs(current.low - previous.close))


def _regime(bars: Sequence[EventBar]) -> str:
    if len(bars) < 12:
        return "none"
    sample = bars[-12:]
    first, second = sample[:6], sample[6:]
    if max(x.high for x in second) > max(x.high for x in first) and min(x.low for x in second) > min(
        x.low for x in first
    ):
        return "long"
    if min(x.low for x in second) < min(x.low for x in first) and max(x.high for x in second) < max(
        x.high for x in first
    ):
        return "short"
    return "none"


def _trend_age(bars: Sequence[EventBar], side: str) -> int:
    age = 0
    for index in range(len(bars) - 1, 0, -1):
        previous, current = bars[index - 1], bars[index]
        if (side == "long" and current.close >= previous.close) or (
            side == "short" and current.close <= previous.close
        ):
            age += 1
        else:
            break
    return age


def _chop_score(bars: Sequence[EventBar]) -> float:
    if len(bars) < 8:
        return 1.0
    sample = bars[-8:]
    net = abs(sample[-1].close - sample[0].open)
    travel = sum(abs(bar.close - bar.open) for bar in sample)
    return 1.0 if travel <= 0 else max(0.0, min(1.0, 1.0 - net / travel))


def _percentile(value: float, history: Sequence[float]) -> float:
    if not history:
        return 0.5
    ordered = sorted(history)
    rank = sum(item <= value for item in ordered)
    return rank / len(ordered)


def _outcome(
    *,
    side: str,
    entry_index: int,
    entry: float,
    stop: float,
    target: float,
    bars15: Sequence[EventBar],
    max_holding_bars: int,
) -> tuple[str, datetime, float] | None:
    for bar in bars15[entry_index : entry_index + max_holding_bars]:
        if side == "long":
            hit_stop, hit_target = bar.low <= stop, bar.high >= target
        else:
            hit_stop, hit_target = bar.high >= stop, bar.low <= target
        if hit_stop:
            return "STOP_FIRST", bar.time, -1.0
        if hit_target:
            risk = abs(entry - stop)
            return "TP_FIRST", bar.time, abs(target - entry) / risk
    last = bars15[min(len(bars15) - 1, entry_index + max_holding_bars - 1)]
    risk = abs(entry - stop)
    signed = (last.close - entry) if side == "long" else (entry - last.close)
    return "TIMEOUT", last.time, max(-1.0, min(abs(target - entry) / risk, signed / risk))


def _mfe_mae(
    *, side: str, entry: float, stop: float, bars15: Sequence[EventBar], entry_index: int, max_holding_bars: int
) -> tuple[float, float]:
    risk = abs(entry - stop)
    window = bars15[entry_index : entry_index + max_holding_bars]
    if risk <= 0 or not window:
        return 0.0, 0.0
    if side == "long":
        mfe = max((bar.high - entry) / risk for bar in window)
        mae = min((bar.low - entry) / risk for bar in window)
    else:
        mfe = max((entry - bar.low) / risk for bar in window)
        mae = min((entry - bar.high) / risk for bar in window)
    return mfe, mae


def build_event_dataset(
    *,
    symbol: str,
    bars15: Sequence[EventBar],
    bars1h: Sequence[EventBar],
    bars4h: Sequence[EventBar],
    development_end: datetime,
    target_rr: float = 1.5,
    max_holding_bars: int = 96,
) -> tuple[EdgeEvent, ...]:
    """Build closed-bar events and point-in-time outcomes before ``development_end``."""
    bars15 = tuple(sorted((bar for bar in bars15 if bar.time < development_end), key=lambda bar: bar.time))
    bars1h = tuple(sorted((bar for bar in bars1h if bar.time < development_end), key=lambda bar: bar.time))
    bars4h = tuple(sorted((bar for bar in bars4h if bar.time < development_end), key=lambda bar: bar.time))
    if not bars15 or not bars1h or not bars4h:
        return ()
    times15 = [bar.time for bar in bars15]
    events: list[EdgeEvent] = []
    atr_history: list[float] = []
    last_outcome_time: datetime | None = None
    for i in range(20, len(bars1h)):
        current = bars1h[i]
        event_time = current.time + timedelta(hours=1)
        if event_time >= development_end:
            break
        if last_outcome_time is not None and event_time < last_outcome_time:
            continue
        prior = bars1h[i - 20 : i]
        atr = _atr(bars1h[: i + 1])
        if atr <= 0:
            continue
        atr_history.append(atr)
        high_level, low_level = max(x.high for x in prior), min(x.low for x in prior)
        mean_volume = median(x.volume for x in prior)
        volume_ratio = current.volume / max(mean_volume, 1e-12)
        regime_bars = [bar for bar in bars4h if bar.time + timedelta(hours=4) <= event_time]
        regime = _regime(regime_bars)
        ema_fast = _ema([bar.close for bar in bars1h[max(0, i - 80) : i + 1]], 20)
        ema_slow = _ema([bar.close for bar in bars1h[max(0, i - 80) : i + 1]], 50)
        side: str | None = None
        distance = 0.0
        if regime == "long" and current.close > high_level + 0.10 * atr and ema_fast > ema_slow:
            side = "long"
            distance = (current.close - high_level) / atr
        elif regime == "short" and current.close < low_level - 0.10 * atr and ema_fast < ema_slow:
            side = "short"
            distance = (low_level - current.close) / atr
        if side is None or abs(current.close - current.open) / atr < 0.35 or volume_ratio < 1.05:
            continue
        event_type = "HTF_STRUCTURE_BREAK"
        retest_depth = 0.0
        # A held retest is a separate event, never both event types at one time.
        if i > 20:
            previous = bars1h[i - 1]
            previous_prior = bars1h[i - 21 : i - 1]
            previous_atr = _atr(bars1h[:i])
            if previous_atr > 0:
                previous_high, previous_low = max(x.high for x in previous_prior), min(x.low for x in previous_prior)
                if (
                    side == "long"
                    and previous.close > previous_high + 0.10 * previous_atr
                    and current.low <= previous_high + 0.35 * previous_atr
                    and current.close > previous_high
                ):
                    event_type = "HTF_BREAK_RETEST"
                    retest_depth = max(0.0, (previous_high - current.low) / previous_atr)
                elif (
                    side == "short"
                    and previous.close < previous_low - 0.10 * previous_atr
                    and current.high >= previous_low - 0.35 * previous_atr
                    and current.close < previous_low
                ):
                    event_type = "HTF_BREAK_RETEST"
                    retest_depth = max(0.0, (current.high - previous_low) / previous_atr)
        entry_index = next((idx for idx, value in enumerate(times15) if value >= event_time), None)
        if entry_index is None or entry_index >= len(bars15) - 1:
            continue
        entry_bar = bars15[entry_index]
        entry = entry_bar.open
        stop = entry - atr if side == "long" else entry + atr
        target = entry + atr * target_rr if side == "long" else entry - atr * target_rr
        result = _outcome(
            side=side,
            entry_index=entry_index,
            entry=entry,
            stop=stop,
            target=target,
            bars15=bars15,
            max_holding_bars=max_holding_bars,
        )
        if result is None:
            continue
        outcome, outcome_time, outcome_r = result
        last_outcome_time = outcome_time
        mfe_r, mae_r = _mfe_mae(
            side=side,
            entry=entry,
            stop=stop,
            bars15=bars15,
            entry_index=entry_index,
            max_holding_bars=max_holding_bars,
        )
        cost_r = (entry * 12.0 / 10000.0) / atr
        events.append(
            EdgeEvent(
                event_id=f"{symbol}:{event_type}:{event_time.isoformat()}:{side}",
                event_type=event_type,
                symbol=symbol,
                side=side,
                event_time=event_time,
                entry_time=entry_bar.time,
                entry=entry,
                stop=stop,
                target=target,
                atr=atr,
                volume_ratio=volume_ratio,
                breakout_distance_atr=max(0.0, distance),
                atr_percentile=_percentile(atr, atr_history[-200:]),
                trend_age=_trend_age(bars1h[: i + 1], side),
                chop_score=_chop_score(bars1h[: i + 1]),
                retest_depth_atr=retest_depth,
                regime_4h=regime,
                trend_strength_1h=abs(ema_fast - ema_slow) / max(atr, 1e-12),
                outcome=outcome,
                outcome_time=outcome_time,
                outcome_r=outcome_r,
                cost_r=cost_r,
                mfe_r=mfe_r,
                mae_r=mae_r,
            )
        )
    return tuple(events)


def _direction_score(bars: Sequence[EventBar], *, lookback: int = 24) -> float:
    if len(bars) < 2:
        return 0.0
    sample = bars[-lookback:]
    first = sample[0].close
    if first <= 0:
        return 0.0
    return max(-1.0, min(1.0, (sample[-1].close - first) / first / 0.03))


def _range_regime_proxy(
    *,
    bars15: Sequence[EventBar],
    bars1h: Sequence[EventBar],
    bars4h: Sequence[EventBar],
    event_time: datetime,
    times15: Sequence[datetime] | None = None,
    times1h_closed: Sequence[datetime] | None = None,
    times4h_closed: Sequence[datetime] | None = None,
    direction_weights: tuple[float, float, float] = (0.20, 0.35, 0.45),
) -> tuple[float, float, float, str, float]:
    """Point-in-time proxy for the existing RegimeScorerV2 contract.

    The proposal generators consume RegimeScore, while EventEdge intentionally
    stays independent of proposal-pipeline objects.  This mirrors the scorer's
    direction weights (15m=.20, 1h=.35, 4h=.45) and volatility/range semantics
    using only bars closed at ``event_time``.
    """
    times15 = times15 or tuple(bar.time for bar in bars15)
    times1h_closed = times1h_closed or tuple(bar.time + timedelta(hours=1) for bar in bars1h)
    times4h_closed = times4h_closed or tuple(bar.time + timedelta(hours=4) for bar in bars4h)
    closed15 = bars15[: bisect_right(times15, event_time)]
    closed1h = bars1h[: bisect_right(times1h_closed, event_time)]
    closed4h = bars4h[: bisect_right(times4h_closed, event_time)]
    directions = {
        "15m": _direction_score(closed15),
        "1h": _direction_score(closed1h),
        "4h": _direction_score(closed4h),
    }
    weight15, weight1h, weight4h = direction_weights
    trend_up = max(
        0.0,
        weight15 * max(0.0, directions["15m"])
        + weight1h * max(0.0, directions["1h"])
        + weight4h * max(0.0, directions["4h"]),
    )
    trend_down = max(
        0.0,
        weight15 * max(0.0, -directions["15m"])
        + weight1h * max(0.0, -directions["1h"])
        + weight4h * max(0.0, -directions["4h"]),
    )
    recent = closed15[-5:]
    baseline = closed15[-20:-5] or closed15[:-5]
    expansion = 0.0
    if len(recent) >= 5 and baseline:
        recent_range = sum(bar.high - bar.low for bar in recent) / len(recent)
        baseline_range = sum(bar.high - bar.low for bar in baseline) / len(baseline)
        if baseline_range > 0:
            expansion = max(0.0, min(1.0, (recent_range / baseline_range - 1.0)))
    dominant = max(trend_up, trend_down)
    range_score = max(0.0, min(1.0, (1.0 - dominant) * (1.0 - expansion * 0.5)))
    regime = "long" if directions["4h"] > 0.05 else "short" if directions["4h"] < -0.05 else "none"
    return range_score, max(0.0, min(1.0, trend_up)), max(0.0, min(1.0, trend_down)), regime, expansion


def build_candidate_event_dataset(
    *,
    candidate_id: str,
    symbol: str,
    bars15: Sequence[EventBar],
    bars1h: Sequence[EventBar],
    bars4h: Sequence[EventBar],
    development_end: datetime,
    max_holding_bars: int = 96,
    direction_weights: tuple[float, float, float] = (0.20, 0.35, 0.45),
) -> tuple[EdgeEvent, ...]:
    """Adapt the existing reversal proposals to the sealed EventEdge format.

    This is research-only.  It preserves the candidates' Donchian-24 sweep and
    next-closed-bar confirmation rules.  Because EventEdge is single-target,
    failed-breakout uses the candidate's 2R runner and range-sweep uses the
    opposite range boundary; the report records this mapping explicitly.
    """
    if candidate_id not in {"failed_breakout_reversal_v1", "range_sweep_reversion_v1"}:
        raise ValueError(f"unsupported reversal candidate: {candidate_id}")
    bars15 = tuple(sorted((bar for bar in bars15 if bar.time < development_end), key=lambda bar: bar.time))
    bars1h = tuple(sorted((bar for bar in bars1h if bar.time < development_end), key=lambda bar: bar.time))
    bars4h = tuple(sorted((bar for bar in bars4h if bar.time < development_end), key=lambda bar: bar.time))
    if len(bars15) < 40 or not bars1h or not bars4h:
        return ()
    times15 = [bar.time for bar in bars15]
    times1h_closed = tuple(bar.time + timedelta(hours=1) for bar in bars1h)
    times4h_closed = tuple(bar.time + timedelta(hours=4) for bar in bars4h)
    events: list[EdgeEvent] = []
    atr_history: list[float] = []
    last_outcome_time: datetime | None = None
    event_type = candidate_id
    lookback = 24
    for i in range(lookback + 1, len(bars15) - 1):
        sweep = bars15[i - 1]
        confirmation = bars15[i]
        event_time = confirmation.time
        if event_time >= development_end or (last_outcome_time is not None and event_time < last_outcome_time):
            continue
        prior = bars15[i - 1 - lookback : i - 1]
        upper = max(bar.high for bar in prior)
        lower = min(bar.low for bar in prior)
        atr = _atr(bars15[max(0, i - 15) : i + 1])
        if atr <= 0:
            continue
        atr_history.append(atr)
        candle_range = sweep.high - sweep.low
        if candle_range <= 0:
            continue
        upper_wick = sweep.high - max(sweep.open, sweep.close)
        lower_wick = min(sweep.open, sweep.close) - sweep.low
        short = (
            sweep.high > upper
            and sweep.close < upper
            and upper_wick / candle_range >= 0.50
            and sweep.high - upper <= 2.0 * atr
            and confirmation.low < sweep.low
            and confirmation.close < upper
        )
        long = (
            sweep.low < lower
            and sweep.close > lower
            and lower_wick / candle_range >= 0.50
            and lower - sweep.low <= 2.0 * atr
            and confirmation.high > sweep.high
            and confirmation.close > lower
        )
        if candidate_id == "range_sweep_reversion_v1":
            upper_touches = sum(abs(bar.high - upper) <= 1e-12 for bar in prior)
            lower_touches = sum(abs(bar.low - lower) <= 1e-12 for bar in prior)
            range_score, trend_up, trend_down, regime, expansion = _range_regime_proxy(
                bars15=bars15,
                bars1h=bars1h,
                bars4h=bars4h,
                event_time=event_time,
                times15=times15,
                times1h_closed=times1h_closed,
                times4h_closed=times4h_closed,
                direction_weights=direction_weights,
            )
            allowed_range = range_score >= 0.55 and max(trend_up, trend_down) <= 0.50 and expansion <= 0.65
            long = long and upper_touches >= 2 and lower_touches >= 2 and allowed_range
            short = short and upper_touches >= 2 and lower_touches >= 2 and allowed_range
        if long == short:
            continue
        side = "long" if long else "short"
        entry_index = i + 1
        if entry_index >= len(bars15):
            continue
        entry_bar = bars15[entry_index]
        entry = entry_bar.open
        stop = sweep.low - 0.25 * atr if side == "long" else sweep.high + 0.25 * atr
        risk = abs(entry - stop)
        if risk <= 0:
            continue
        if candidate_id == "failed_breakout_reversal_v1":
            target = entry + 2.0 * risk if side == "long" else entry - 2.0 * risk
        else:
            target = upper if side == "long" else lower
            if (target - entry) * (1 if side == "long" else -1) <= 0:
                continue
        result = _outcome(
            side=side,
            entry_index=entry_index,
            entry=entry,
            stop=stop,
            target=target,
            bars15=bars15,
            max_holding_bars=max_holding_bars,
        )
        if result is None:
            continue
        outcome, outcome_time, outcome_r = result
        last_outcome_time = outcome_time
        mfe_r, mae_r = _mfe_mae(
            side=side,
            entry=entry,
            stop=stop,
            bars15=bars15,
            entry_index=entry_index,
            max_holding_bars=max_holding_bars,
        )
        volume_baseline = median(bar.volume for bar in prior)
        volume_ratio = sweep.volume / max(volume_baseline, 1e-12)
        range_score, trend_up, trend_down, regime, expansion = _range_regime_proxy(
            bars15=bars15,
            bars1h=bars1h,
            bars4h=bars4h,
            event_time=event_time,
            times15=times15,
            times1h_closed=times1h_closed,
            times4h_closed=times4h_closed,
            direction_weights=direction_weights,
        )
        direction = trend_up if side == "long" else trend_down
        cost_r = (entry * 12.0 / 10000.0) / risk
        events.append(
            EdgeEvent(
                event_id=f"{symbol}:{event_type}:{event_time.isoformat()}:{side}",
                event_type=event_type,
                symbol=symbol,
                side=side,
                event_time=event_time,
                entry_time=entry_bar.time,
                entry=entry,
                stop=stop,
                target=target,
                atr=atr,
                volume_ratio=volume_ratio,
                breakout_distance_atr=max((sweep.high - upper) / atr, (lower - sweep.low) / atr, 0.0),
                atr_percentile=_percentile(atr, atr_history[-200:]),
                trend_age=_trend_age(bars15[max(0, i - 32) : i + 1], side),
                chop_score=_chop_score(bars15[max(0, i - 8) : i + 1]),
                retest_depth_atr=0.0,
                regime_4h=regime,
                trend_strength_1h=direction,
                outcome=outcome,
                outcome_time=outcome_time,
                outcome_r=outcome_r,
                cost_r=cost_r,
                mfe_r=mfe_r,
                mae_r=mae_r,
            )
        )
    return tuple(events)


def gate_metrics(
    events: Iterable[EdgeEvent], gate: QualityGate | None = None, *, cost_multiple: float = 1.0
) -> GateMetrics:
    selected = sorted(
        (event for event in events if gate is None or gate.accepts(event)),
        key=lambda event: (event.entry_time, event.symbol, event.event_id),
    )
    values = [event.outcome_r - event.cost_r * cost_multiple for event in selected]
    wins = [value for value in values if value > 0]
    losses = [value for value in values if value < 0]
    gross_wins, gross_losses = sum(wins), abs(sum(losses))
    equity = peak = drawdown = 0.0
    compounded = peak_compounded = 1.0
    drawdown_pct = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
        compounded *= max(0.0001, 1.0 + value * 0.01)
        peak_compounded = max(peak_compounded, compounded)
        drawdown_pct = max(drawdown_pct, (peak_compounded - compounded) / peak_compounded)
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    mean = sum(values) / len(values) if values else 0.0
    variance = sum((value - mean) ** 2 for value in values) / max(1, len(values) - 1)
    lcb95 = mean - 1.96 * sqrt(variance / len(values)) if values else 0.0
    return GateMetrics(
        trades=len(values),
        wins=len(wins),
        losses=len(losses),
        win_rate=len(wins) / len(values) if values else 0.0,
        avg_win_r=avg_win,
        avg_loss_r=avg_loss,
        payoff=avg_win / abs(avg_loss) if avg_loss else 0.0,
        profit_factor=gross_wins / gross_losses if gross_losses else float("inf"),
        expectancy=mean,
        max_drawdown_r=drawdown,
        max_drawdown_pct=drawdown_pct,
        expectancy_lcb95=lcb95,
    )


def discover_quality_gate(
    events: Sequence[EdgeEvent], *, min_trades: int = 30, event_types: Sequence[str] | None = None
) -> tuple[QualityGate | None, GateMetrics]:
    """Nested selection: fit the finite grid on early train, validate on late train."""
    ordered = tuple(sorted(events, key=lambda event: event.event_time))
    split = int(len(ordered) * 0.67)
    fit, validation = ordered[:split], ordered[split:]
    best: tuple[tuple[float, float, float], QualityGate, GateMetrics] | None = None
    candidate_event_types = (
        tuple(event_types)
        if event_types is not None
        else ("ANY", "HTF_STRUCTURE_BREAK", "HTF_BREAK_RETEST")
    )
    for event_type in candidate_event_types:
        for side in ("ANY", "long", "short"):
            for min_volume in (1.05, 1.20, 1.40):
                for min_distance in (0.10, 0.25, 0.50):
                    for min_age in (0, 2, 4):
                        for max_chop in (0.45, 0.60, 0.75):
                            for atr_low, atr_high in ((0.0, 1.0), (0.2, 0.9), (0.4, 1.0)):
                                gate = QualityGate(
                                    event_type, side, min_volume, min_distance, min_age, max_chop, atr_low, atr_high
                                )
                                fit_metrics = gate_metrics(fit, gate)
                                if (
                                    fit_metrics.trades < min_trades
                                    or fit_metrics.win_rate < 0.55
                                    or fit_metrics.payoff < 1.15
                                    or fit_metrics.profit_factor < 1.40
                                    or fit_metrics.expectancy < 0.10
                                    or fit_metrics.expectancy_lcb95 <= 0
                                ):
                                    continue
                                metrics = gate_metrics(validation, gate)
                                if metrics.trades < max(10, min_trades // 3) or metrics.expectancy <= 0:
                                    continue
                                score = (metrics.expectancy, metrics.payoff, metrics.profit_factor)
                                if best is None or score > best[0]:
                                    best = (score, gate, metrics)
    return (best[1], best[2]) if best else (None, gate_metrics(()))


__all__ = [
    "EdgeEvent",
    "EventBar",
    "GateMetrics",
    "QualityGate",
    "build_event_dataset",
    "build_candidate_event_dataset",
    "discover_quality_gate",
    "gate_metrics",
]
