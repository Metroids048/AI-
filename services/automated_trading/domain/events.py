"""V2 domain events: immutable records of state transitions.

Events record what happened in the state machine. They are the result
of applying a command to the current state. Events drive state transitions
and produce the immutable audit trail.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from services.automated_trading.domain.enums import (
    V2CandidateType,
    V2ExecutionMode,
    V2IntentState,
    V2PositionState,
)
from services.automated_trading.domain.receipts import (
    FillReceipt,
    ProtectionReceipt,
    ReduceReceipt,
)


@dataclass(frozen=True)
class IntentCreatedEvent:
    """Entry intent created from strategy decision."""

    intent_id: str
    symbol: str
    direction: str
    candidate_key: str
    candidate_type: V2CandidateType
    execution_mode: V2ExecutionMode
    decision_bar_timestamp: datetime
    decision_funnel_id: str | None
    fencing_token: str
    created_at: datetime


@dataclass(frozen=True)
class IntentSubmittedEvent:
    """Entry order submitted to exchange."""

    intent_id: str
    client_order_id: str
    quantity: Decimal
    leverage: int
    submitted_at: datetime
    new_state: V2IntentState = V2IntentState.EXCHANGE_SUBMITTING


@dataclass(frozen=True)
class IntentAcknowledgedEvent:
    """Exchange acknowledged order receipt."""

    intent_id: str
    exchange_order_id: str
    acknowledged_at: datetime
    new_state: V2IntentState = V2IntentState.EXCHANGE_ACKNOWLEDGED


@dataclass(frozen=True)
class IntentFilledEvent:
    """Exchange fill confirmed. Requires FillReceipt."""

    intent_id: str
    fill_receipt: FillReceipt
    filled_at: datetime
    new_state: V2IntentState = V2IntentState.FILLED


@dataclass(frozen=True)
class IntentRejectedEvent:
    """Exchange rejected the order."""

    intent_id: str
    rejection_reason: str
    rejected_at: datetime
    new_state: V2IntentState = V2IntentState.REJECTED


@dataclass(frozen=True)
class IntentCancelledEvent:
    """Order cancelled before fill."""

    intent_id: str
    cancel_reason: str
    cancelled_at: datetime
    new_state: V2IntentState = V2IntentState.CANCELLED


@dataclass(frozen=True)
class PositionProjectedEvent:
    """Local position projected from exchange fill. Requires FillReceipt."""

    position_id: str
    intent_id: str
    fill_receipt: FillReceipt  # Must have valid fill receipt
    projected_at: datetime
    new_state: V2PositionState = V2PositionState.POSITION_PROJECTED


@dataclass(frozen=True)
class PositionProtectedEvent:
    """Protection orders submitted and confirmed by exchange. Requires ProtectionReceipt."""

    position_id: str
    protection_receipt: ProtectionReceipt  # Must have exchange order IDs
    protected_at: datetime
    new_state: V2PositionState = V2PositionState.PROTECTED


@dataclass(frozen=True)
class PositionReducingEvent:
    """Reduce-only exit order submitted."""

    position_id: str
    exit_reason: str
    reduce_quantity: Decimal
    is_emergency: bool
    reducing_at: datetime
    new_state: V2PositionState = V2PositionState.REDUCING


@dataclass(frozen=True)
class PositionClosedEvent:
    """Position fully closed. Requires ReduceReceipt."""

    position_id: str
    reduce_receipt: ReduceReceipt
    realized_pnl: Decimal | None  # None until fully reconciled
    closed_at: datetime
    new_state: V2PositionState = V2PositionState.CLOSED


@dataclass(frozen=True)
class PositionQuarantinedEvent:
    """Position quarantined due to irrecoverable state."""

    position_id: str
    quarantine_reason: str
    quarantined_at: datetime
    new_state: V2PositionState = V2PositionState.QUARANTINED
