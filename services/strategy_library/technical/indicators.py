"""Additional deterministic indicator signals for crypto technical strategies."""

from __future__ import annotations

import pandas as pd

from shared.models import TradeSide, TradeSignal


def generate_rsi_signal(
    frame: pd.DataFrame,
    *,
    symbol: str,
    period: int = 14,
    oversold: float = 30.0,
    overbought: float = 70.0,
) -> TradeSignal | None:
    """Emit RSI reversal signals only at clear overbought/oversold extremes."""

    if len(frame) < period + 2:
        return None
    close = frame["close"].astype(float)
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / loss.replace(0, 1e-12)
    rsi = 100 - (100 / (1 + rs))
    latest = float(rsi.iloc[-1])
    previous = float(rsi.iloc[-2])
    signal_time = _signal_time(frame)
    if previous < oversold <= latest:
        confidence = min((oversold - min(previous, latest)) / max(oversold, 1.0) + 0.35, 1.0)
        return TradeSignal(
            symbol=symbol,
            side=TradeSide.LONG,
            source="technical_rsi",
            signal_time=signal_time,
            reason="rsi_oversold_recovery",
            confidence=confidence,
        )
    if previous > overbought >= latest:
        confidence = min((max(previous, latest) - overbought) / max(100 - overbought, 1.0) + 0.35, 1.0)
        return TradeSignal(
            symbol=symbol,
            side=TradeSide.SHORT,
            source="technical_rsi",
            signal_time=signal_time,
            reason="rsi_overbought_rejection",
            confidence=confidence,
        )
    return None


def generate_ema_trend_signal(
    frame: pd.DataFrame,
    *,
    symbol: str,
    fast: int = 20,
    slow: int = 50,
) -> TradeSignal | None:
    """Emit trend-continuation signal from EMA alignment and slope."""

    if len(frame) < slow + 2:
        return None
    close = frame["close"].astype(float)
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    spread = float((ema_fast.iloc[-1] - ema_slow.iloc[-1]) / max(close.iloc[-1], 1.0))
    slope = 0.0
    if len(frame) >= slow + 5:
        slope = float((ema_slow.iloc[-1] - ema_slow.iloc[-5]) / max(ema_slow.iloc[-5], 1.0))
    raw = spread + slope
    if abs(raw) < 0.0015:
        return None
    return TradeSignal(
        symbol=symbol,
        side=TradeSide.LONG if raw > 0 else TradeSide.SHORT,
        source="technical_ema_trend",
        signal_time=_signal_time(frame),
        reason="ema_fast_slow_trend_alignment",
        confidence=min(abs(raw) * 80.0, 1.0),
    )


def generate_adx_trend_signal(
    frame: pd.DataFrame,
    *,
    symbol: str,
    period: int = 14,
    threshold: float = 22.0,
) -> TradeSignal | None:
    """Emit directional trend signal when ADX confirms directional movement."""

    if len(frame) < period * 2 + 2:
        return None
    high = frame["high"].astype(float)
    low = frame["low"].astype(float)
    close = frame["close"].astype(float)
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
    minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)
    tr = pd.concat(
        [
            high - low,
            (high - close.shift()).abs(),
            (low - close.shift()).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr = tr.ewm(alpha=1 / period, adjust=False).mean().replace(0, 1e-12)
    plus_di = 100 * plus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr
    minus_di = 100 * minus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr
    dx = ((plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, 1e-12)) * 100
    adx = dx.ewm(alpha=1 / period, adjust=False).mean()
    latest_adx = float(adx.iloc[-1])
    if latest_adx < threshold:
        return None
    plus = float(plus_di.iloc[-1])
    minus = float(minus_di.iloc[-1])
    if abs(plus - minus) < 1.0:
        return None
    return TradeSignal(
        symbol=symbol,
        side=TradeSide.LONG if plus > minus else TradeSide.SHORT,
        source="technical_adx",
        signal_time=_signal_time(frame),
        reason="adx_directional_trend_confirmed",
        confidence=min((latest_adx - threshold) / max(50.0 - threshold, 1.0) + abs(plus - minus) / 100.0, 1.0),
    )


def generate_vwap_reclaim_signal(frame: pd.DataFrame, *, symbol: str, lookback: int = 48) -> TradeSignal | None:
    """Emit VWAP reclaim/rejection signal for intraday-style crypto entries."""

    if len(frame) < lookback + 2:
        return None
    recent = frame.iloc[-lookback:]
    typical = (recent["high"].astype(float) + recent["low"].astype(float) + recent["close"].astype(float)) / 3.0
    volume = recent["volume"].astype(float).replace(0, 1e-12)
    vwap = float((typical * volume).sum() / volume.sum())
    previous_close = float(frame["close"].iloc[-2])
    latest_close = float(frame["close"].iloc[-1])
    if previous_close <= vwap < latest_close:
        return TradeSignal(
            symbol=symbol,
            side=TradeSide.LONG,
            source="technical_vwap",
            signal_time=_signal_time(frame),
            reason="vwap_reclaim",
            confidence=min((latest_close - vwap) / max(latest_close, 1.0) * 40.0 + 0.3, 1.0),
        )
    if previous_close >= vwap > latest_close:
        return TradeSignal(
            symbol=symbol,
            side=TradeSide.SHORT,
            source="technical_vwap",
            signal_time=_signal_time(frame),
            reason="vwap_rejection",
            confidence=min((vwap - latest_close) / max(latest_close, 1.0) * 40.0 + 0.3, 1.0),
        )
    return None


def generate_bollinger_reversion_signal(
    frame: pd.DataFrame,
    *,
    symbol: str,
    period: int = 20,
    deviations: float = 2.0,
) -> TradeSignal | None:
    """Emit mean-reversion signal when price returns inside stretched Bollinger bands."""

    if len(frame) < period + 2:
        return None
    close = frame["close"].astype(float)
    mid = close.rolling(period).mean()
    std = close.rolling(period).std(ddof=0)
    upper = mid + deviations * std
    lower = mid - deviations * std
    prev_close = float(close.iloc[-2])
    latest_close = float(close.iloc[-1])
    prev_lower = float(lower.iloc[-2])
    latest_lower = float(lower.iloc[-1])
    prev_upper = float(upper.iloc[-2])
    latest_upper = float(upper.iloc[-1])
    if prev_close < prev_lower and latest_close >= latest_lower:
        return TradeSignal(
            symbol=symbol,
            side=TradeSide.LONG,
            source="technical_bollinger",
            signal_time=_signal_time(frame),
            reason="bollinger_lower_band_reentry",
            confidence=min(abs(prev_lower - prev_close) / max(latest_close, 1.0) * 60.0 + 0.35, 1.0),
        )
    if prev_close > prev_upper and latest_close <= latest_upper:
        return TradeSignal(
            symbol=symbol,
            side=TradeSide.SHORT,
            source="technical_bollinger",
            signal_time=_signal_time(frame),
            reason="bollinger_upper_band_reentry",
            confidence=min(abs(prev_close - prev_upper) / max(latest_close, 1.0) * 60.0 + 0.35, 1.0),
        )
    return None


def _signal_time(frame: pd.DataFrame):  # noqa: ANN202
    return frame.index[-1].to_pydatetime() if hasattr(frame.index[-1], "to_pydatetime") else None
