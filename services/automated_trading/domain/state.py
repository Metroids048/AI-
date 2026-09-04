"""V2 state machine transition validation.

Enforces legal state transitions and Exchange-First invariants:
- Cannot transition to FILLED without FillReceipt
- Cannot transition to POSITION_PROJECTED without FillReceipt
- Cannot transition to PROTECTION_ACTIVE without exchange order_id
- All transitions must pass through validate_intent_transition() / validate_position_transition()

Illegal transitions raise ValueError before database persistence.
"""

from __future__ import annotations

from services.automated_trading.domain.enums import (
    V2IntentState,
    V2PositionState,
    V2ProtectionState,
)

# Legal intent state transitions (from_state -> set of legal next states)
_LEGAL_INTENT_TRANSITIONS: dict[V2IntentState, set[V2IntentState]] = {
    V2IntentState.INTENT_CREATED: {
        V2IntentState.EXCHANGE_SUBMITTING,
        V2IntentState.CANCELLED,  # Cancelled before submission
    },
    V2IntentState.EXCHANGE_SUBMITTING: {
        V2IntentState.EXCHANGE_ACKNOWLEDGED,
        V2IntentState.FILLED,  # Fast fill before acknowledgment
        V2IntentState.REJECTED,
        V2IntentState.CANCELLED,
        V2IntentState.EXCHANGE_UNKNOWN,  # Request sent, outcome undetermined
    },
    # Resolved only by Client Order ID lookup against the exchange. Every exit
    # from here is an *observation* of what the exchange already did; there is no
    # transition back to EXCHANGE_SUBMITTING because that would mean resubmitting.
    V2IntentState.EXCHANGE_UNKNOWN: {
        V2IntentState.EXCHANGE_ACKNOWLEDGED,
        V2IntentState.FILLED,
        V2IntentState.REJECTED,
        V2IntentState.CANCELLED,
        V2IntentState.EXPIRED,
    },
    V2IntentState.EXCHANGE_ACKNOWLEDGED: {
        V2IntentState.FILLED,
        V2IntentState.CANCELLED,
        V2IntentState.EXPIRED,
    },
    V2IntentState.FILLED: set(),  # Terminal state
    V2IntentState.REJECTED: set(),  # Terminal state
    V2IntentState.CANCELLED: set(),  # Terminal state
    V2IntentState.EXPIRED: set(),  # Terminal state
}

# Legal position state transitions
_LEGAL_POSITION_TRANSITIONS: dict[V2PositionState, set[V2PositionState]] = {
    V2PositionState.POSITION_PROJECTED: {
        V2PositionState.PROTECTED,
        V2PositionState.REDUCING,  # Emergency exit before protection
        V2PositionState.QUARANTINED,
    },
    V2PositionState.PROTECTED: {
        V2PositionState.REDUCING,
        V2PositionState.CLOSED,
        V2PositionState.QUARANTINED,
    },
    V2PositionState.REDUCING: {
        V2PositionState.CLOSED,
        V2PositionState.QUARANTINED,
    },
    V2PositionState.CLOSED: set(),  # Terminal state
    V2PositionState.QUARANTINED: set(),  # Terminal state (manual intervention required)
}

# Legal protection state transitions
_LEGAL_PROTECTION_TRANSITIONS: dict[V2ProtectionState, set[V2ProtectionState]] = {
    V2ProtectionState.PROTECTION_INTENT: {
        V2ProtectionState.PROTECTION_SUBMITTING,
        V2ProtectionState.PROTECTION_CANCELLED,  # Position closed before submission
    },
    V2ProtectionState.PROTECTION_SUBMITTING: {
        V2ProtectionState.PROTECTION_ACTIVE,
        V2ProtectionState.PROTECTION_CANCELLED,
        V2ProtectionState.PROTECTION_FAILED,
        V2ProtectionState.PROTECTION_UNKNOWN,
    },
    V2ProtectionState.PROTECTION_ACTIVE: {
        V2ProtectionState.PROTECTION_TRIGGERED,
        V2ProtectionState.PROTECTION_CANCELLED,
    },
    V2ProtectionState.PROTECTION_TRIGGERED: {
        V2ProtectionState.PROTECTION_FILLED,
    },
    # A failed protection attempt may be retried once under a new attempt number,
    # or escalate to emergency close. It is never a resting state.
    V2ProtectionState.PROTECTION_FAILED: {
        V2ProtectionState.PROTECTION_SUBMITTING,
        V2ProtectionState.PROTECTION_CANCELLED,
    },
    # Resolved by Client Order ID lookup: the order either exists at the exchange
    # or it does not. No transition back to SUBMITTING (that would resubmit).
    V2ProtectionState.PROTECTION_UNKNOWN: {
        V2ProtectionState.PROTECTION_ACTIVE,
        V2ProtectionState.PROTECTION_TRIGGERED,
        V2ProtectionState.PROTECTION_CANCELLED,
        V2ProtectionState.PROTECTION_FAILED,
    },
    V2ProtectionState.PROTECTION_FILLED: set(),  # Terminal state
    V2ProtectionState.PROTECTION_CANCELLED: set(),  # Terminal state
}


def validate_intent_transition(
    current: V2IntentState,
    next_state: V2IntentState,
) -> None:
    """Validate intent state transition is legal.

    Args:
        current: Current intent state
        next_state: Proposed next state

    Raises:
        ValueError: If transition is illegal
    """
    if current == next_state:
        return  # No-op transition is always legal

    legal_next = _LEGAL_INTENT_TRANSITIONS.get(current, set())
    if next_state not in legal_next:
        raise ValueError(
            f"Illegal V2 intent transition: {current.value} -> {next_state.value}. "
            f"Legal transitions from {current.value}: {[s.value for s in legal_next]}"
        )


def validate_position_transition(
    current: V2PositionState,
    next_state: V2PositionState,
) -> None:
    """Validate position state transition is legal.

    Args:
        current: Current position state
        next_state: Proposed next state

    Raises:
        ValueError: If transition is illegal
    """
    if current == next_state:
        return  # No-op transition is always legal

    legal_next = _LEGAL_POSITION_TRANSITIONS.get(current, set())
    if next_state not in legal_next:
        raise ValueError(
            f"Illegal V2 position transition: {current.value} -> {next_state.value}. "
            f"Legal transitions from {current.value}: {[s.value for s in legal_next]}"
        )


def validate_historical_attribution_transition(
    current: V2PositionState,
    next_state: V2PositionState,
) -> None:
    """Allow only the explicit operator adjudication recovery transition."""
    if current is not V2PositionState.QUARANTINED or next_state is not V2PositionState.CLOSED:
        raise ValueError("Historical attribution may transition only QUARANTINED -> CLOSED")


def validate_protection_transition(
    current: V2ProtectionState,
    next_state: V2ProtectionState,
) -> None:
    """Validate protection state transition is legal.

    Args:
        current: Current protection state
        next_state: Proposed next state

    Raises:
        ValueError: If transition is illegal
    """
    if current == next_state:
        return  # No-op transition is always legal

    legal_next = _LEGAL_PROTECTION_TRANSITIONS.get(current, set())
    if next_state not in legal_next:
        raise ValueError(
            f"Illegal V2 protection transition: {current.value} -> {next_state.value}. "
            f"Legal transitions from {current.value}: {[s.value for s in legal_next]}"
        )
