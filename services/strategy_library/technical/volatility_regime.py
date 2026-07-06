"""Volatility helpers for dynamic Paper risk plans."""

from __future__ import annotations

import pandas as pd


def calculate_atr(frame: pd.DataFrame, *, period: int = 14) -> float | None:
    """Calculate the latest Average True Range."""

    if len(frame) < period + 1:
        return None
    high = frame["high"].astype(float)
    low = frame["low"].astype(float)
    close = frame["close"].astype(float)
    previous_close = close.shift(1)
    true_range = pd.concat(
        [
            high - low,
            (high - previous_close).abs(),
            (low - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    value = true_range.rolling(period).mean().iloc[-1]
    if pd.isna(value):
        return None
    return float(value)


def classify_volatility_regime(frame: pd.DataFrame, *, period: int = 14) -> dict[str, float | str | None]:
    """Return a compact volatility summary for veto and audit context."""

    atr = calculate_atr(frame, period=period)
    if atr is None or frame.empty:
        return {"atr": atr, "atr_percent": None, "regime": "insufficient_data"}
    close = float(frame["close"].iloc[-1])
    atr_percent = atr / max(close, 1.0)
    if atr_percent >= 0.04:
        regime = "extreme_volatility"
    elif atr_percent >= 0.02:
        regime = "high_volatility"
    elif atr_percent <= 0.004:
        regime = "low_volatility"
    else:
        regime = "normal_volatility"
    return {"atr": atr, "atr_percent": atr_percent, "regime": regime}
