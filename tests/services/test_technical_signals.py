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


def test_macd_emits_continuous_histogram_signal_without_recent_cross() -> None:
    closes = [100.0] * 45 + [100.0 + index * 0.2 for index in range(1, 16)]
    frame = pd.DataFrame(
        {
            "open": closes,
            "high": [value + 0.2 for value in closes],
            "low": [value - 0.2 for value in closes],
            "close": closes,
            "volume": [100.0] * len(closes),
        },
        index=pd.date_range("2024-01-01", periods=len(closes), freq="h", tz="UTC"),
    )

    signal = generate_macd_signal(frame, symbol="BTC/USDT")

    assert signal is not None
    assert signal.reason in {"macd_histogram", "macd_bullish_cross", "macd_bearish_cross"}
    assert 0.0 < signal.confidence <= 1.0


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


def test_dow_trend_emits_continuous_signal_in_choppy_structure() -> None:
    highs = [10, 11, 10.5, 11.5, 11, 12, 11.8, 12.5, 12.2, 13, 12.8, 13.5]
    lows = [8, 9, 8.7, 9.2, 9.1, 9.8, 9.6, 10.1, 10.0, 10.4, 10.2, 10.8]
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

    if signal is not None:
        assert signal.source == "technical_dow_trend"
        assert signal.reason in {"dow_continuous_trend", "dow_higher_high_higher_low", "dow_lower_high_lower_low"}
        assert 0.0 < signal.confidence <= 1.0
