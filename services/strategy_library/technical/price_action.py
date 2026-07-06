"""Price-action signals for the Paper decision pipeline."""

from __future__ import annotations

import pandas as pd

from shared.models import TradeSide, TradeSignal


def generate_engulfing_signal(frame: pd.DataFrame, *, symbol: str) -> TradeSignal | None:
    """Detect the latest bullish/bearish engulfing candle."""

    if len(frame) < 2:
        return None
    previous = frame.iloc[-2]
    latest = frame.iloc[-1]
    prev_open = float(previous["open"])
    prev_close = float(previous["close"])
    open_ = float(latest["open"])
    close = float(latest["close"])
    if prev_close < prev_open and close > open_ and open_ <= prev_close and close >= prev_open:
        direction = TradeSide.LONG
        reason = "bullish_engulfing"
    elif prev_close > prev_open and close < open_ and open_ >= prev_close and close <= prev_open:
        direction = TradeSide.SHORT
        reason = "bearish_engulfing"
    else:
        return None
    return _signal(frame=frame, symbol=symbol, direction=direction, reason=reason, source="price_action_engulfing")


def generate_pin_bar_signal(
    frame: pd.DataFrame,
    *,
    symbol: str,
    wick_body_ratio: float = 2.0,
) -> TradeSignal | None:
    """Detect long-wick rejection candles."""

    if len(frame) < 1:
        return None
    latest = frame.iloc[-1]
    open_ = float(latest["open"])
    close = float(latest["close"])
    high = float(latest["high"])
    low = float(latest["low"])
    body = max(abs(close - open_), 1e-9)
    upper_wick = high - max(open_, close)
    lower_wick = min(open_, close) - low
    if lower_wick >= body * wick_body_ratio and upper_wick <= body * 1.25:
        return _signal(
            frame=frame,
            symbol=symbol,
            direction=TradeSide.LONG,
            reason="bullish_pin_bar_rejection",
            source="price_action_pin_bar",
        )
    if upper_wick >= body * wick_body_ratio and lower_wick <= body * 1.25:
        return _signal(
            frame=frame,
            symbol=symbol,
            direction=TradeSide.SHORT,
            reason="bearish_pin_bar_rejection",
            source="price_action_pin_bar",
        )
    return None


def generate_donchian_breakout_signal(
    frame: pd.DataFrame,
    *,
    symbol: str,
    lookback: int = 20,
) -> TradeSignal | None:
    """Detect simple support/resistance breakouts using a Donchian channel."""

    if len(frame) < lookback + 1:
        return None
    history = frame.iloc[-lookback - 1 : -1]
    latest = frame.iloc[-1]
    previous_high = float(history["high"].max())
    previous_low = float(history["low"].min())
    close = float(latest["close"])
    if close > previous_high:
        confidence = min((close - previous_high) / max(close, 1.0) * 20.0, 1.0)
        return _signal(
            frame=frame,
            symbol=symbol,
            direction=TradeSide.LONG,
            reason="donchian_resistance_breakout",
            source="price_action_donchian",
            confidence=confidence,
        )
    if close < previous_low:
        confidence = min((previous_low - close) / max(close, 1.0) * 20.0, 1.0)
        return _signal(
            frame=frame,
            symbol=symbol,
            direction=TradeSide.SHORT,
            reason="donchian_support_breakdown",
            source="price_action_donchian",
            confidence=confidence,
        )
    return None


def generate_price_action_signals(frame: pd.DataFrame, *, symbol: str) -> list[TradeSignal]:
    """Return the first batch of supported price-action signals."""

    candidates = [
        generate_engulfing_signal(frame, symbol=symbol),
        generate_pin_bar_signal(frame, symbol=symbol),
        generate_donchian_breakout_signal(frame, symbol=symbol),
    ]
    return [signal for signal in candidates if signal is not None]


def _signal(
    *,
    frame: pd.DataFrame,
    symbol: str,
    direction: TradeSide,
    reason: str,
    source: str,
    confidence: float | None = None,
) -> TradeSignal:
    signal_time = frame.index[-1].to_pydatetime() if hasattr(frame.index[-1], "to_pydatetime") else None
    if confidence is None:
        latest = frame.iloc[-1]
        candle_range = max(float(latest["high"]) - float(latest["low"]), 1e-9)
        confidence = min(abs(float(latest["close"]) - float(latest["open"])) / candle_range, 1.0)
    return TradeSignal(
        symbol=symbol,
        side=direction,
        source=source,
        signal_time=signal_time,
        reason=reason,
        confidence=float(confidence),
    )
