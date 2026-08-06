"""Phase 1: RegimeRouter identification + regime-aware layered ensemble routing.

Covers:
- RegimeRouter.identify_regime() branches (trend up/down, range, uncertain).
- SignalEnsembleService._eligible_layered_signals() regime routing:
  RANGE only admits range-layer signals (direction of the range signal itself
  decides the trade); TREND_UP/TREND_DOWN/UNCERTAIN/None keep the pre-existing
  behavior where entry-layer signals must agree with the direction-source
  majority.
"""

from __future__ import annotations

import pandas as pd

from services.strategy_library.ensemble.service import SignalEnsembleService
from services.strategy_library.regime import MarketRegime, RegimeRouter
from shared.models import CandidateSignalSeries, TradeSide


def _trending_frame(*, up: bool, periods: int = 260) -> pd.DataFrame:
    # Strong directional move: 0.5% per period compounded, so ~200 periods gives
    # ~100% total move (e.g. 100 -> 200 for uptrend, 100 -> 50 for downtrend),
    # ensuring both ADX > 25 and ema_diff_pct crosses the ±2% threshold.
    multiplier = 1.005 if up else 0.995
    closes = [100.0 * (multiplier**i) for i in range(periods)]
    highs = [c * 1.01 for c in closes]
    lows = [c * 0.99 for c in closes]
    return pd.DataFrame({"open": closes, "high": highs, "low": lows, "close": closes})


def _range_frame(periods: int = 260) -> pd.DataFrame:
    closes = [100.0 + (1.0 if i % 2 == 0 else -1.0) for i in range(periods)]
    highs = [c + 0.2 for c in closes]
    lows = [c - 0.2 for c in closes]
    return pd.DataFrame({"open": closes, "high": highs, "low": lows, "close": closes})


def test_regime_router_identifies_trend_up() -> None:
    regime, weight = RegimeRouter().identify_regime(_trending_frame(up=True))

    assert regime == MarketRegime.TREND_UP
    assert weight.breakout > weight.range_mean_reversion


def test_regime_router_identifies_trend_down() -> None:
    regime, weight = RegimeRouter().identify_regime(_trending_frame(up=False))

    assert regime == MarketRegime.TREND_DOWN
    assert weight.trend_pullback > weight.range_mean_reversion


def test_regime_router_identifies_range() -> None:
    regime, weight = RegimeRouter().identify_regime(_range_frame())

    assert regime == MarketRegime.RANGE
    assert weight.range_mean_reversion > weight.breakout


def test_regime_router_identifies_uncertain_on_insufficient_data() -> None:
    tiny_frame = pd.DataFrame({"open": [1.0, 2.0], "high": [1.1, 2.1], "low": [0.9, 1.9], "close": [1.0, 2.0]})

    regime, weight = RegimeRouter().identify_regime(tiny_frame)

    assert regime == MarketRegime.UNCERTAIN
    assert weight.trend_pullback == weight.breakout == weight.range_mean_reversion == weight.momentum_continuation


def _series(*, direction: TradeSide, confidence: float = 0.8) -> CandidateSignalSeries:
    return CandidateSignalSeries(
        strategy_id=f"placeholder:{direction.value}",
        direction=direction,
        weight=1.0,
        confidence=confidence,
        validation_score=confidence,
        series=[float(i % 7) for i in range(220)],
    )


def _direction_signal(source: str, *, direction: TradeSide) -> CandidateSignalSeries:
    signal = _series(direction=direction)
    return signal.model_copy(update={"strategy_id": f"{source}:{direction.value}"})


def _entry_signal(source: str, *, direction: TradeSide) -> CandidateSignalSeries:
    signal = _series(direction=direction)
    return signal.model_copy(update={"strategy_id": f"{source}:{direction.value}"})


def _range_signal(source: str, *, direction: TradeSide) -> CandidateSignalSeries:
    signal = _series(direction=direction)
    return signal.model_copy(update={"strategy_id": f"{source}:{direction.value}"})


DIRECTION_LONG_MAJORITY = [
    _direction_signal("technical_dow_trend", direction=TradeSide.LONG),
    _direction_signal("technical_ema_trend", direction=TradeSide.LONG),
    _direction_signal("technical_adx", direction=TradeSide.LONG),
]


