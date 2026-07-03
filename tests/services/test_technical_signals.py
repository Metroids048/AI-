from __future__ import annotations

import pandas as pd

from services.strategy_library.technical import generate_dow_trend_signal, generate_macd_signal


def test_macd_generates_structured_signal() -> None:
    closes = [100.0] * 35 + [101.0, 102.0, 104.0, 107.0, 111.0]
    frame = pd.DataFrame(
        {
            "open": closes,
            "high": [value + 1 for value in closes],
            "low": [value - 1 for value in closes],
            "close": closes,
            "volume": [100.0] * len(closes),
        },
        index=pd.date_range("2024-01-01", periods=len(closes), freq="h", tz="UTC"),
    )

    signal = generate_macd_signal(frame, symbol="BTC/USDT")

    assert signal is not None
    assert signal.source == "technical_macd"
    assert signal.reason in {"macd_bullish_cross", "macd_bearish_cross"}


def test_dow_trend_generates_structured_signal() -> None:
    highs = [10, 11, 10, 12, 11, 13, 12, 14, 13, 15, 14, 16]
    lows = [8, 9, 8.5, 9.5, 9, 10, 9.5, 10.5, 10, 11, 10.5, 11.5]
    frame = pd.DataFrame(
        {
            "open": lows,
            "high": highs,
            "low": lows,
            "close": [(high + low) / 2 for high, low in zip(highs, lows, strict=True)],
            "volume": [100.0] * len(highs),
        },
        index=pd.date_range("2024-01-01", periods=len(highs), freq="h", tz="UTC"),
    )

    signal = generate_dow_trend_signal(frame, symbol="BTC/USDT", pivot_window=1)

    assert signal is not None
    assert signal.source == "technical_dow_trend"
    assert signal.reason == "dow_higher_high_higher_low"
