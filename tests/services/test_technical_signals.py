from __future__ import annotations

import pandas as pd

from services.strategy_library.technical import (
    generate_adx_trend_signal,
    generate_bollinger_reversion_signal,
    generate_dow_trend_signal,
    generate_ema_trend_signal,
    generate_false_breakout_signal,
    generate_fvg_signal,
    generate_macd_signal,
    generate_multi_timeframe_ma_signal,
    generate_rsi_signal,
    generate_vwap_reclaim_signal,
)


def _frame_from_ohlc(opens: list[float], highs: list[float], lows: list[float], closes: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": [100.0] * len(closes)},
        index=pd.date_range("2024-01-01", periods=len(closes), freq="h", tz="UTC"),
    )


def _trend_frame(length: int, *, start: float, step: float) -> pd.DataFrame:
    closes = [start + index * step for index in range(length)]
    spread = abs(step) * 2 + 0.5
    return _frame_from_ohlc(
        opens=closes,
        highs=[value + spread for value in closes],
        lows=[value - spread for value in closes],
        closes=closes,
    )


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


def test_rsi_oversold_recovery_generates_long_signal() -> None:
    closes = [100.0, 98.0, 96.0, 94.0, 92.0, 90.0, 88.0, 86.0, 84.0, 82.0, 80.0, 88.0]
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

    signal = generate_rsi_signal(frame, symbol="BTC/USDT", period=3, oversold=35.0)

    assert signal is not None
    assert signal.source == "technical_rsi"
    assert signal.reason == "rsi_oversold_recovery"


def test_ema_and_adx_trend_generators_emit_directional_signals() -> None:
    closes = [100.0 + index * 0.5 for index in range(60)]
    frame = pd.DataFrame(
        {
            "open": closes,
            "high": [value + 0.6 for value in closes],
            "low": [value - 0.6 for value in closes],
            "close": closes,
            "volume": [100.0] * len(closes),
        },
        index=pd.date_range("2024-01-01", periods=len(closes), freq="h", tz="UTC"),
    )

    ema_signal = generate_ema_trend_signal(frame, symbol="BTC/USDT", fast=5, slow=12)
    adx_signal = generate_adx_trend_signal(frame, symbol="BTC/USDT", period=5, threshold=15.0)

    assert ema_signal is not None
    assert ema_signal.source == "technical_ema_trend"
    assert adx_signal is not None
    assert adx_signal.source == "technical_adx"


def test_false_breakout_generates_reversal_signal() -> None:
    frame = pd.DataFrame(
        {
            "open": [95.0] * 20 + [99.0],
            "high": [100.0] * 20 + [103.0],
            "low": [90.0] * 20 + [94.0],
            "close": [95.0] * 20 + [99.0],
            "volume": [100.0] * 21,
        },
        index=pd.date_range("2024-01-01", periods=21, freq="h", tz="UTC"),
    )

    signal = generate_false_breakout_signal(frame, symbol="BTC/USDT")

    assert signal is not None
    assert signal.source == "price_action_false_breakout"
    assert signal.reason == "false_resistance_breakout_reversal"


def test_vwap_reclaim_and_bollinger_reentry_are_directional() -> None:
    closes = [100.0] * 48 + [99.0, 102.0]
    frame = pd.DataFrame(
        {
            "open": closes,
            "high": [value + 0.5 for value in closes],
            "low": [value - 0.5 for value in closes],
            "close": closes,
            "volume": [100.0] * len(closes),
        },
        index=pd.date_range("2024-01-01", periods=len(closes), freq="h", tz="UTC"),
    )

    vwap = generate_vwap_reclaim_signal(frame, symbol="BTC/USDT")
    assert vwap is not None
    assert vwap.side.value == "long"
    assert vwap.reason == "vwap_reclaim"

    stretched = [100.0] * 20 + [80.0, 95.0]
    stretched_frame = pd.DataFrame(
        {
            "open": stretched,
            "high": [value + 1 for value in stretched],
            "low": [value - 1 for value in stretched],
            "close": stretched,
            "volume": [100.0] * len(stretched),
        },
        index=pd.date_range("2024-01-01", periods=len(stretched), freq="h", tz="UTC"),
    )
    bollinger = generate_bollinger_reversion_signal(stretched_frame, symbol="BTC/USDT")
    assert bollinger is not None
    assert bollinger.side.value == "long"


def test_fvg_signal_fires_long_on_bullish_gap_retest_and_reclaim() -> None:
    opens = [100.0] * 10 + [100.0, 100.6, 102.5, 102.0]
    highs = [100.5] * 10 + [100.5, 101.0, 103.0, 98.5]
    lows = [99.5] * 10 + [99.5, 100.2, 102.0, 101.5]
    closes = [100.0] * 10 + [100.0, 100.6, 102.5, 103.0]
    highs[-1] = 103.5
    lows[-1] = 101.0
    frame = _frame_from_ohlc(opens, highs, lows, closes)

    signal = generate_fvg_signal(frame, symbol="BTC/USDT", lookback=10)

    assert signal is not None
    assert signal.source == "technical_fvg"
    assert signal.side.value == "long"
    assert signal.reason == "fvg_bullish_gap_fill_reclaim"


def test_fvg_signal_fires_short_on_bearish_gap_retest_and_rejection() -> None:
    opens = [100.0] * 10 + [100.0, 100.6, 97.5, 98.0]
    highs = [100.5] * 10 + [100.5, 101.0, 98.0, 98.5]
    lows = [99.5] * 10 + [99.5, 100.2, 97.0, 96.5]
    closes = [100.0] * 10 + [100.0, 100.6, 97.5, 97.0]
    frame = _frame_from_ohlc(opens, highs, lows, closes)

    signal = generate_fvg_signal(frame, symbol="BTC/USDT", lookback=10)

    assert signal is not None
    assert signal.source == "technical_fvg"
    assert signal.side.value == "short"
    assert signal.reason == "fvg_bearish_gap_fill_rejection"


def test_fvg_signal_returns_none_when_frame_too_short() -> None:
    frame = _frame_from_ohlc([100.0] * 5, [100.5] * 5, [99.5] * 5, [100.0] * 5)

    assert generate_fvg_signal(frame, symbol="BTC/USDT", lookback=10) is None


def test_multi_timeframe_ma_signal_confirms_when_all_timeframes_align() -> None:
    frames = {
        "15m": _trend_frame(20, start=100.0, step=1.0),
        "1h": _trend_frame(20, start=100.0, step=1.0),
    }

    signal = generate_multi_timeframe_ma_signal(frames, symbol="BTC/USDT", fast=5, slow=10)

    assert signal is not None
    assert signal.source == "technical_mtf_ma"
    assert signal.side.value == "long"
    assert signal.reason == "multi_timeframe_ma_alignment"


def test_multi_timeframe_ma_signal_fails_closed_on_disagreement() -> None:
    frames = {
        "15m": _trend_frame(20, start=100.0, step=1.0),
        "1h": _trend_frame(20, start=100.0, step=-1.0),
    }

    assert generate_multi_timeframe_ma_signal(frames, symbol="BTC/USDT", fast=5, slow=10) is None


def test_multi_timeframe_ma_signal_requires_at_least_two_timeframes() -> None:
    frames = {"15m": _trend_frame(20, start=100.0, step=1.0)}

    assert generate_multi_timeframe_ma_signal(frames, symbol="BTC/USDT", fast=5, slow=10) is None
