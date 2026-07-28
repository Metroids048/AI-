"""Testnet Sampling V2 strategy (plan section 10.2).

Non-promotable sampling strategy for the TESTNET_SAMPLING lane. Its sole
purpose is to produce a steady stream of V2 Entry->Protection->Exit cycles so
the execution chain stays exercised even when production strategies are silent.

Rules (plan 10.2):
    LONG:  close > EMA50, MACD histogram > 0, RSI in [50, 72], ATR14 > 0
    SHORT: close < EMA50, MACD histogram < 0, RSI in [28, 50], ATR14 > 0
    stop_distance        = max(1.2 * ATR14, fill_price * 0.0035)
    take_profit_distance = 1.5 * stop_distance

All candidates produced here are:
- lane = TESTNET_SAMPLING
- candidate_type = SAMPLING
- non_promotable = True

They must never enter strategy promotion evidence.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from decimal import Decimal

from services.automated_trading.application.decision_service import (
    TimeframeView,
    evaluate_sampling_signal,
    sampling_stop_distance,
)
from services.automated_trading.domain.candidates import (
    CandidateLane,
    TradeCandidate,
)
from services.automated_trading.domain.enums import V2CandidateType

STRATEGY_ID = "testnet_sampling_v2"
STRATEGY_VERSION = "2.0.0"
MAX_ENTRY_DRIFT_BPS = Decimal("20")
CANDIDATE_TTL_SECONDS = 75


def generate_sampling_candidate(
    cycle_id: str,
    symbol: str,
    entry_timeframe: TimeframeView,
    now: datetime,
    *,
    max_entry_drift_bps: Decimal = MAX_ENTRY_DRIFT_BPS,
    candidate_ttl_seconds: int = CANDIDATE_TTL_SECONDS,
) -> TradeCandidate | None:
    """Evaluate closed bars and return a sampling candidate, or None.

    Returns None when signal conditions are not met or data is insufficient.
    Callers must still check sampling throttle (SamplingState) before submitting.

    Args:
        cycle_id: Owning cycle identifier.
        symbol: Trading symbol (must be in EXECUTION_UNIVERSE).
        entry_timeframe: Closed bars for the 15m evaluation timeframe.
        now: Current UTC timestamp (used for expiry).
        max_entry_drift_bps: Maximum tolerated price drift before submission.
        candidate_ttl_seconds: Seconds until the candidate expires.

    Returns:
        TradeCandidate if signal qualifies, else None.
    """
    if not entry_timeframe.bars:
        return None

    last_bar = entry_timeframe.last_closed
    if last_bar is None:
        return None

    evaluation = evaluate_sampling_signal(entry_timeframe)
    if evaluation.side is None:
        return None

    # ATR is needed for stop distance; the signal eval already checked it,
    # but we need the raw value for the stop formula.
    from services.automated_trading.application.decision_service import atr

    atr14 = atr(entry_timeframe.bars, 14)
    if atr14 is None or atr14 <= 0:
        return None

    reference_price = last_bar.close
    stop_dist = sampling_stop_distance(atr14=atr14, reference_price=reference_price)
    take_profit_dist = Decimal("1.5") * stop_dist

    return TradeCandidate(
        candidate_id=str(uuid.uuid4()),
        cycle_id=cycle_id,
        strategy_id=STRATEGY_ID,
        strategy_version=STRATEGY_VERSION,
        lane=CandidateLane.TESTNET_SAMPLING,
        candidate_type=V2CandidateType.SAMPLING,
        symbol=symbol,
        side=evaluation.side,
        signal_candle_close_time=last_bar.timestamp,
        signal_reference_price=reference_price,
        confidence=evaluation.confidence,
        stop_distance=stop_dist,
        take_profit_distance=take_profit_dist,
        max_entry_drift_bps=max_entry_drift_bps,
        expires_at=now + timedelta(seconds=candidate_ttl_seconds),
        non_promotable=True,
        signal_context=(
            ("strategy", STRATEGY_ID),
            ("lane", CandidateLane.TESTNET_SAMPLING),
        ),
    )
