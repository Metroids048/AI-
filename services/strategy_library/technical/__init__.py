"""Deterministic technical-rule signal modules."""

from __future__ import annotations

from .dow_trend import generate_dow_trend_signal
from .macd import generate_macd_signal
from .price_action import (
    generate_donchian_breakout_signal,
    generate_engulfing_signal,
    generate_pin_bar_signal,
    generate_price_action_signals,
)
from .volatility_regime import calculate_atr, classify_volatility_regime

__all__ = [
    "calculate_atr",
    "classify_volatility_regime",
    "generate_donchian_breakout_signal",
    "generate_dow_trend_signal",
    "generate_engulfing_signal",
    "generate_macd_signal",
    "generate_pin_bar_signal",
    "generate_price_action_signals",
]
