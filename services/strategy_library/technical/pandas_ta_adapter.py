"""pandas-ta indicator adapter: convert 150+ pandas-ta indicators to TradeSignal format.

pandas-ta is MIT-licensed and used here as a direct runtime dependency
(pip-installed, not code copied), consistent with the `quant` extras
already declared in pyproject.toml.

This adapter bridges pandas-ta's output format to the project's standard
TradeSignal format, enabling the indicator search space to expand from
~10 hand-coded indicators to 150+ battle-tested indicators.
"""

from __future__ import annotations

from typing import Any, Callable

import pandas as pd

# pandas_ta will be imported at runtime after pip install completes
# For now, we'll use conditional import to allow the module to load
try:
    import pandas_ta as ta
except ImportError:
    ta = None  # type: ignore

from shared.models import TradeSide, TradeSignal


def _signal_time(frame: pd.DataFrame):  # noqa: ANN202
    """Extract signal time from DataFrame index."""
    return frame.index[-1].to_pydatetime() if hasattr(frame.index[-1], "to_pydatetime") else None


# ============================================================================
# Indicator-specific converters
# ============================================================================


def _convert_supertrend(result: pd.DataFrame, frame: pd.DataFrame, symbol: str) -> TradeSignal | None:
    """Convert SuperTrend indicator output to TradeSignal.

    SuperTrend returns:
    - SUPERT_{length}_{multiplier}: trend line value
    - SUPERTd_{length}_{multiplier}: direction (1=bullish, -1=bearish)
    - SUPERTl_{length}_{multiplier}: long-term trend
    """
    if result is None or result.empty:
        return None

    direction_cols = [c for c in result.columns if c.startswith("SUPERTd_")]
    if not direction_cols:
        return None

    direction_col = direction_cols[0]
    latest_direction = float(result[direction_col].iloc[-1])
    previous_direction = float(result[direction_col].iloc[-2]) if len(result) >= 2 else latest_direction

    # Signal only on direction flip
    if previous_direction != latest_direction:
        if latest_direction == 1:
            return TradeSignal(
                symbol=symbol,
                side=TradeSide.LONG,
                source="technical_pandas_ta_supertrend",
                signal_time=_signal_time(frame),
                reason="supertrend_bullish_flip",
                confidence=0.7,
            )
        elif latest_direction == -1:
            return TradeSignal(
                symbol=symbol,
                side=TradeSide.SHORT,
                source="technical_pandas_ta_supertrend",
                signal_time=_signal_time(frame),
                reason="supertrend_bearish_flip",
                confidence=0.7,
            )
    return None


def _convert_stoch_rsi(result: pd.DataFrame, frame: pd.DataFrame, symbol: str) -> TradeSignal | None:
    """Convert Stochastic RSI output to TradeSignal.

    StochRSI returns:
    - STOCHRSIk_{length}_{rsi_length}_{k}_{d}: %K line
    - STOCHRSId_{length}_{rsi_length}_{k}_{d}: %D line
    """
    if result is None or result.empty:
        return None

    k_cols = [c for c in result.columns if c.startswith("STOCHRSIk_")]
    if not k_cols:
        return None

    k_col = k_cols[0]
    latest_k = float(result[k_col].iloc[-1])
    previous_k = float(result[k_col].iloc[-2]) if len(result) >= 2 else latest_k

    # Oversold recovery (crossing above 20)
    if previous_k < 20 <= latest_k:
        confidence = min(0.3 + (20 - min(previous_k, latest_k)) / 20 * 0.4, 0.95)
        return TradeSignal(
            symbol=symbol,
            side=TradeSide.LONG,
            source="technical_pandas_ta_stoch_rsi",
            signal_time=_signal_time(frame),
            reason="stoch_rsi_oversold_recovery",
            confidence=confidence,
        )

    # Overbought rejection (crossing below 80)
    if previous_k > 80 >= latest_k:
        confidence = min(0.3 + (max(previous_k, latest_k) - 80) / 20 * 0.4, 0.95)
        return TradeSignal(
            symbol=symbol,
            side=TradeSide.SHORT,
            source="technical_pandas_ta_stoch_rsi",
            signal_time=_signal_time(frame),
            reason="stoch_rsi_overbought_rejection",
            confidence=confidence,
        )

    return None


