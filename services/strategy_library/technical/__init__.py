"""Deterministic technical-rule signal modules."""

from __future__ import annotations

from .dow_trend import generate_dow_trend_signal
from .macd import generate_macd_signal

__all__ = ["generate_dow_trend_signal", "generate_macd_signal"]