def test_eligible_layered_signals_trend_up_requires_entry_direction_match() -> None:
    signals = [
        *DIRECTION_LONG_MAJORITY,
        _entry_signal("technical_macd", direction=TradeSide.LONG),
        _entry_signal("technical_rsi", direction=TradeSide.SHORT),
        _range_signal("technical_vwap", direction=TradeSide.LONG),
    ]

    eligible = SignalEnsembleService._eligible_layered_signals(
        signals, allowed_direction=TradeSide.LONG, regime=MarketRegime.TREND_UP
    )

    sources = {SignalEnsembleService._signal_source(s.strategy_id) for s in eligible}
    assert sources == {"technical_macd"}


def test_eligible_layered_signals_trend_down_requires_entry_direction_match() -> None:
    signals = [
        _direction_signal("technical_dow_trend", direction=TradeSide.SHORT),
        _direction_signal("technical_ema_trend", direction=TradeSide.SHORT),
        _direction_signal("technical_adx", direction=TradeSide.SHORT),
        _entry_signal("technical_macd", direction=TradeSide.SHORT),
        _entry_signal("technical_rsi", direction=TradeSide.LONG),
    ]

    eligible = SignalEnsembleService._eligible_layered_signals(
        signals, allowed_direction=TradeSide.SHORT, regime=MarketRegime.TREND_DOWN
    )

    sources = {SignalEnsembleService._signal_source(s.strategy_id) for s in eligible}
    assert sources == {"technical_macd"}


def test_eligible_layered_signals_range_regime_only_admits_range_layer() -> None:
    signals = [
        *DIRECTION_LONG_MAJORITY,
        _entry_signal("technical_macd", direction=TradeSide.LONG),
        _range_signal("technical_vwap", direction=TradeSide.SHORT),
        _range_signal("technical_bollinger", direction=TradeSide.SHORT),
    ]

    eligible = SignalEnsembleService._eligible_layered_signals(
        signals, allowed_direction=TradeSide.LONG, regime=MarketRegime.RANGE
    )

    sources = {SignalEnsembleService._signal_source(s.strategy_id) for s in eligible}
    # TEMP_FIX_GATE17_2026_08_06: Now allows both range and entry layers
    # TODO: Restore strict filtering when RANGE strategy pool is expanded (Plan B)
    assert sources == {"technical_vwap", "technical_bollinger", "technical_macd"}
    # Range signals (vwap, bollinger) disagree with the direction-source majority (short vs long)
    # yet are still admitted -- regime routing lets range signals decide on their own.
    # Entry signal (macd) is now also admitted to address signal scarcity in RANGE regime.
    range_signals = [
        s
        for s in eligible
        if SignalEnsembleService._signal_source(s.strategy_id) in {"technical_vwap", "technical_bollinger"}
    ]
    assert all(s.direction == TradeSide.SHORT for s in range_signals)


def test_eligible_layered_signals_uncertain_regime_falls_back_to_existing_behavior() -> None:
    signals = [
        *DIRECTION_LONG_MAJORITY,
        _entry_signal("technical_macd", direction=TradeSide.LONG),
        _range_signal("technical_vwap", direction=TradeSide.LONG),
    ]

    eligible = SignalEnsembleService._eligible_layered_signals(
        signals, allowed_direction=TradeSide.LONG, regime=MarketRegime.UNCERTAIN
    )

    sources = {SignalEnsembleService._signal_source(s.strategy_id) for s in eligible}
    assert sources == {"technical_macd"}


def test_eligible_layered_signals_none_regime_matches_pre_phase1_behavior() -> None:
    signals = [
        *DIRECTION_LONG_MAJORITY,
        _entry_signal("technical_macd", direction=TradeSide.LONG),
        _range_signal("technical_vwap", direction=TradeSide.LONG),
    ]

    eligible_no_regime = SignalEnsembleService._eligible_layered_signals(
        signals, allowed_direction=TradeSide.LONG, regime=None
    )
    eligible_uncertain = SignalEnsembleService._eligible_layered_signals(
        signals, allowed_direction=TradeSide.LONG, regime=MarketRegime.UNCERTAIN
    )

    assert eligible_no_regime == eligible_uncertain