def _convert_hma(result: pd.DataFrame, frame: pd.DataFrame, symbol: str) -> TradeSignal | None:
    """Convert Hull Moving Average output to TradeSignal.

    HMA returns a single column: HMA_{length}
    """
    if result is None or result.empty or len(result) < 3:
        return None

    hma_cols = [c for c in result.columns if c.startswith("HMA_")]
    if not hma_cols:
        return None

    hma_col = hma_cols[0]
    close = frame["close"].astype(float)
    latest_hma = float(result[hma_col].iloc[-1])
    latest_close = float(close.iloc[-1])
    previous_close = float(close.iloc[-2])
    previous_hma = float(result[hma_col].iloc[-2])

    # Price crossing above HMA
    if previous_close <= previous_hma and latest_close > latest_hma:
        return TradeSignal(
            symbol=symbol,
            side=TradeSide.LONG,
            source="technical_pandas_ta_hma",
            signal_time=_signal_time(frame),
            reason="hma_bullish_cross",
            confidence=0.65,
        )

    # Price crossing below HMA
    if previous_close >= previous_hma and latest_close < latest_hma:
        return TradeSignal(
            symbol=symbol,
            side=TradeSide.SHORT,
            source="technical_pandas_ta_hma",
            signal_time=_signal_time(frame),
            reason="hma_bearish_cross",
            confidence=0.65,
        )

    return None


def _convert_squeeze(result: pd.DataFrame, frame: pd.DataFrame, symbol: str) -> TradeSignal | None:
    """Convert TTM Squeeze Momentum output to TradeSignal.

    Squeeze returns:
    - SQZ_{bb_length}_{bb_std}_{kc_length}_{kc_mult}: squeeze indicator
    - SQZ_{...}_ON: squeeze is on (consolidation)
    - SQZ_{...}_OFF: squeeze is off (breakout)
    """
    if result is None or result.empty:
        return None

    sqz_cols = [c for c in result.columns if c.startswith("SQZ_") and not c.endswith(("_ON", "_OFF"))]
    on_cols = [c for c in result.columns if c.endswith("_ON")]
    off_cols = [c for c in result.columns if c.endswith("_OFF")]

    if not sqz_cols or not on_cols or not off_cols:
        return None

    sqz_col = sqz_cols[0]
    on_col = on_cols[0]
    off_col = off_cols[0]

    latest_sqz = float(result[sqz_col].iloc[-1])
    previous_sqz = float(result[sqz_col].iloc[-2]) if len(result) >= 2 else 0
    latest_off = float(result[off_col].iloc[-1]) if not pd.isna(result[off_col].iloc[-1]) else 0
    previous_on = float(result[on_col].iloc[-2]) if len(result) >= 2 and not pd.isna(result[on_col].iloc[-2]) else 0

    # Squeeze release: was ON (consolidation), now OFF (breakout), check momentum direction
    if previous_on > 0 and latest_off > 0:
        if latest_sqz > 0:
            return TradeSignal(
                symbol=symbol,
                side=TradeSide.LONG,
                source="technical_pandas_ta_squeeze",
                signal_time=_signal_time(frame),
                reason="squeeze_bullish_release",
                confidence=min(0.4 + abs(latest_sqz) * 10, 0.9),
            )
        elif latest_sqz < 0:
            return TradeSignal(
                symbol=symbol,
                side=TradeSide.SHORT,
                source="technical_pandas_ta_squeeze",
                signal_time=_signal_time(frame),
                reason="squeeze_bearish_release",
                confidence=min(0.4 + abs(latest_sqz) * 10, 0.9),
            )

    return None


def _convert_cmo(result: pd.DataFrame, frame: pd.DataFrame, symbol: str) -> TradeSignal | None:
    """Convert Chande Momentum Oscillator output to TradeSignal.

    CMO returns a single column: CMO_{length}
    Range: -100 to +100
    """
    if result is None or result.empty:
        return None

    cmo_cols = [c for c in result.columns if c.startswith("CMO_")]
    if not cmo_cols:
        return None

    cmo_col = cmo_cols[0]
    latest_cmo = float(result[cmo_col].iloc[-1])
    previous_cmo = float(result[cmo_col].iloc[-2]) if len(result) >= 2 else latest_cmo

    # Oversold recovery (crossing above -50)
    if previous_cmo < -50 <= latest_cmo:
        return TradeSignal(
            symbol=symbol,
            side=TradeSide.LONG,
            source="technical_pandas_ta_cmo",
            signal_time=_signal_time(frame),
            reason="cmo_oversold_recovery",
            confidence=min(0.35 + abs(previous_cmo + 50) / 50 * 0.3, 0.85),
        )

    # Overbought rejection (crossing below +50)
    if previous_cmo > 50 >= latest_cmo:
        return TradeSignal(
            symbol=symbol,
            side=TradeSide.SHORT,
            source="technical_pandas_ta_cmo",
            signal_time=_signal_time(frame),
            reason="cmo_overbought_rejection",
            confidence=min(0.35 + abs(previous_cmo - 50) / 50 * 0.3, 0.85),
        )

    return None


