"""V2 decision funnel: every evaluated decision bar produces one terminal record.

Design rule (plan section 10.1 / Gate 6):
"Silence" is never an acceptable outcome. For every symbol and every closed
decision bar the runtime must be able to answer "why was no order opened?" with
a stable, machine-readable reason code plus the metrics that drove it.

This module provides:
- ``FunnelStage``: the ordered pipeline stages a decision passes through.
- ``StageOutcome``: PASSED / SKIPPED / REJECTED / ERROR.
- ``DecisionReasonCode``: stable reason codes safe to expose over the API and
  to aggregate on. Never format free text into these.
- ``DecisionFunnelBuilder``: mutable accumulator used during one evaluation.
- ``DecisionFunnelRecord``: the immutable result, ready for persistence.

The builder is deliberately separate from the record: the decision bar timestamp
is only known after the CANDLE_CLOSED stage, and the candidate id only after
CANDIDATE_CREATED, but the funnel must already be accumulating stages before
either is available.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping


class FunnelStage(StrEnum):
    """Ordered decision pipeline stages (plan section 10.1)."""

    CYCLE_STARTED = "CYCLE_STARTED"
    DATA_AVAILABLE = "DATA_AVAILABLE"
    CANDLE_CLOSED = "CANDLE_CLOSED"
    DATA_FRESH = "DATA_FRESH"
    TIMEFRAMES_ALIGNED = "TIMEFRAMES_ALIGNED"
    REGIME_EVALUATED = "REGIME_EVALUATED"
    ENTRY_SIGNAL_EVALUATED = "ENTRY_SIGNAL_EVALUATED"
    CANDIDATE_CREATED = "CANDIDATE_CREATED"
    META_LABEL_EVALUATED = "META_LABEL_EVALUATED"
    MANIFEST_EVALUATED = "MANIFEST_EVALUATED"
    RECONCILIATION_HEALTHY = "RECONCILIATION_HEALTHY"
    RISK_APPROVED = "RISK_APPROVED"
    AI_REVIEWED = "AI_REVIEWED"
    PRICE_DRIFT_APPROVED = "PRICE_DRIFT_APPROVED"
    INTENT_CREATED = "INTENT_CREATED"
    EXCHANGE_SUBMITTED = "EXCHANGE_SUBMITTED"
    EXCHANGE_FILLED = "EXCHANGE_FILLED"
    POSITION_PROJECTED = "POSITION_PROJECTED"
    PROTECTION_CONFIRMED = "PROTECTION_CONFIRMED"

    @property
    def order(self) -> int:
        """Position of this stage in the canonical pipeline order."""
        return _STAGE_ORDER[self]


_STAGE_ORDER: dict[FunnelStage, int] = {stage: index for index, stage in enumerate(FunnelStage)}


class StageOutcome(StrEnum):
    """Outcome of a single funnel stage.

    PASSED continues the pipeline. SKIPPED / REJECTED / ERROR are terminal:
    they seal the funnel so a later stage cannot silently overwrite the reason
    the decision actually stopped.
    """

    PASSED = "PASSED"
    SKIPPED = "SKIPPED"
    REJECTED = "REJECTED"
    ERROR = "ERROR"

    @property
    def is_terminal(self) -> bool:
        return self in {StageOutcome.SKIPPED, StageOutcome.REJECTED, StageOutcome.ERROR}


class DecisionReasonCode(StrEnum):
    """Stable reason codes. Safe to aggregate on and to expose over the API.

    Never synthesize these from free text: the frontend and the audit scripts
    group by exact value.
    """

    # --- Success / progress ---
    OK = "OK"
    CANDIDATE_READY = "CANDIDATE_READY"
    ENTRY_INTENT_CREATED = "ENTRY_INTENT_CREATED"

    # --- Data availability and freshness ---
    NO_MARKET_DATA = "NO_MARKET_DATA"
    NO_CLOSED_CANDLE = "NO_CLOSED_CANDLE"
    DUPLICATE_DECISION = "DUPLICATE_DECISION"
    MARKET_DATA_STALE = "MARKET_DATA_STALE"
    INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"

    # --- Timeframe / regime ---
    SINGLE_TIMEFRAME_LANE = "SINGLE_TIMEFRAME_LANE"
    FOUR_HOUR_DIRECTION_CONFLICT = "FOUR_HOUR_DIRECTION_CONFLICT"
    MULTI_TIMEFRAME_DISAGREEMENT = "MULTI_TIMEFRAME_DISAGREEMENT"
    ONE_HOUR_REGIME_RANGE = "ONE_HOUR_REGIME_RANGE"
    REGIME_NOT_ELIGIBLE = "REGIME_NOT_ELIGIBLE"

    # --- Entry signal evaluation ---
    NO_ENTRY_SIGNAL = "NO_ENTRY_SIGNAL"
    RSI_OUTSIDE_RANGE = "RSI_OUTSIDE_RANGE"
    MACD_DIRECTION_MISMATCH = "MACD_DIRECTION_MISMATCH"
    EMA_DIRECTION_MISMATCH = "EMA_DIRECTION_MISMATCH"
    ATR_NOT_POSITIVE = "ATR_NOT_POSITIVE"
    SIGNAL_CONFIDENCE_BELOW_THRESHOLD = "SIGNAL_CONFIDENCE_BELOW_THRESHOLD"

    # --- Candidate construction ---
    CANDIDATE_EXPIRED = "CANDIDATE_EXPIRED"
    CANDIDATE_CONSTRUCTION_FAILED = "CANDIDATE_CONSTRUCTION_FAILED"
    SYMBOL_NOT_IN_EXECUTION_UNIVERSE = "SYMBOL_NOT_IN_EXECUTION_UNIVERSE"

    # --- Meta-label / manifest / edge ---
    META_LABEL_BET_SKIPPED = "META_LABEL_BET_SKIPPED"
    META_LABEL_NOT_CONFIGURED = "META_LABEL_NOT_CONFIGURED"
    MANIFEST_NOT_ELIGIBLE = "MANIFEST_NOT_ELIGIBLE"
    MANIFEST_UNAVAILABLE = "MANIFEST_UNAVAILABLE"
    NO_AUTHORIZED_PRODUCTION_STRATEGY = "NO_AUTHORIZED_PRODUCTION_STRATEGY"
    NET_EDGE_AFTER_COST_NEGATIVE = "NET_EDGE_AFTER_COST_NEGATIVE"

    # --- Reconciliation / risk (entry-only gates) ---
    RECONCILIATION_UNAVAILABLE = "RECONCILIATION_UNAVAILABLE"
    RECONCILIATION_DEGRADED = "RECONCILIATION_DEGRADED"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"
    UNMANAGED_EXTERNAL_POSITION = "UNMANAGED_EXTERNAL_POSITION"
    RISK_LIMIT_EXCEEDED = "RISK_LIMIT_EXCEEDED"
    ENTRY_KILL_SWITCH_ACTIVE = "ENTRY_KILL_SWITCH_ACTIVE"
    POSITION_ALREADY_OPEN = "POSITION_ALREADY_OPEN"
    DAILY_TRADE_LIMIT_REACHED = "DAILY_TRADE_LIMIT_REACHED"
    SYMBOL_COOLDOWN_ACTIVE = "SYMBOL_COOLDOWN_ACTIVE"

    # --- AI advisory (never blocks hard exits) ---
    AI_PROVIDER_UNAVAILABLE = "AI_PROVIDER_UNAVAILABLE"
    AI_ADVISORY_VETO = "AI_ADVISORY_VETO"
    AI_REVIEW_DISABLED = "AI_REVIEW_DISABLED"

    # --- Pre-submit / exchange ---
    PRICE_DRIFT_EXCEEDED = "PRICE_DRIFT_EXCEEDED"
    SHADOW_MODE_NO_SUBMIT = "SHADOW_MODE_NO_SUBMIT"
    EXCHANGE_UNAVAILABLE = "EXCHANGE_UNAVAILABLE"
    EXCHANGE_REJECTED = "EXCHANGE_REJECTED"
    EXCHANGE_UNKNOWN = "EXCHANGE_UNKNOWN"
    PROTECTION_FAILED = "PROTECTION_FAILED"

    # --- Internal ---
    INTERNAL_ERROR = "INTERNAL_ERROR"


@dataclass(frozen=True)
class FunnelStageRecord:
    """One immutable stage evaluation.

    ``metrics`` holds the numeric inputs that drove the outcome (RSI value,
    MACD histogram, drift bps, ...). It is normalized to a sorted tuple of
    string pairs so the record stays hashable and serializes deterministically.
    """

    stage: FunnelStage
    outcome: StageOutcome
    reason_code: DecisionReasonCode = DecisionReasonCode.OK
    detail: str = ""
    metrics: tuple[tuple[str, str], ...] = ()
    recorded_at: datetime | None = None

    @property
    def is_terminal(self) -> bool:
        return self.outcome.is_terminal

    def to_payload(self) -> dict[str, Any]:
        return {
            "stage": self.stage.value,
            "outcome": self.outcome.value,
            "reason_code": self.reason_code.value,
            "detail": self.detail,
            "metrics": dict(self.metrics),
            "recorded_at": self.recorded_at.isoformat() if self.recorded_at else None,
        }


@dataclass(frozen=True)
class DecisionFunnelRecord:
    """Immutable, complete record of one (symbol, decision-bar) evaluation.

    This is what gets persisted and served over the Runtime Truth API. Every
    evaluated closed bar produces exactly one of these — including the boring
    "no signal" case, which is the whole point of the funnel.
    """

    funnel_id: str
    cycle_id: str
    symbol: str
    lane: str
    strategy_id: str
    strategy_version: str
    terminal_stage: FunnelStage
    reason_code: DecisionReasonCode
    stages: tuple[FunnelStageRecord, ...]
    bar_timestamp: datetime | None = None
    candidate_id: str | None = None
    created_at: datetime | None = None

    @property
    def terminal_outcome(self) -> StageOutcome | None:
        return self.stages[-1].outcome if self.stages else None

    @property
    def created_candidate(self) -> bool:
        """True only when evaluation produced a usable candidate."""
        return self.candidate_id is not None and self.reason_code is DecisionReasonCode.CANDIDATE_READY

    def stage_for(self, stage: FunnelStage) -> FunnelStageRecord | None:
        """Look up a recorded stage, or None when it was never reached."""
        for record in self.stages:
            if record.stage is stage:
                return record
        return None

    def to_payload(self) -> dict[str, Any]:
        """Serialize for persistence and the Runtime Truth API."""
        return {
            "funnel_id": self.funnel_id,
            "cycle_id": self.cycle_id,
            "symbol": self.symbol,
            "lane": self.lane,
            "strategy_id": self.strategy_id,
            "strategy_version": self.strategy_version,
            "bar_timestamp": self.bar_timestamp.isoformat() if self.bar_timestamp else None,
            "terminal_stage": self.terminal_stage.value,
            "terminal_outcome": self.terminal_outcome.value if self.terminal_outcome else None,
            "reason_code": self.reason_code.value,
            "candidate_id": self.candidate_id,
            "created_candidate": self.created_candidate,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "stages": [record.to_payload() for record in self.stages],
        }


class DecisionFunnelBuilder:
    """Mutable accumulator that produces one immutable ``DecisionFunnelRecord``.

    The builder exists because two facts are not known when evaluation starts:
    the decision bar timestamp (known only after CANDLE_CLOSED) and the
    candidate id (known only after CANDIDATE_CREATED). Callers set them via
    :meth:`set_decision_bar` and :meth:`set_candidate` as they become available.

    ``build()`` may be called exactly once. Recording after ``build()`` raises,
    which is what stops a half-evaluated funnel from being silently extended.
    """

    def __init__(
        self,
        *,
        cycle_id: str,
        symbol: str,
        lane: str,
        strategy_id: str,
        strategy_version: str,
        funnel_id: str | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.funnel_id = funnel_id or str(uuid.uuid4())
        self.cycle_id = cycle_id
        self.symbol = symbol
        self.lane = lane
        self.strategy_id = strategy_id
        self.strategy_version = strategy_version
        self._clock = clock or (lambda: datetime.now(UTC))
        self._stages: list[FunnelStageRecord] = []
        self._bar_timestamp: datetime | None = None
        self._candidate_id: str | None = None
        self._built = False
        self._created_at = self._clock()

    def record(
        self,
        stage: FunnelStage,
        outcome: StageOutcome,
        reason_code: DecisionReasonCode = DecisionReasonCode.OK,
        *,
        detail: str = "",
        metrics: Mapping[str, Any] | None = None,
    ) -> FunnelStageRecord:
        """Append one stage evaluation.

        Raises:
            RuntimeError: If the funnel has already been built.
        """
        if self._built:
            raise RuntimeError(f"DecisionFunnelBuilder {self.funnel_id} already built; cannot record {stage.value}")

        normalized = tuple(sorted((str(key), str(value)) for key, value in (metrics or {}).items()))
        record = FunnelStageRecord(
            stage=stage,
            outcome=outcome,
            reason_code=reason_code,
            detail=detail,
            metrics=normalized,
            recorded_at=self._clock(),
        )
        self._stages.append(record)
        return record

    def set_decision_bar(self, bar_timestamp: datetime) -> None:
        """Record which closed bar this evaluation belongs to."""
        self._bar_timestamp = bar_timestamp

    def set_candidate(self, candidate_id: str) -> None:
        """Record the candidate produced by this evaluation."""
        self._candidate_id = candidate_id

    @property
    def stages(self) -> tuple[FunnelStageRecord, ...]:
        return tuple(self._stages)

    def build(
        self,
        *,
        terminal_stage: FunnelStage,
        reason_code: DecisionReasonCode,
    ) -> DecisionFunnelRecord:
        """Seal the funnel into an immutable record.

        Raises:
            RuntimeError: If called more than once, or with no stages recorded.
        """
        if self._built:
            raise RuntimeError(f"DecisionFunnelBuilder {self.funnel_id} already built")
        if not self._stages:
            raise RuntimeError(
                f"DecisionFunnelBuilder {self.funnel_id} has no recorded stages; "
                "every evaluated bar must record at least CYCLE_STARTED"
            )
        self._built = True
        return DecisionFunnelRecord(
            funnel_id=self.funnel_id,
            cycle_id=self.cycle_id,
            symbol=self.symbol,
            lane=self.lane,
            strategy_id=self.strategy_id,
            strategy_version=self.strategy_version,
            terminal_stage=terminal_stage,
            reason_code=reason_code,
            stages=tuple(self._stages),
            bar_timestamp=self._bar_timestamp,
            candidate_id=self._candidate_id,
            created_at=self._created_at,
        )


def build_funnel_record(
    *,
    cycle_id: str,
    symbol: str,
    lane: str,
    strategy_id: str,
    strategy_version: str,
    funnel_id: str | None = None,
    clock: Callable[[], datetime] | None = None,
) -> DecisionFunnelBuilder:
    """Start a new decision funnel for one (symbol, decision-bar) evaluation."""
    return DecisionFunnelBuilder(
        cycle_id=cycle_id,
        symbol=symbol,
        lane=lane,
        strategy_id=strategy_id,
        strategy_version=strategy_version,
        funnel_id=funnel_id,
        clock=clock,
    )
