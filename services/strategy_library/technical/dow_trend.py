"""Dow-style swing trend signal generation."""

from __future__ import annotations

import pandas as pd

from shared.models import TradeSide, TradeSignal

CONTINUOUS_STRENGTH_THRESHOLD = 0.05


def _collect_pivots(frame: pd.DataFrame, *, pivot_window: int) -> tuple[list[tuple[int, float]], list[tuple[int, float]]]:
    highs = frame["high"].astype(float)
    lows = frame["low"].astype(float)
    pivot_highs: list[tuple[int, float]] = []
    pivot_lows: list[tuple[int, float]] = []
    for idx in range(pivot_window, len(frame) - pivot_window):
        high_window = highs.iloc[idx - pivot_window : idx + pivot_window + 1]
        low_window = lows.iloc[idx - pivot_window : idx + pivot_window + 1]
        if highs.iloc[idx] == high_window.max():
            pivot_highs.append((idx, float(highs.iloc[idx])))
        if lows.iloc[idx] == low_window.min():
            pivot_lows.append((idx, float(lows.iloc[idx])))
    return pivot_highs, pivot_lows


def _dow_continuous_strength(
    *,
    prev_high: float,
    latest_high: float,
    prev_low: float,
    latest_low: float,
    close: float,
) -> float:
    high_slope = (latest_high - prev_high) / max(prev_high, 1.0)
    low_slope = (latest_low - prev_low) / max(prev_low, 1.0)
    raw = (high_slope + low_slope) / 2.0
    normalized = max(min(raw * 20.0, 1.0), -1.0)
    return normalized


def generate_dow_trend_signal(
    frame: pd.DataFrame,
    *,
    symbol: str,
    pivot_window: int = 2,
) -> TradeSignal | None:
    """Detect swing structure or emit a continuous trend-strength signal."""

    if len(frame) < pivot_window * 4 + 3:
        return None
    pivot_highs, pivot_lows = _collect_pivots(frame, pivot_window=pivot_window)
    if len(pivot_highs) < 2 or len(pivot_lows) < 2:
        return None
    prev_high, latest_high = pivot_highs[-2][1], pivot_highs[-1][1]
    prev_low, latest_low = pivot_lows[-2][1], pivot_lows[-1][1]
    close = float(frame["close"].iloc[-1])
    signal_time = frame.index[-1].to_pydatetime() if hasattr(frame.index[-1], "to_pydatetime") else None
    if latest_high > prev_high and latest_low > prev_low:
        direction = TradeSide.LONG
        reason = "dow_higher_high_higher_low"
    elif latest_high < prev_high and latest_low < prev_low:
        direction = TradeSide.SHORT
        reason = "dow_lower_high_lower_low"
    else:
        strength = _dow_continuous_strength(
            prev_high=prev_high,
            latest_high=latest_high,
            prev_low=prev_low,
            latest_low=latest_low,
            close=close,
        )
        if abs(strength) < CONTINUOUS_STRENGTH_THRESHOLD:
            return None
        return TradeSignal(
            symbol=symbol,
            side=TradeSide.LONG if strength > 0 else TradeSide.SHORT,
            source="technical_dow_trend",
            signal_time=signal_time,
            reason="dow_continuous_trend",
            confidence=abs(strength),
        )
    confidence = min((abs(latest_high - prev_high) + abs(latest_low - prev_low)) / max(close, 1.0), 1.0)
    return TradeSignal(
        symbol=symbol,
        side=direction,
        source="technical_dow_trend",
        signal_time=signal_time,
        reason=reason,
        confidence=float(confidence),
    )