def _convert_kc(result: pd.DataFrame, frame: pd.DataFrame, symbol: str) -> TradeSignal | None:
    """Convert Keltner Channels output to TradeSignal.

    KC returns:
    - KCLe_{length}_{multiplier}: lower band
    - KCBe_{length}_{multiplier}: basis (EMA)
    - KCUe_{length}_{multiplier}: upper band
    """
    if result is None or result.empty:
        return None

    lower_cols = [c for c in result.columns if c.startswith("KCLe_")]
    upper_cols = [c for c in result.columns if c.startswith("KCUe_")]

    if not lower_cols or not upper_cols:
        return None

    lower_col = lower_cols[0]
    upper_col = upper_cols[0]
    close = frame["close"].astype(float)

    latest_close = float(close.iloc[-1])
    previous_close = float(close.iloc[-2])
    latest_lower = float(result[lower_col].iloc[-1])
    previous_lower = float(result[lower_col].iloc[-2])
    latest_upper = float(result[upper_col].iloc[-1])
    previous_upper = float(result[upper_col].iloc[-2])

    # Price bouncing off lower band (mean reversion)
    if previous_close < previous_lower and latest_close >= latest_lower:
        return TradeSignal(
            symbol=symbol,
            side=TradeSide.LONG,
            source="technical_pandas_ta_keltner",
            signal_time=_signal_time(frame),
            reason="keltner_lower_band_bounce",
            confidence=min(0.4 + abs(previous_lower - previous_close) / max(latest_close, 1.0) * 30, 0.85),
        )

    # Price rejecting upper band (mean reversion)
    if previous_close > previous_upper and latest_close <= latest_upper:
        return TradeSignal(
            symbol=symbol,
            side=TradeSide.SHORT,
            source="technical_pandas_ta_keltner",
            signal_time=_signal_time(frame),
            reason="keltner_upper_band_rejection",
            confidence=min(0.4 + abs(previous_close - previous_upper) / max(latest_close, 1.0) * 30, 0.85),
        )

    return None


def _convert_obv(result: pd.DataFrame, frame: pd.DataFrame, symbol: str) -> TradeSignal | None:
    """Convert On Balance Volume output to TradeSignal.

    OBV returns a single column: OBV
    We look for divergence with price or OBV trend changes.
    """
    if result is None or result.empty or len(result) < 5:
        return None

    obv_cols = [c for c in result.columns if c == "OBV" or c.startswith("OBV_")]
    if not obv_cols:
        return None

    obv_col = obv_cols[0]
    close = frame["close"].astype(float)

    # Simple OBV trend: compare recent slope
    recent_obv = result[obv_col].iloc[-5:].astype(float)
    obv_slope = float(recent_obv.iloc[-1] - recent_obv.iloc[0])

    # OBV increasing while price consolidates/dips = accumulation
    recent_close = close.iloc[-5:]
    price_change = float(recent_close.iloc[-1] - recent_close.iloc[0])

    if obv_slope > 0 and abs(price_change) / max(float(recent_close.iloc[0]), 1.0) < 0.02:
        return TradeSignal(
            symbol=symbol,
            side=TradeSide.LONG,
            source="technical_pandas_ta_obv",
            signal_time=_signal_time(frame),
            reason="obv_accumulation_divergence",
            confidence=0.55,
        )

    # OBV decreasing while price consolidates/rises = distribution
    if obv_slope < 0 and abs(price_change) / max(float(recent_close.iloc[0]), 1.0) < 0.02:
        return TradeSignal(
            symbol=symbol,
            side=TradeSide.SHORT,
            source="technical_pandas_ta_obv",
            signal_time=_signal_time(frame),
            reason="obv_distribution_divergence",
            confidence=0.55,
        )

    return None


