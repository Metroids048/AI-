"""Read-only loss attribution for real ``testnet_sampling_v2`` episodes.

The script deliberately holds each real Binance-filled entry fixed.  It derives
the current control exit from the existing shadow replay, then labels the 1h
and 4h state that was available at the decision bar.  It never creates an
intent, changes a strategy rule, or writes to the runtime database.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from services.automated_trading.application.decision_service import (
    BarView,
    TimeframeView,
    atr,
    ema,
    sampling_stop_distance,
)
from services.research.exit_policy_shadow.contracts import ExitPolicyId, ShadowOutcome
from services.research.exit_policy_shadow.loader import (
    build_entry_context,
    load_bars,
    load_real_entries,
)
from services.research.exit_policy_shadow.metrics import aggregate_outcomes
from services.research.exit_policy_shadow.regime import Regime
from services.research.exit_policy_shadow.replay import replay_entry_under_policy


def _trend_at_decision(db_path: Path, *, symbol: str, decision_bar: Any, timeframe: str) -> str:
    """Classify only closed higher-timeframe bars using close relative to EMA50."""
    closed_bar_offsets = {"1h": timedelta(hours=1), "4h": timedelta(hours=4)}
    try:
        closed_at_or_before = decision_bar - closed_bar_offsets[timeframe]
    except KeyError as exc:
        raise ValueError(f"unsupported trend timeframe: {timeframe}") from exc
    bars = load_bars(
        db_path,
        symbol=symbol,
        timeframe=timeframe,
        start=decision_bar - timedelta(days=14),
        end=closed_at_or_before,
    )
    if len(bars) < 50:
        return "unavailable"
    average = ema([bar.close for bar in bars], 50)
    if average is None:
        return "unavailable"
    return "bullish" if bars[-1].close > average else "bearish"


def _aligned(side: str, trend: str) -> bool | None:
    if trend == "unavailable":
        return None
    return (side == "long" and trend == "bullish") or (side == "short" and trend == "bearish")


def _outcome_row(outcome: ShadowOutcome, *, trend_1h: str, trend_4h: str) -> dict[str, Any]:
    direction_hit = outcome.excursions.mfe_r is not None and outcome.excursions.mfe_r >= Decimal("0.5")
    return {
        "position_id": outcome.position_id,
        "symbol": outcome.symbol,
        "side": outcome.side,
        "entry_price": str(outcome.entry_price),
        "exit_reason": outcome.final_reason.value,
        "net_pnl_usdt": str(outcome.net_pnl_usdt),
        "realized_r": None if outcome.r_multiple is None else str(outcome.r_multiple),
        "mfe_r": None if outcome.excursions.mfe_r is None else str(outcome.excursions.mfe_r),
        "mae_r": None if outcome.excursions.mae_r is None else str(outcome.excursions.mae_r),
        "direction_hit": direction_hit,
        "trend_1h": trend_1h,
        "trend_4h": trend_4h,
        "aligned_1h": _aligned(outcome.side, trend_1h),
        "aligned_4h": _aligned(outcome.side, trend_4h),
    }


def _summary(outcomes: list[ShadowOutcome]) -> dict[str, Any]:
    if not outcomes:
        return {"trades": 0}
    aggregate = aggregate_outcomes(outcomes, slice_key="real_entries")[ExitPolicyId.CURRENT_CONTROL]
    hits = sum(
        outcome.excursions.mfe_r is not None and outcome.excursions.mfe_r >= Decimal("0.5") for outcome in outcomes
    )
    return {
        "trades": len(outcomes),
        "direction_hit_rate": str(Decimal(hits) / Decimal(len(outcomes))),
        "net_expectancy_usdt": None if aggregate.net_expectancy_usdt is None else str(aggregate.net_expectancy_usdt),
        "profit_factor": None if aggregate.profit_factor is None else str(aggregate.profit_factor),
        "max_drawdown_usdt": str(aggregate.max_drawdown_usdt),
        "avg_r": None if aggregate.avg_r is None else str(aggregate.avg_r),
    }


def _as_view(bar: Any) -> BarView:
    return BarView(
        timestamp=bar.time,
        open=bar.open,
        high=bar.high,
        low=bar.low,
        close=bar.close,
        volume=bar.volume,
    )


def _fast_sampling_side(view: TimeframeView) -> str | None:
    """Mirror ``evaluate_sampling_signal`` without its quadratic MACD recompute."""
    bars = view.bars
    if not bars:
        return None
    closes = [bar.close for bar in bars]
    if len(closes) < 50 or len(closes) < 26 + 9 or len(closes) < 15:
        return None

    def _ema_latest(values: list[Decimal], period: int) -> Decimal:
        multiplier = Decimal(2) / Decimal(period + 1)
        current = sum(values[:period], Decimal("0")) / Decimal(period)
        for value in values[period:]:
            current = (value - current) * multiplier + current
        return current

    ema50 = _ema_latest(closes, 50)
    fast_multiplier = Decimal(2) / Decimal(13)
    slow_multiplier = Decimal(2) / Decimal(27)
    fast_current = sum(closes[:12], Decimal("0")) / Decimal(12)
    slow_current = sum(closes[:26], Decimal("0")) / Decimal(26)
    macd_series: list[Decimal] = []
    for value in closes[12:26]:
        fast_current = (value - fast_current) * fast_multiplier + fast_current
    macd_series.append(fast_current - slow_current)
    for value in closes[26:]:
        fast_current = (value - fast_current) * fast_multiplier + fast_current
        slow_current = (value - slow_current) * slow_multiplier + slow_current
        macd_series.append(fast_current - slow_current)
    signal_line = _ema_latest(macd_series, 9)
    histogram = macd_series[-1] - signal_line
    gains = Decimal("0")
    losses = Decimal("0")
    for previous, current in zip(closes[-15:-1], closes[-14:], strict=True):
        delta = current - previous
        if delta >= 0:
            gains += delta
        else:
            losses += -delta
    if losses == 0:
        rsi14 = Decimal("100")
    else:
        rs = (gains / Decimal(14)) / (losses / Decimal(14))
        rsi14 = Decimal("100") - (Decimal("100") / (Decimal("1") + rs))
    if atr(bars, 14) is None:
        return None
    close = closes[-1]
    if close > ema50 and histogram > 0 and Decimal("50") <= rsi14 <= Decimal("72"):
        return "long"
    if close < ema50 and histogram < 0 and Decimal("28") <= rsi14 <= Decimal("50"):
        return "short"
    return None


def _four_hour_conflicts(side: str, bars: list[Any]) -> bool:
    """Return the point-in-time 4h conflict veto using EMA20/EMA50 + close."""
    closes = [bar.close for bar in bars]
    fast = ema(closes, 20)
    slow = ema(closes, 50)
    if fast is None or slow is None:
        return False
    last = bars[-1].close
    return (side == "long" and last < slow and fast < slow) or (side == "short" and last > slow and fast > slow)


def _trend_aligned(side: str, bars: list[Any]) -> bool:
    closes = [bar.close for bar in bars]
    fast = ema(closes, 20)
    slow = ema(closes, 50)
    if fast is None or slow is None:
        return False
    last = bars[-1].close
    return (side == "long" and last > slow and fast >= slow) or (side == "short" and last < slow and fast <= slow)


def _trade_metrics(trades: list[dict[str, Any]], candidates: int) -> dict[str, Any]:
    values = [trade["net_r"] for trade in trades]
    wins = [value for value in values if value > 0]
    losses = [value for value in values if value < 0]
    equity = Decimal("0")
    peak = Decimal("0")
    max_drawdown = Decimal("0")
    for value in values:
        equity += value
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)
    return {
        "candidates": candidates,
        "trades": len(trades),
        "direction_hit_rate": None
        if not trades
        else str(Decimal(sum(trade["direction_hit"] for trade in trades)) / Decimal(len(trades))),
        "win_rate": None if not trades else str(Decimal(len(wins)) / Decimal(len(trades))),
        "avg_win_r": None if not wins else str(sum(wins, Decimal("0")) / Decimal(len(wins))),
        "avg_loss_r": None if not losses else str(sum(losses, Decimal("0")) / Decimal(len(losses))),
        "win_loss_ratio": None
        if not wins or not losses
        else str(
            (sum(wins, Decimal("0")) / Decimal(len(wins))) / abs(sum(losses, Decimal("0")) / Decimal(len(losses)))
        ),
        "net_expectancy_r": None if not trades else str(sum(values, Decimal("0")) / Decimal(len(trades))),
        "profit_factor": None if not losses else str(sum(wins, Decimal("0")) / abs(sum(losses, Decimal("0")))),
        "net_r": str(sum(values, Decimal("0"))),
        "max_drawdown_r": str(max_drawdown),
    }


def _replay_symbol(
    bars_15m: list[Any],
    trend_bars: list[Any],
    *,
    conflict_veto: bool,
    trend_hours: int,
    split_at: datetime,
    one_closed_bar_confirmation: bool = False,
    confirmation_bars: int = 0,
    long_side_veto: bool = False,
    short_side_veto: bool = False,
    short_side_conflict_only: bool = False,
    long_side_conflict_only: bool = False,
    secondary_trend_bars: list[Any] | None = None,
    secondary_trend_hours: int = 1,
    dual_timeframe_conflict_only: bool = False,
    strict_dual_timeframe_alignment_only: bool = False,
) -> dict[str, dict[str, Any]]:
    """Replay the exact entry evaluator with next-bar fill and unchanged geometry.

    The replay is a conservative 15m-bar screen: a bar touching both stop and
    target resolves stop-first.  It is used only to choose whether the hypothesis
    deserves the existing 1m fidelity validation, never as deployment evidence.
    """
    outcomes: dict[str, list[dict[str, Any]]] = {"development": [], "oos": []}
    candidates = {"development": 0, "oos": 0}
    trend_index = 0
    secondary_trend_index = 0
    active_until = -1
    confirmation_bars = max(confirmation_bars, 1 if one_closed_bar_confirmation else 0)
    final_signal_index = len(bars_15m) - 1 - confirmation_bars
    for index in range(100, final_signal_index):
        decision_bar = bars_15m[index]
        while trend_index + 1 < len(trend_bars) and trend_bars[trend_index + 1].time <= decision_bar.time - timedelta(
            hours=trend_hours
        ):
            trend_index += 1
        if dual_timeframe_conflict_only or strict_dual_timeframe_alignment_only:
            if secondary_trend_bars is None:
                raise RuntimeError("dual-timeframe replay requires secondary trend bars")
            while secondary_trend_index + 1 < len(secondary_trend_bars) and secondary_trend_bars[
                secondary_trend_index + 1
            ].time <= decision_bar.time - timedelta(hours=secondary_trend_hours):
                secondary_trend_index += 1
        view = TimeframeView("15m", tuple(_as_view(bar) for bar in bars_15m[index - 100 : index + 1]))
        side = _fast_sampling_side(view)
        cohort = "development" if decision_bar.time < split_at else "oos"
        if side is None or index <= active_until:
            continue
        if long_side_veto and side == "long":
            continue
        if short_side_veto and side == "short":
            continue
        trend = trend_bars[: trend_index + 1]
        secondary_trend = None
        if strict_dual_timeframe_alignment_only:
            assert secondary_trend_bars is not None
            secondary_trend = secondary_trend_bars[: secondary_trend_index + 1]
            if not _trend_aligned(side, trend) or not _trend_aligned(side, secondary_trend):
                continue
        conflict_applies = (not short_side_conflict_only or side == "short") and (
            not long_side_conflict_only or side == "long"
        )
        conflict = _four_hour_conflicts(side, trend)
        if dual_timeframe_conflict_only:
            assert secondary_trend_bars is not None
            conflict = conflict and _four_hour_conflicts(side, secondary_trend_bars[: secondary_trend_index + 1])
        if conflict_veto and conflict_applies and conflict:
            continue
        signal_index = index
        entry_index = index + 1
        if confirmation_bars:
            confirmation_view = None
            for offset in range(1, confirmation_bars + 1):
                confirmation_end = index + offset + 1
                confirmation_view = TimeframeView(
                    "15m",
                    tuple(_as_view(bar) for bar in bars_15m[confirmation_end - 101 : confirmation_end]),
                )
                confirmation_side = _fast_sampling_side(confirmation_view)
                if confirmation_side != side:
                    break
            else:
                assert confirmation_view is not None
                signal_index = index + confirmation_bars
                entry_index = signal_index + 1
                decision_bar = bars_15m[signal_index]
                view = confirmation_view
                cohort = "development" if decision_bar.time < split_at else "oos"
                confirmation_view = None
            if confirmation_view is not None:
                continue
        candidates[cohort] += 1
        entry_bar = bars_15m[entry_index]
        atr14 = atr(view.bars, 14)
        if atr14 is None:
            continue
        entry = entry_bar.open
        risk = sampling_stop_distance(atr14=atr14, reference_price=entry)
        target = entry + Decimal("1.5") * risk if side == "long" else entry - Decimal("1.5") * risk
        stop = entry - risk if side == "long" else entry + risk
        mfe = Decimal("0")
        hit = False
        for exit_index in range(entry_index, len(bars_15m)):
            bar = bars_15m[exit_index]
            favorable = bar.high - entry if side == "long" else entry - bar.low
            mfe = max(mfe, favorable)
            stop_hit = bar.low <= stop if side == "long" else bar.high >= stop
            target_hit = bar.high >= target if side == "long" else bar.low <= target
            if stop_hit or target_hit:
                # Same-bar ambiguity follows the production shadow's conservative rule.
                exit_price = stop if stop_hit else target
                gross_r = (exit_price - entry) / risk if side == "long" else (entry - exit_price) / risk
                # 5 bps entry + 5 bps exit + 1 bp exit slippage: same shadow cost model.
                cost_r = entry * Decimal("0.0011") / risk
                outcomes[cohort].append(
                    {
                        "net_r": gross_r - cost_r,
                        "direction_hit": mfe >= Decimal("0.5") * risk,
                        "side": side,
                        "decision_time": bars_15m[signal_index].time.isoformat(),
                    }
                )
                active_until = exit_index
                hit = True
                break
        if not hit:
            active_until = len(bars_15m) - 1
    return {cohort: _trade_metrics(outcomes[cohort], candidates[cohort]) for cohort in outcomes}


def historical_oos_comparison(
    db_path: Path,
    *,
    one_bar_only: bool = False,
    two_bar_only: bool = False,
    long_side_only: bool = False,
    short_side_only: bool = False,
    short_side_conflict_only: bool = False,
    long_side_conflict_only: bool = False,
    long_side_1h_conflict_only: bool = False,
    short_side_1h_conflict_only: bool = False,
    dual_timeframe_conflict_only: bool = False,
    long_dual_timeframe_conflict_only: bool = False,
    short_dual_timeframe_conflict_only: bool = False,
    strict_dual_timeframe_alignment_only: bool = False,
    strict_dual_timeframe_alignment_one_bar_only: bool = False,
    strict_dual_timeframe_alignment_two_bar_only: bool = False,
) -> dict[str, Any]:
    """Run the frozen 70/30 chronological screen for the requested policies."""
    start = datetime(2023, 1, 1, tzinfo=UTC)
    end = datetime(2026, 7, 29, tzinfo=UTC)
    by_policy: dict[str, dict[str, dict[str, dict[str, Any]]]] = {
        "baseline": {},
        "one_closed_bar_confirmation": {},
        "two_closed_bar_confirmation": {},
        "long_side_veto": {},
        "short_side_veto": {},
        "short_side_4h_conflict_veto": {},
        "long_side_4h_conflict_veto": {},
        "long_side_1h_conflict_veto": {},
        "short_side_1h_conflict_veto": {},
        "dual_timeframe_conflict_veto": {},
        "long_dual_timeframe_conflict_veto": {},
        "short_dual_timeframe_conflict_veto": {},
        "strict_dual_timeframe_alignment": {},
        "strict_dual_timeframe_alignment_one_bar": {},
        "strict_dual_timeframe_alignment_two_bar": {},
    }
    if long_side_only:
        by_policy = {"baseline": {}, "long_side_veto": {}}
    elif short_side_only:
        by_policy = {"baseline": {}, "short_side_veto": {}}
    elif short_side_conflict_only:
        by_policy = {"baseline": {}, "short_side_4h_conflict_veto": {}}
    elif long_side_conflict_only:
        by_policy = {"baseline": {}, "long_side_4h_conflict_veto": {}}
    elif long_side_1h_conflict_only:
        by_policy = {"baseline": {}, "long_side_1h_conflict_veto": {}}
    elif short_side_1h_conflict_only:
        by_policy = {"baseline": {}, "short_side_1h_conflict_veto": {}}
    elif dual_timeframe_conflict_only:
        by_policy = {"baseline": {}, "dual_timeframe_conflict_veto": {}}
    elif long_dual_timeframe_conflict_only:
        by_policy = {"baseline": {}, "long_dual_timeframe_conflict_veto": {}}
    elif short_dual_timeframe_conflict_only:
        by_policy = {"baseline": {}, "short_dual_timeframe_conflict_veto": {}}
    elif strict_dual_timeframe_alignment_only:
        by_policy = {"baseline": {}, "strict_dual_timeframe_alignment": {}}
    elif strict_dual_timeframe_alignment_one_bar_only:
        by_policy = {"baseline": {}, "strict_dual_timeframe_alignment_one_bar": {}}
    elif strict_dual_timeframe_alignment_two_bar_only:
        by_policy = {"baseline": {}, "strict_dual_timeframe_alignment_two_bar": {}}
    elif two_bar_only:
        by_policy = {"baseline": {}, "two_closed_bar_confirmation": {}}
    elif one_bar_only:
        by_policy = {"baseline": {}, "one_closed_bar_confirmation": {}}
    if (
        not one_bar_only
        and not two_bar_only
        and not long_side_only
        and not short_side_only
        and not short_side_conflict_only
        and not long_side_conflict_only
        and not long_side_1h_conflict_only
        and not short_side_1h_conflict_only
        and not dual_timeframe_conflict_only
        and not long_dual_timeframe_conflict_only
        and not short_dual_timeframe_conflict_only
        and not strict_dual_timeframe_alignment_only
        and not strict_dual_timeframe_alignment_one_bar_only
        and not strict_dual_timeframe_alignment_two_bar_only
    ):
        by_policy.update({"four_hour_conflict_veto": {}, "one_hour_conflict_veto": {}})
    for symbol in ("BTC/USDT", "ETH/USDT"):
        bars_15m = load_bars(db_path, symbol=symbol, timeframe="15m", start=start, end=end)
        bars_4h = load_bars(db_path, symbol=symbol, timeframe="4h", start=start, end=end)
        if not bars_15m or not bars_4h:
            raise RuntimeError(f"missing historical bars for {symbol}")
        split_at = bars_15m[int(len(bars_15m) * Decimal("0.7"))].time
        by_policy["baseline"][symbol] = _replay_symbol(
            bars_15m, bars_4h, conflict_veto=False, trend_hours=4, split_at=split_at
        )
        if (
            not two_bar_only
            and not long_side_only
            and not short_side_only
            and not short_side_conflict_only
            and not long_side_conflict_only
            and not long_side_1h_conflict_only
            and not short_side_1h_conflict_only
            and not dual_timeframe_conflict_only
            and not long_dual_timeframe_conflict_only
            and not short_dual_timeframe_conflict_only
            and not strict_dual_timeframe_alignment_only
            and not strict_dual_timeframe_alignment_one_bar_only
            and not strict_dual_timeframe_alignment_two_bar_only
        ):
            by_policy["one_closed_bar_confirmation"][symbol] = _replay_symbol(
                bars_15m,
                bars_4h,
                conflict_veto=False,
                trend_hours=4,
                split_at=split_at,
                confirmation_bars=1,
            )
        if (
            not one_bar_only
            and not two_bar_only
            and not long_side_only
            and not short_side_only
            and not short_side_conflict_only
            and not long_side_conflict_only
            and not long_side_1h_conflict_only
            and not short_side_1h_conflict_only
            and not dual_timeframe_conflict_only
            and not long_dual_timeframe_conflict_only
            and not short_dual_timeframe_conflict_only
            and not strict_dual_timeframe_alignment_only
            and not strict_dual_timeframe_alignment_one_bar_only
            and not strict_dual_timeframe_alignment_two_bar_only
        ):
            by_policy["two_closed_bar_confirmation"][symbol] = _replay_symbol(
                bars_15m,
                bars_4h,
                conflict_veto=False,
                trend_hours=4,
                split_at=split_at,
                confirmation_bars=2,
            )
        if (
            not one_bar_only
            and not two_bar_only
            and not long_side_only
            and not short_side_only
            and not short_side_conflict_only
            and not long_side_conflict_only
            and not long_side_1h_conflict_only
            and not short_side_1h_conflict_only
            and not dual_timeframe_conflict_only
            and not long_dual_timeframe_conflict_only
            and not short_dual_timeframe_conflict_only
            and not strict_dual_timeframe_alignment_only
            and not strict_dual_timeframe_alignment_one_bar_only
            and not strict_dual_timeframe_alignment_two_bar_only
        ):
            bars_1h = load_bars(db_path, symbol=symbol, timeframe="1h", start=start, end=end)
            by_policy["four_hour_conflict_veto"][symbol] = _replay_symbol(
                bars_15m, bars_4h, conflict_veto=True, trend_hours=4, split_at=split_at
            )
            by_policy["one_hour_conflict_veto"][symbol] = _replay_symbol(
                bars_15m, bars_1h, conflict_veto=True, trend_hours=1, split_at=split_at
            )
        if long_side_only:
            by_policy["long_side_veto"][symbol] = _replay_symbol(
                bars_15m,
                bars_4h,
                conflict_veto=False,
                trend_hours=4,
                split_at=split_at,
                long_side_veto=True,
            )
        if short_side_only:
            by_policy["short_side_veto"][symbol] = _replay_symbol(
                bars_15m,
                bars_4h,
                conflict_veto=False,
                trend_hours=4,
                split_at=split_at,
                short_side_veto=True,
            )
        if short_side_conflict_only:
            by_policy["short_side_4h_conflict_veto"][symbol] = _replay_symbol(
                bars_15m,
                bars_4h,
                conflict_veto=True,
                trend_hours=4,
                split_at=split_at,
                short_side_conflict_only=True,
            )
        if long_side_conflict_only:
            by_policy["long_side_4h_conflict_veto"][symbol] = _replay_symbol(
                bars_15m,
                bars_4h,
                conflict_veto=True,
                trend_hours=4,
                split_at=split_at,
                long_side_conflict_only=True,
            )
        if long_side_1h_conflict_only:
            bars_1h = load_bars(db_path, symbol=symbol, timeframe="1h", start=start, end=end)
            if not bars_1h:
                raise RuntimeError(f"missing historical bars for {symbol} 1h trend")
            by_policy["long_side_1h_conflict_veto"][symbol] = _replay_symbol(
                bars_15m,
                bars_1h,
                conflict_veto=True,
                trend_hours=1,
                split_at=split_at,
                long_side_conflict_only=True,
            )
        if short_side_1h_conflict_only:
            bars_1h = load_bars(db_path, symbol=symbol, timeframe="1h", start=start, end=end)
            if not bars_1h:
                raise RuntimeError(f"missing historical bars for {symbol} 1h trend")
            by_policy["short_side_1h_conflict_veto"][symbol] = _replay_symbol(
                bars_15m,
                bars_1h,
                conflict_veto=True,
                trend_hours=1,
                split_at=split_at,
                short_side_conflict_only=True,
            )
        if dual_timeframe_conflict_only:
            bars_1h = load_bars(db_path, symbol=symbol, timeframe="1h", start=start, end=end)
            if not bars_1h:
                raise RuntimeError(f"missing historical bars for {symbol} 1h trend")
            by_policy["dual_timeframe_conflict_veto"][symbol] = _replay_symbol(
                bars_15m,
                bars_4h,
                conflict_veto=True,
                trend_hours=4,
                split_at=split_at,
                secondary_trend_bars=bars_1h,
                secondary_trend_hours=1,
                dual_timeframe_conflict_only=True,
            )
        if long_dual_timeframe_conflict_only:
            bars_1h = load_bars(db_path, symbol=symbol, timeframe="1h", start=start, end=end)
            if not bars_1h:
                raise RuntimeError(f"missing historical bars for {symbol} 1h trend")
            by_policy["long_dual_timeframe_conflict_veto"][symbol] = _replay_symbol(
                bars_15m,
                bars_4h,
                conflict_veto=True,
                trend_hours=4,
                split_at=split_at,
                long_side_conflict_only=True,
                secondary_trend_bars=bars_1h,
                secondary_trend_hours=1,
                dual_timeframe_conflict_only=True,
            )
        if short_dual_timeframe_conflict_only:
            bars_1h = load_bars(db_path, symbol=symbol, timeframe="1h", start=start, end=end)
            if not bars_1h:
                raise RuntimeError(f"missing historical bars for {symbol} 1h trend")
            by_policy["short_dual_timeframe_conflict_veto"][symbol] = _replay_symbol(
                bars_15m,
                bars_4h,
                conflict_veto=True,
                trend_hours=4,
                split_at=split_at,
                short_side_conflict_only=True,
                secondary_trend_bars=bars_1h,
                secondary_trend_hours=1,
                dual_timeframe_conflict_only=True,
            )
        if strict_dual_timeframe_alignment_only:
            bars_1h = load_bars(db_path, symbol=symbol, timeframe="1h", start=start, end=end)
            if not bars_1h:
                raise RuntimeError(f"missing historical bars for {symbol} 1h trend")
            by_policy["strict_dual_timeframe_alignment"][symbol] = _replay_symbol(
                bars_15m,
                bars_4h,
                conflict_veto=False,
                trend_hours=4,
                split_at=split_at,
                secondary_trend_bars=bars_1h,
                secondary_trend_hours=1,
                strict_dual_timeframe_alignment_only=True,
            )
        if strict_dual_timeframe_alignment_one_bar_only:
            bars_1h = load_bars(db_path, symbol=symbol, timeframe="1h", start=start, end=end)
            if not bars_1h:
                raise RuntimeError(f"missing historical bars for {symbol} 1h trend")
            by_policy["strict_dual_timeframe_alignment_one_bar"][symbol] = _replay_symbol(
                bars_15m,
                bars_4h,
                conflict_veto=False,
                trend_hours=4,
                split_at=split_at,
                confirmation_bars=1,
                secondary_trend_bars=bars_1h,
                secondary_trend_hours=1,
                strict_dual_timeframe_alignment_only=True,
            )
        if strict_dual_timeframe_alignment_two_bar_only:
            bars_1h = load_bars(db_path, symbol=symbol, timeframe="1h", start=start, end=end)
            if not bars_1h:
                raise RuntimeError(f"missing historical bars for {symbol} 1h trend")
            by_policy["strict_dual_timeframe_alignment_two_bar"][symbol] = _replay_symbol(
                bars_15m,
                bars_4h,
                conflict_veto=False,
                trend_hours=4,
                split_at=split_at,
                confirmation_bars=2,
                secondary_trend_bars=bars_1h,
                secondary_trend_hours=1,
                strict_dual_timeframe_alignment_only=True,
            )
    return by_policy


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path(".local_paper_console.db"))
    parser.add_argument(
        "--report-path",
        type=Path,
        default=Path("artifacts/active_strategy_optimization/real_trade_context.json"),
    )
    parser.add_argument("--historical-oos", action="store_true")
    parser.add_argument("--one-bar-only", action="store_true")
    parser.add_argument("--two-bar-only", action="store_true")
    parser.add_argument("--long-side-only", action="store_true")
    parser.add_argument("--short-side-only", action="store_true")
    parser.add_argument("--short-side-conflict-only", action="store_true")
    parser.add_argument("--long-side-conflict-only", action="store_true")
    parser.add_argument("--long-side-1h-conflict-only", action="store_true")
    parser.add_argument("--short-side-1h-conflict-only", action="store_true")
    parser.add_argument("--dual-timeframe-conflict-only", action="store_true")
    parser.add_argument("--long-dual-timeframe-conflict-only", action="store_true")
    parser.add_argument("--short-dual-timeframe-conflict-only", action="store_true")
    parser.add_argument("--strict-dual-timeframe-alignment-only", action="store_true")
    parser.add_argument("--strict-dual-timeframe-alignment-one-bar-only", action="store_true")
    parser.add_argument("--strict-dual-timeframe-alignment-two-bar-only", action="store_true")
    args = parser.parse_args()
    db_path = args.database.resolve()
    entries = load_real_entries(db_path)
    outcomes_by_alignment: dict[str, list[ShadowOutcome]] = defaultdict(list)
    rows: list[dict[str, Any]] = []

    for entry in entries:
        context = build_entry_context(db_path, symbol=entry.symbol, decision_bar=entry.decision_bar_timestamp)
        bars = load_bars(
            db_path,
            symbol=entry.symbol,
            timeframe="1m",
            start=entry.fill_timestamp,
            end=entry.fill_timestamp + timedelta(days=7),
        )
        if context is None or not bars:
            continue
        trend_1h = _trend_at_decision(
            db_path, symbol=entry.symbol, decision_bar=entry.decision_bar_timestamp, timeframe="1h"
        )
        trend_4h = _trend_at_decision(
            db_path, symbol=entry.symbol, decision_bar=entry.decision_bar_timestamp, timeframe="4h"
        )
        outcome = replay_entry_under_policy(
            entry=entry,
            bars=bars,
            policy=ExitPolicyId.CURRENT_CONTROL,
            regime=Regime.UNKNOWN,
            entry_context=context,
        )
        rows.append(_outcome_row(outcome, trend_1h=trend_1h, trend_4h=trend_4h))
        outcomes_by_alignment["all"].append(outcome)
        outcomes_by_alignment[f"1h_{_aligned(entry.side, trend_1h)}"].append(outcome)
        outcomes_by_alignment[f"4h_{_aligned(entry.side, trend_4h)}"].append(outcome)
        outcomes_by_alignment[f"symbol_{entry.symbol}"].append(outcome)
        outcomes_by_alignment[f"side_{entry.side}"].append(outcome)

    report = {
        "scope": "real closed testnet_sampling_v2 entries; current control exit replay",
        "entry_count": len(rows),
        "summaries": {key: _summary(value) for key, value in sorted(outcomes_by_alignment.items())},
        "trades": rows,
    }
    if args.historical_oos:
        report["historical_oos_screen"] = historical_oos_comparison(
            Path(".strategy_refactor_history.db").resolve(),
            one_bar_only=args.one_bar_only,
            two_bar_only=args.two_bar_only,
            long_side_only=args.long_side_only,
            short_side_only=args.short_side_only,
            short_side_conflict_only=args.short_side_conflict_only,
            long_side_conflict_only=args.long_side_conflict_only,
            long_side_1h_conflict_only=args.long_side_1h_conflict_only,
            short_side_1h_conflict_only=args.short_side_1h_conflict_only,
            dual_timeframe_conflict_only=args.dual_timeframe_conflict_only,
            long_dual_timeframe_conflict_only=args.long_dual_timeframe_conflict_only,
            short_dual_timeframe_conflict_only=args.short_dual_timeframe_conflict_only,
            strict_dual_timeframe_alignment_only=args.strict_dual_timeframe_alignment_only,
            strict_dual_timeframe_alignment_one_bar_only=args.strict_dual_timeframe_alignment_one_bar_only,
            strict_dual_timeframe_alignment_two_bar_only=args.strict_dual_timeframe_alignment_two_bar_only,
        )
    args.report_path.parent.mkdir(parents=True, exist_ok=True)
    args.report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["summaries"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
