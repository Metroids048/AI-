"""Dow-style swing trend signal generation."""

from __future__ import annotations

import pandas as pd

from shared.models import TradeSide, TradeSignal


def generate_dow_trend_signal(
    frame: pd.DataFrame,
    *,
    symbol: str,
    pivot_window: int = 2,
) -> TradeSignal | None:
    """Detect higher-high/higher-low or lower-low/lower-high swing structure."""

    if len(frame) < pivot_window * 4 + 3:
        return None
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
    if len(pivot_highs) < 2 or len(pivot_lows) < 2:
        return None
    prev_high, latest_high = pivot_highs[-2][1], pivot_highs[-1][1]
    prev_low, latest_low = pivot_lows[-2][1], pivot_lows[-1][1]
    if latest_high > prev_high and latest_low > prev_low:
        direction = TradeSide.LONG
        reason = "dow_higher_high_higher_low"
    elif latest_high < prev_high and latest_low < prev_low:
        direction = TradeSide.SHORT
        reason = "dow_lower_high_lower_low"
    else:
        return None
    signal_time = frame.index[-1].to_pydatetime() if hasattr(frame.index[-1], "to_pydatetime") else None
    confidence = min(
        (abs(latest_high - prev_high) + abs(latest_low - prev_low)) / max(frame["close"].iloc[-1], 1.0),
        1.0,
    )
    return TradeSignal(
        symbol=symbol,
        side=direction,
        source="technical_dow_trend",
        signal_time=signal_time,
        reason=reason,
        confidence=float(confidence),
    )
