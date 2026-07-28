"""V2 decision service: pure strategy evaluation producing observable outcomes.

Design constraints (plan Task 6 / Gate 6):
- This module is PURE decision logic. It never touches exchange state, never
  submits orders, never creates positions, and never imports the legacy
  ``paper_cycle_orchestrator`` / ``paper_order_lifecycle`` modules.
- Every evaluated closed decision bar produces exactly one terminal
  ``DecisionOutcome`` with a funnel record. Silence is never allowed.
- Candidates only carry *relative* risk distances. Absolute protection prices
  are computed later from the real ``average_fill_price`` by the entry/protection
  services (plan section 3.4).

The decision flow mirrors plan section 10.1's funnel stages:

    CYCLE_STARTED -> DATA_AVAILABLE -> CANDLE_CLOSED -> DATA_FRESH
      -> TIMEFRAMES_ALIGNED -> REGIME_EVALUATED -> ENTRY_SIGNAL_EVALUATED
      -> CANDIDATE_CREATED -> META_LABEL_EVALUATED
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from services.automated_trading.domain.candidates import (
    CandidateLane,
    CandidateSide,
    TradeCandidate,
)
from services.automated_trading.domain.enums import V2CandidateType
from services.automated_trading.observability.decision_funnel import (
    DecisionFunnelRecord,
    DecisionReasonCode,
    FunnelStage,
    StageOutcome,
    build_funnel_record,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence


DEFAULT_MAX_ENTRY_DRIFT_BPS = Decimal("20")
DEFAULT_CANDIDATE_TTL_SECONDS = 75


@dataclass(frozen=True)
class BarView:
    """Minimal immutable OHLCV bar view consumed by the decision service.

    Using an explicit view keeps the decision layer free of pandas coupling and
    makes every test deterministic.
    """

    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


@dataclass(frozen=True)
class TimeframeView:
    """Closed bars for a single timeframe, oldest first."""

    timeframe: str
    bars: tuple[BarView, ...]

    @property
    def last_closed(self) -> BarView | None:
        return self.bars[-1] if self.bars else None


@dataclass(frozen=True)
class SignalEvaluation:
    """Result of an entry-signal evaluation for one side."""

    side: CandidateSide | None
    confidence: Decimal
    reason_code: DecisionReasonCode | None
    metrics: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DecisionContext:
    """Everything the decision service needs for one (symbol, bar) evaluation.

    ``already_evaluated_bars`` lets the caller pass the set of decision-bar
    timestamps already recorded for this symbol/lane so duplicates terminate
    with ``DUPLICATE_DECISION`` instead of silently returning.
    """

    cycle_id: str
    symbol: str
    lane: CandidateLane
    strategy_id: str
    strategy_version: str
    entry_timeframe: TimeframeView
    direction_timeframe: TimeframeView | None = None
    state_timeframe: TimeframeView | None = None
    now: datetime = field(default_factory=lambda: datetime.now(UTC))
    data_stale_after_seconds: int = 120
    already_evaluated_bars: frozenset[datetime] = frozenset()
    max_entry_drift_bps: Decimal = DEFAULT_MAX_ENTRY_DRIFT_BPS
    candidate_ttl_seconds: int = DEFAULT_CANDIDATE_TTL_SECONDS
    meta_label_min_win_rate: Decimal | None = None
    meta_label_predictor: Callable[[TradeCandidate], Decimal] | None = None
    manifest_eligible: bool = True
    manifest_reason: DecisionReasonCode | None = None


@dataclass(frozen=True)
class DecisionOutcome:
    """Terminal result of one decision-bar evaluation.

    ``candidate`` is non-None only when the funnel reached
    ``CANDIDATE_CREATED`` (and passed meta-label/manifest gates).
    """

    cycle_id: str
    symbol: str
    lane: CandidateLane
    terminal_stage: FunnelStage
    reason_code: DecisionReasonCode
    funnel: DecisionFunnelRecord
    candidate: TradeCandidate | None = None

    @property
    def has_candidate(self) -> bool:
        return self.candidate is not None


# --------------------------------------------------------------------------
# Indicator helpers (pure, Decimal-safe, no pandas dependency)
# --------------------------------------------------------------------------


def _closes(bars: Sequence[BarView]) -> list[Decimal]:
    return [bar.close for bar in bars]


def ema(values: Sequence[Decimal], period: int) -> Decimal | None:
    """Exponential moving average over the trailing window."""
    if len(values) < period:
        return None
    multiplier = Decimal(2) / Decimal(period + 1)
    seed_window = values[:period]
    current = sum(seed_window, Decimal(0)) / Decimal(period)
    for value in values[period:]:
        current = (value - current) * multiplier + current
    return current


def rsi(values: Sequence[Decimal], period: int = 14) -> Decimal | None:
    """Wilder RSI over the trailing window."""
    if len(values) < period + 1:
        return None
    gains = Decimal(0)
    losses = Decimal(0)
    for previous, current in zip(values[-period - 1 : -1], values[-period:], strict=True):
        delta = current - previous
        if delta >= 0:
            gains += delta
        else:
            losses += -delta
    if losses == 0:
        return Decimal(100)
    avg_gain = gains / Decimal(period)
    avg_loss = losses / Decimal(period)
    rs = avg_gain / avg_loss
    return Decimal(100) - (Decimal(100) / (Decimal(1) + rs))


def macd_histogram(
    values: Sequence[Decimal],
    *,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> Decimal | None:
    """MACD histogram (macd line minus signal line) for the latest bar."""
    if len(values) < slow + signal:
        return None
    macd_series: list[Decimal] = []
    for end in range(slow, len(values) + 1):
        window = values[:end]
        fast_ema = ema(window, fast)
        slow_ema = ema(window, slow)
        if fast_ema is None or slow_ema is None:
            return None
        macd_series.append(fast_ema - slow_ema)
    signal_line = ema(macd_series, signal)
    if signal_line is None:
        return None
    return macd_series[-1] - signal_line


def atr(bars: Sequence[BarView], period: int = 14) -> Decimal | None:
    """Average true range over the trailing window."""
    if len(bars) < period + 1:
        return None
    true_ranges: list[Decimal] = []
    for previous, current in zip(bars[-period - 1 : -1], bars[-period:], strict=True):
        high_low = current.high - current.low
        high_close = abs(current.high - previous.close)
        low_close = abs(current.low - previous.close)
        true_ranges.append(max(high_low, high_close, low_close))
    return sum(true_ranges, Decimal(0)) / Decimal(period)


# --------------------------------------------------------------------------
# Sampling-lane signal rules (plan section 10.2)
# --------------------------------------------------------------------------


def evaluate_sampling_signal(entry: TimeframeView) -> SignalEvaluation:
    """Evaluate the Testnet sampling lane entry rules.

    LONG:  close > EMA50, MACD histogram > 0, RSI in [50, 72], ATR14 > 0
    SHORT: close < EMA50, MACD histogram < 0, RSI in [28, 50], ATR14 > 0
    """
    bars = entry.bars
    last = entry.last_closed
    if last is None:
        return SignalEvaluation(
            side=None,
            confidence=Decimal(0),
            reason_code=DecisionReasonCode.NO_ENTRY_SIGNAL,
        )

    closes = _closes(bars)
    ema50 = ema(closes, 50)
    hist = macd_histogram(closes)
    rsi14 = rsi(closes, 14)
    atr14 = atr(bars, 14)

    metrics: dict[str, Any] = {
        "close": str(last.close),
        "ema50": None if ema50 is None else str(ema50),
        "macd_histogram": None if hist is None else str(hist),
        "rsi14": None if rsi14 is None else str(rsi14),
        "atr14": None if atr14 is None else str(atr14),
    }

    if ema50 is None or hist is None or rsi14 is None or atr14 is None:
        return SignalEvaluation(
            side=None,
            confidence=Decimal(0),
            reason_code=DecisionReasonCode.INSUFFICIENT_HISTORY,
            metrics=metrics,
        )

    if atr14 <= 0:
        return SignalEvaluation(
            side=None,
            confidence=Decimal(0),
            reason_code=DecisionReasonCode.ATR_NOT_POSITIVE,
            metrics=metrics,
        )

    long_ok = last.close > ema50 and hist > 0 and Decimal(50) <= rsi14 <= Decimal(72)
    short_ok = last.close < ema50 and hist < 0 and Decimal(28) <= rsi14 <= Decimal(50)

    if long_ok:
        return SignalEvaluation(
            side=CandidateSide.LONG,
            confidence=Decimal("0.55"),
            reason_code=None,
            metrics=metrics,
        )
    if short_ok:
        return SignalEvaluation(
            side=CandidateSide.SHORT,
            confidence=Decimal("0.55"),
            reason_code=None,
            metrics=metrics,
        )

    # Distinguish *why* no side triggered so operators can read the funnel.
    if (last.close > ema50 and hist <= 0) or (last.close < ema50 and hist >= 0):
        reason = DecisionReasonCode.MACD_DIRECTION_MISMATCH
    elif Decimal(72) < rsi14 or rsi14 < Decimal(28):
        reason = DecisionReasonCode.RSI_OUTSIDE_RANGE
    else:
        reason = DecisionReasonCode.NO_ENTRY_SIGNAL

    return SignalEvaluation(side=None, confidence=Decimal(0), reason_code=reason, metrics=metrics)


def sampling_stop_distance(*, atr14: Decimal, reference_price: Decimal) -> Decimal:
    """stop_distance = max(1.2 * ATR14, reference_price * 0.0035)."""
    return max(Decimal("1.2") * atr14, reference_price * Decimal("0.0035"))


# --------------------------------------------------------------------------
# Decision service
# --------------------------------------------------------------------------


def _candidate_type_for_lane(lane: CandidateLane) -> V2CandidateType:
    """Map a lane to its promotion semantics.

    TESTNET_SAMPLING candidates are SAMPLING, which the ``TradeCandidate``
    invariants then force to be ``non_promotable``. This is what keeps sampling
    fills out of strategy promotion evidence (plan section 10.2).
    """
    if lane is CandidateLane.TESTNET_SAMPLING:
        return V2CandidateType.SAMPLING
    return V2CandidateType.PRIMARY


def _terminate(
    context: DecisionContext,
    builder: Any,
    stage: FunnelStage,
    reason: DecisionReasonCode,
    *,
    outcome: StageOutcome = StageOutcome.REJECTED,
    metrics: Mapping[str, Any] | None = None,
) -> DecisionOutcome:
    builder.record(stage, outcome, reason_code=reason, metrics=metrics)
    return DecisionOutcome(
        cycle_id=context.cycle_id,
        symbol=context.symbol,
        lane=context.lane,
        terminal_stage=stage,
        reason_code=reason,
        funnel=builder.build(terminal_stage=stage, reason_code=reason),
        candidate=None,
    )


def evaluate_symbol(context: DecisionContext) -> DecisionOutcome:
    """Evaluate one symbol for one closed decision bar.

    Always returns a terminal outcome with a complete funnel record.
    """
    builder = build_funnel_record(
        cycle_id=context.cycle_id,
        symbol=context.symbol,
        lane=context.lane.value,
        strategy_id=context.strategy_id,
        strategy_version=context.strategy_version,
    )

    builder.record(FunnelStage.CYCLE_STARTED, StageOutcome.PASSED)

    # --- DATA_AVAILABLE ---
    if not context.entry_timeframe.bars:
        return _terminate(
            context,
            builder,
            FunnelStage.DATA_AVAILABLE,
            DecisionReasonCode.NO_MARKET_DATA,
        )
    builder.record(
        FunnelStage.DATA_AVAILABLE,
        StageOutcome.PASSED,
        metrics={"entry_bar_count": len(context.entry_timeframe.bars)},
    )

    last_bar = context.entry_timeframe.last_closed
    assert last_bar is not None  # guarded above
    bar_timestamp = last_bar.timestamp
    builder.set_decision_bar(bar_timestamp)

    # --- CANDLE_CLOSED (duplicate detection) ---
    if bar_timestamp in context.already_evaluated_bars:
        return _terminate(
            context,
            builder,
            FunnelStage.CANDLE_CLOSED,
            DecisionReasonCode.DUPLICATE_DECISION,
            outcome=StageOutcome.SKIPPED,
            metrics={"bar_timestamp": bar_timestamp.isoformat()},
        )
    builder.record(
        FunnelStage.CANDLE_CLOSED,
        StageOutcome.PASSED,
        metrics={"bar_timestamp": bar_timestamp.isoformat()},
    )

    # --- DATA_FRESH ---
    age_seconds = (context.now - bar_timestamp).total_seconds()
    if age_seconds > context.data_stale_after_seconds:
        return _terminate(
            context,
            builder,
            FunnelStage.DATA_FRESH,
            DecisionReasonCode.MARKET_DATA_STALE,
            metrics={
                "bar_age_seconds": age_seconds,
                "stale_after_seconds": context.data_stale_after_seconds,
            },
        )
    builder.record(
        FunnelStage.DATA_FRESH,
        StageOutcome.PASSED,
        metrics={"bar_age_seconds": age_seconds},
    )

    # --- TIMEFRAMES_ALIGNED ---
    if context.direction_timeframe is not None:
        direction_last = context.direction_timeframe.last_closed
        if direction_last is None:
            return _terminate(
                context,
                builder,
                FunnelStage.TIMEFRAMES_ALIGNED,
                DecisionReasonCode.NO_MARKET_DATA,
                metrics={"missing_timeframe": context.direction_timeframe.timeframe},
            )
        builder.record(
            FunnelStage.TIMEFRAMES_ALIGNED,
            StageOutcome.PASSED,
            metrics={"direction_timeframe": context.direction_timeframe.timeframe},
        )
    else:
        builder.record(
            FunnelStage.TIMEFRAMES_ALIGNED,
            StageOutcome.SKIPPED,
            reason_code=DecisionReasonCode.SINGLE_TIMEFRAME_LANE,
        )

    # --- REGIME_EVALUATED ---
    atr14 = atr(context.entry_timeframe.bars, 14)
    if atr14 is None:
        return _terminate(
            context,
            builder,
            FunnelStage.REGIME_EVALUATED,
            DecisionReasonCode.INSUFFICIENT_HISTORY,
            metrics={"entry_bar_count": len(context.entry_timeframe.bars)},
        )
    if atr14 <= 0:
        return _terminate(
            context,
            builder,
            FunnelStage.REGIME_EVALUATED,
            DecisionReasonCode.ATR_NOT_POSITIVE,
            metrics={"atr14": str(atr14)},
        )
    builder.record(
        FunnelStage.REGIME_EVALUATED,
        StageOutcome.PASSED,
        metrics={"atr14": str(atr14)},
    )

    # --- ENTRY_SIGNAL_EVALUATED ---
    evaluation = evaluate_sampling_signal(context.entry_timeframe)
    if evaluation.side is None:
        reason = evaluation.reason_code or DecisionReasonCode.NO_ENTRY_SIGNAL
        return _terminate(
            context,
            builder,
            FunnelStage.ENTRY_SIGNAL_EVALUATED,
            reason,
            metrics=evaluation.metrics,
        )
    builder.record(
        FunnelStage.ENTRY_SIGNAL_EVALUATED,
        StageOutcome.PASSED,
        metrics={**dict(evaluation.metrics), "side": evaluation.side.value},
    )

    # --- CANDIDATE_CREATED ---
    reference_price = last_bar.close
    stop_distance = sampling_stop_distance(atr14=atr14, reference_price=reference_price)
    take_profit_distance = Decimal("1.5") * stop_distance
    expires_at = context.now + timedelta(seconds=context.candidate_ttl_seconds)

    candidate = TradeCandidate(
        candidate_id=str(uuid.uuid4()),
        cycle_id=context.cycle_id,
        strategy_id=context.strategy_id,
        strategy_version=context.strategy_version,
        lane=context.lane,
        candidate_type=_candidate_type_for_lane(context.lane),
        symbol=context.symbol,
        side=evaluation.side,
        signal_candle_close_time=bar_timestamp,
        signal_reference_price=reference_price,
        confidence=evaluation.confidence,
        stop_distance=stop_distance,
        take_profit_distance=take_profit_distance,
        max_entry_drift_bps=context.max_entry_drift_bps,
        expires_at=expires_at,
        non_promotable=context.lane is CandidateLane.TESTNET_SAMPLING,
    )
    builder.set_candidate(candidate.candidate_id)
    builder.record(
        FunnelStage.CANDIDATE_CREATED,
        StageOutcome.PASSED,
        metrics={
            "candidate_id": candidate.candidate_id,
            "side": str(candidate.side),
            "stop_distance": str(candidate.stop_distance),
            "take_profit_distance": str(candidate.take_profit_distance),
            "non_promotable": candidate.non_promotable,
        },
    )

    # --- META_LABEL_EVALUATED ---
    if context.meta_label_predictor is not None and context.meta_label_min_win_rate is not None:
        predicted = context.meta_label_predictor(candidate)
        if predicted < context.meta_label_min_win_rate:
            return _terminate(
                context,
                builder,
                FunnelStage.META_LABEL_EVALUATED,
                DecisionReasonCode.META_LABEL_BET_SKIPPED,
                metrics={
                    "predicted_win_rate": str(predicted),
                    "min_win_rate": str(context.meta_label_min_win_rate),
                },
            )
        builder.record(
            FunnelStage.META_LABEL_EVALUATED,
            StageOutcome.PASSED,
            metrics={"predicted_win_rate": str(predicted)},
        )
    else:
        builder.record(
            FunnelStage.META_LABEL_EVALUATED,
            StageOutcome.SKIPPED,
            reason_code=DecisionReasonCode.META_LABEL_NOT_CONFIGURED,
        )

    # --- MANIFEST_EVALUATED ---
    if not context.manifest_eligible:
        return _terminate(
            context,
            builder,
            FunnelStage.MANIFEST_EVALUATED,
            context.manifest_reason or DecisionReasonCode.MANIFEST_NOT_ELIGIBLE,
        )
    builder.record(FunnelStage.MANIFEST_EVALUATED, StageOutcome.PASSED)

    return DecisionOutcome(
        cycle_id=context.cycle_id,
        symbol=context.symbol,
        lane=context.lane,
        terminal_stage=FunnelStage.MANIFEST_EVALUATED,
        reason_code=DecisionReasonCode.CANDIDATE_READY,
        funnel=builder.build(
            terminal_stage=FunnelStage.MANIFEST_EVALUATED,
            reason_code=DecisionReasonCode.CANDIDATE_READY,
        ),
        candidate=candidate,
    )