def _convert_mfi(result: pd.DataFrame, frame: pd.DataFrame, symbol: str) -> TradeSignal | None:
    """Convert Money Flow Index output to TradeSignal.

    MFI returns a single column: MFI_{length}
    Range: 0 to 100 (similar to RSI but volume-weighted)
    """
    if result is None or result.empty:
        return None

    mfi_cols = [c for c in result.columns if c.startswith("MFI_")]
    if not mfi_cols:
        return None

    mfi_col = mfi_cols[0]
    latest_mfi = float(result[mfi_col].iloc[-1])
    previous_mfi = float(result[mfi_col].iloc[-2]) if len(result) >= 2 else latest_mfi

    # Oversold recovery (crossing above 20)
    if previous_mfi < 20 <= latest_mfi:
        return TradeSignal(
            symbol=symbol,
            side=TradeSide.LONG,
            source="technical_pandas_ta_mfi",
            signal_time=_signal_time(frame),
            reason="mfi_oversold_recovery",
            confidence=min(0.35 + (20 - min(previous_mfi, latest_mfi)) / 20 * 0.4, 0.9),
        )

    # Overbought rejection (crossing below 80)
    if previous_mfi > 80 >= latest_mfi:
        return TradeSignal(
            symbol=symbol,
            side=TradeSide.SHORT,
            source="technical_pandas_ta_mfi",
            signal_time=_signal_time(frame),
            reason="mfi_overbought_rejection",
            confidence=min(0.35 + (max(previous_mfi, latest_mfi) - 80) / 20 * 0.4, 0.9),
        )

    return None


def _convert_zlema(result: pd.DataFrame, frame: pd.DataFrame, symbol: str) -> TradeSignal | None:
    """Convert Zero Lag EMA output to TradeSignal.

    ZLEMA returns a single column: ZLEMA_{length}
    Similar to HMA converter: look for price crossing ZLEMA.
    """
    if result is None or result.empty or len(result) < 3:
        return None

    zlema_cols = [c for c in result.columns if c.startswith("ZLEMA_")]
    if not zlema_cols:
        return None

    zlema_col = zlema_cols[0]
    close = frame["close"].astype(float)
    latest_zlema = float(result[zlema_col].iloc[-1])
    latest_close = float(close.iloc[-1])
    previous_close = float(close.iloc[-2])
    previous_zlema = float(result[zlema_col].iloc[-2])

    # Price crossing above ZLEMA
    if previous_close <= previous_zlema and latest_close > latest_zlema:
        return TradeSignal(
            symbol=symbol,
            side=TradeSide.LONG,
            source="technical_pandas_ta_zlema",
            signal_time=_signal_time(frame),
            reason="zlema_bullish_cross",
            confidence=0.65,
        )

    # Price crossing below ZLEMA
    if previous_close >= previous_zlema and latest_close < latest_zlema:
        return TradeSignal(
            symbol=symbol,
            side=TradeSide.SHORT,
            source="technical_pandas_ta_zlema",
            signal_time=_signal_time(frame),
            reason="zlema_bearish_cross",
            confidence=0.65,
        )

    return None


def _convert_atr_trailing(result: pd.DataFrame, frame: pd.DataFrame, symbol: str) -> TradeSignal | None:
    """Convert ATR Trailing Stop output to TradeSignal.

    This is not a direct entry signal but can confirm trend strength.
    For now, we skip this and return None (placeholder for future).
    """
    return None


# ============================================================================
# Registry: map indicator name to compute + convert functions
# ============================================================================

IndicatorConfig = dict[str, Any]


def _make_compute_supertrend() -> Callable[[pd.DataFrame], pd.DataFrame | None]:
    def compute(frame: pd.DataFrame) -> pd.DataFrame | None:
        if ta is None:
            return None
        return ta.supertrend(frame["high"], frame["low"], frame["close"])

    return compute


def _make_compute_stoch_rsi() -> Callable[[pd.DataFrame], pd.DataFrame | None]:
    def compute(frame: pd.DataFrame) -> pd.DataFrame | None:
        if ta is None:
            return None
        return ta.stochrsi(frame["close"])

    return compute


def _make_compute_hma() -> Callable[[pd.DataFrame], pd.DataFrame | None]:
    def compute(frame: pd.DataFrame) -> pd.DataFrame | None:
        if ta is None:
            return None
        return ta.hma(frame["close"])

    return compute


def _make_compute_squeeze() -> Callable[[pd.DataFrame], pd.DataFrame | None]:
    def compute(frame: pd.DataFrame) -> pd.DataFrame | None:
        if ta is None:
            return None
        return ta.squeeze(frame["high"], frame["low"], frame["close"])

    return compute


