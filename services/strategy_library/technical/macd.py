"""MACD crossover signal generation."""

from __future__ import annotations

import math

import pandas as pd

from shared.models import TradeSide, TradeSignal

CONTINUOUS_STRENGTH_THRESHOLD = 0.05


def generate_macd_signal(
    frame: pd.DataFrame,
    *,
    symbol: str,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> TradeSignal | None:
    """Return the latest MACD crossover as a TradeSignal."""

    if len(frame) < slow + signal + 2:
        return None
    close = frame["close"].astype(float)
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    direction: TradeSide | None = None
    reason: str | None = None
    signal_index = -1
    latest_delta = macd_line.iloc[-1] - signal_line.iloc[-1]
    for offset in range(1, min(7, len(frame) - 1)):
        previous_delta = macd_line.iloc[-offset - 1] - signal_line.iloc[-offset - 1]
        current_delta = macd_line.iloc[-offset] - signal_line.iloc[-offset]
        if previous_delta <= 0 < current_delta:
            direction = TradeSide.LONG
            reason = "macd_bullish_cross"
            signal_index = -offset
            latest_delta = current_delta
            break
        if previous_delta >= 0 > current_delta:
            direction = TradeSide.SHORT
            reason = "macd_bearish_cross"
            signal_index = -offset
            latest_delta = current_delta
            break
    if direction is not None and reason is not None:
        signal_time = (
            frame.index[signal_index].to_pydatetime() if hasattr(frame.index[signal_index], "to_pydatetime") else None
        )
        confidence = min(abs(float(latest_delta)) / max(float(close.iloc[signal_index]), 1.0) * 1000.0, 1.0)
        return TradeSignal(
            symbol=symbol,
            side=direction,
            source="technical_macd",
            signal_time=signal_time,
            reason=reason,
            confidence=confidence,
        )

    normalized = math.tanh(float(latest_delta) / max(float(close.iloc[-1]), 1.0) * 50.0)
    if abs(normalized) < CONTINUOUS_STRENGTH_THRESHOLD:
        return None
    signal_time = frame.index[-1].to_pydatetime() if hasattr(frame.index[-1], "to_pydatetime") else None
    return TradeSignal(
        symbol=symbol,
        side=TradeSide.LONG if normalized > 0 else TradeSide.SHORT,
        source="technical_macd",
        signal_time=signal_time,
        reason="macd_histogram",
        confidence=abs(normalized),
    )
