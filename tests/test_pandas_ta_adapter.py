"""Unit tests for pandas_ta_adapter.

验证 pandas_ta_adapter 能否正确产出标准 TradeSignal 格式。
"""

import pandas as pd
import pytest

from services.strategy_library.technical.pandas_ta_adapter import (
    generate_pandas_ta_signal,
    list_available_indicators,
    ta,
)
from shared.models import TradeSide, TradeSignal

# pandas-ta lives in the optional `quant` extra and is not installed by CI's
# `pip install -e ".[dev]"`. Tests that actually invoke an indicator must skip
# when it is absent instead of hard-failing with ImportError; the adapter's
# registry-only surface (list_available_indicators) still runs unguarded.
requires_pandas_ta = pytest.mark.skipif(
    ta is None,
    reason="pandas-ta not installed (optional `quant` extra)",
)


def make_sample_ohlcv(length: int = 100, trend: str = "flat") -> pd.DataFrame:
    """创建模拟OHLCV数据用于测试。

    Args:
        length: 数据长度
        trend: "flat" | "up" | "down"
    """
    import numpy as np

    base_price = 50000.0
    dates = pd.date_range(end="2026-07-15", periods=length, freq="1h")

    if trend == "up":
        close = base_price + np.cumsum(np.random.randn(length) * 50 + 10)
    elif trend == "down":
        close = base_price - np.cumsum(np.random.randn(length) * 50 + 10)
    else:  # flat
        close = base_price + np.random.randn(length) * 100

    high = close + np.abs(np.random.randn(length) * 50)
    low = close - np.abs(np.random.randn(length) * 50)
    open_ = close + np.random.randn(length) * 30
    volume = np.abs(np.random.randn(length) * 1000000)

    return pd.DataFrame(
        {
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        },
        index=dates,
    )


def test_list_available_indicators():
    """测试指标列表函数。"""
    indicators = list_available_indicators()
    assert len(indicators) == 9  # 我们注册了9个指标（跳过了atr_trailing占位符）
    assert "supertrend" in indicators
    assert "stoch_rsi" in indicators
    assert "hma" in indicators


@requires_pandas_ta
def test_unknown_indicator_raises_error():
    """测试未知指标名称抛出错误。"""
    frame = make_sample_ohlcv()
    with pytest.raises(ValueError, match="Unknown pandas-ta indicator"):
        generate_pandas_ta_signal(name="unknown_indicator", symbol="BTC/USDT", frame=frame)


@requires_pandas_ta
def test_insufficient_data_returns_none():
    """测试数据不足返回None。"""
    frame = make_sample_ohlcv(length=5)  # 少于min_periods
    signal = generate_pandas_ta_signal(name="supertrend", symbol="BTC/USDT", frame=frame)
    assert signal is None


@requires_pandas_ta
def test_supertrend_signal_format():
    """测试SuperTrend信号格式正确。"""
    frame = make_sample_ohlcv(length=100, trend="up")
    signal = generate_pandas_ta_signal(name="supertrend", symbol="BTC/USDT", frame=frame)

    # 可能有信号也可能没有（取决于随机数据），但如果有信号，格式必须正确
    if signal is not None:
        assert isinstance(signal, TradeSignal)
        assert signal.symbol == "BTC/USDT"
        assert signal.side in [TradeSide.LONG, TradeSide.SHORT]
        assert signal.source == "technical_pandas_ta_supertrend"
        assert signal.reason in ["supertrend_bullish_flip", "supertrend_bearish_flip"]
        assert 0.0 <= signal.confidence <= 1.0
        assert signal.signal_time is not None


@requires_pandas_ta
def test_stoch_rsi_signal_format():
    """测试Stochastic RSI信号格式正确。"""
    frame = make_sample_ohlcv(length=100)
    signal = generate_pandas_ta_signal(name="stoch_rsi", symbol="BTC/USDT", frame=frame)

    if signal is not None:
        assert isinstance(signal, TradeSignal)
        assert signal.symbol == "BTC/USDT"
        assert signal.side in [TradeSide.LONG, TradeSide.SHORT]
        assert signal.source == "technical_pandas_ta_stoch_rsi"
        assert signal.reason in ["stoch_rsi_oversold_recovery", "stoch_rsi_overbought_rejection"]
        assert 0.0 <= signal.confidence <= 1.0


@requires_pandas_ta
def test_hma_signal_format():
    """测试Hull MA信号格式正确。"""
    frame = make_sample_ohlcv(length=100)
    signal = generate_pandas_ta_signal(name="hma", symbol="BTC/USDT", frame=frame)

    if signal is not None:
        assert isinstance(signal, TradeSignal)
        assert signal.symbol == "BTC/USDT"
        assert signal.side in [TradeSide.LONG, TradeSide.SHORT]
        assert signal.source == "technical_pandas_ta_hma"
        assert signal.reason in ["hma_bullish_cross", "hma_bearish_cross"]


@requires_pandas_ta
def test_mfi_signal_format():
    """测试Money Flow Index信号格式正确。"""
    frame = make_sample_ohlcv(length=100)
    signal = generate_pandas_ta_signal(name="mfi", symbol="BTC/USDT", frame=frame)

    if signal is not None:
        assert isinstance(signal, TradeSignal)
        assert signal.symbol == "BTC/USDT"
        assert signal.side in [TradeSide.LONG, TradeSide.SHORT]
        assert signal.source == "technical_pandas_ta_mfi"
        assert signal.reason in ["mfi_oversold_recovery", "mfi_overbought_rejection"]


@requires_pandas_ta
def test_obv_signal_format():
    """测试OBV信号格式正确。"""
    frame = make_sample_ohlcv(length=100)
    signal = generate_pandas_ta_signal(name="obv", symbol="BTC/USDT", frame=frame)

    if signal is not None:
        assert isinstance(signal, TradeSignal)
        assert signal.symbol == "BTC/USDT"
        assert signal.side in [TradeSide.LONG, TradeSide.SHORT]
        assert signal.source == "technical_pandas_ta_obv"
        assert signal.reason in ["obv_accumulation_divergence", "obv_distribution_divergence"]


@requires_pandas_ta
def test_all_indicators_callable():
    """测试所有注册的指标都可调用且不崩溃。"""
    frame = make_sample_ohlcv(length=100)
    indicators = list_available_indicators()

    for indicator in indicators:
        # 应该能调用成功，不抛出异常（可能返回None）
        signal = generate_pandas_ta_signal(name=indicator, symbol="BTC/USDT", frame=frame)
        # 如果有信号，格式必须正确
        if signal is not None:
            assert isinstance(signal, TradeSignal)
            assert signal.symbol == "BTC/USDT"
            assert signal.side in [TradeSide.LONG, TradeSide.SHORT]
            assert signal.source.startswith("technical_pandas_ta_")
            assert 0.0 <= signal.confidence <= 1.0


@requires_pandas_ta
def test_pandas_ta_import():
    """pandas_ta 已安装时，确认它确实可导入（可选依赖存在才运行）。"""
    import pandas_ta as ta  # noqa: F401

    assert ta is not None