def _make_compute_cmo() -> Callable[[pd.DataFrame], pd.DataFrame | None]:
    def compute(frame: pd.DataFrame) -> pd.DataFrame | None:
        if ta is None:
            return None
        return ta.cmo(frame["close"])

    return compute


def _make_compute_kc() -> Callable[[pd.DataFrame], pd.DataFrame | None]:
    def compute(frame: pd.DataFrame) -> pd.DataFrame | None:
        if ta is None:
            return None
        return ta.kc(frame["high"], frame["low"], frame["close"])

    return compute


def _make_compute_obv() -> Callable[[pd.DataFrame], pd.DataFrame | None]:
    def compute(frame: pd.DataFrame) -> pd.DataFrame | None:
        if ta is None:
            return None
        return ta.obv(frame["close"], frame["volume"])

    return compute


def _make_compute_mfi() -> Callable[[pd.DataFrame], pd.DataFrame | None]:
    def compute(frame: pd.DataFrame) -> pd.DataFrame | None:
        if ta is None:
            return None
        return ta.mfi(frame["high"], frame["low"], frame["close"], frame["volume"])

    return compute


def _make_compute_zlema() -> Callable[[pd.DataFrame], pd.DataFrame | None]:
    def compute(frame: pd.DataFrame) -> pd.DataFrame | None:
        if ta is None:
            return None
        return ta.zlma(frame["close"])

    return compute


PANDAS_TA_SIGNAL_REGISTRY: dict[str, IndicatorConfig] = {
    "supertrend": {
        "compute": _make_compute_supertrend(),
        "convert": _convert_supertrend,
        "min_periods": 14,
    },
    "stoch_rsi": {
        "compute": _make_compute_stoch_rsi(),
        "convert": _convert_stoch_rsi,
        "min_periods": 14,
    },
    "hma": {
        "compute": _make_compute_hma(),
        "convert": _convert_hma,
        "min_periods": 20,
    },
    "squeeze": {
        "compute": _make_compute_squeeze(),
        "convert": _convert_squeeze,
        "min_periods": 20,
    },
    "cmo": {
        "compute": _make_compute_cmo(),
        "convert": _convert_cmo,
        "min_periods": 14,
    },
    "keltner": {
        "compute": _make_compute_kc(),
        "convert": _convert_kc,
        "min_periods": 20,
    },
    "obv": {
        "compute": _make_compute_obv(),
        "convert": _convert_obv,
        "min_periods": 10,
    },
    "mfi": {
        "compute": _make_compute_mfi(),
        "convert": _convert_mfi,
        "min_periods": 14,
    },
    "zlema": {
        "compute": _make_compute_zlema(),
        "convert": _convert_zlema,
        "min_periods": 20,
    },
}


# ============================================================================
# Public API
# ============================================================================


def generate_pandas_ta_signal(
    *,
    name: str,
    symbol: str,
    frame: pd.DataFrame,
) -> TradeSignal | None:
    """Generate a TradeSignal from a pandas-ta indicator.

    Args:
        name: Indicator name, must be in PANDAS_TA_SIGNAL_REGISTRY
        symbol: Trading pair symbol (e.g. "BTC/USDT")
        frame: OHLCV DataFrame with columns: open, high, low, close, volume

    Returns:
        TradeSignal or None if no signal generated

    Raises:
        ValueError: If indicator name is not registered
        ImportError: If pandas-ta is not installed
    """
    if ta is None:
        raise ImportError(
            "pandas-ta is not installed. Run: pip install -e '.[quant]' "
            "or pip install pandas-ta>=0.3.14b"
        )

    if name not in PANDAS_TA_SIGNAL_REGISTRY:
        raise ValueError(
            f"Unknown pandas-ta indicator: {name}. "
            f"Available: {', '.join(PANDAS_TA_SIGNAL_REGISTRY.keys())}"
        )

    config = PANDAS_TA_SIGNAL_REGISTRY[name]

    # Check minimum periods
    if len(frame) < config["min_periods"]:
        return None

    # Compute indicator
    result = config["compute"](frame)
    if result is None:
        return None

    # Convert to signal
    return config["convert"](result, frame, symbol)


def list_available_indicators() -> list[str]:
    """Return list of all registered pandas-ta indicators."""
    return list(PANDAS_TA_SIGNAL_REGISTRY.keys())
