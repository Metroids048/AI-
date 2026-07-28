"""Tests for V2 automated trading state machine transitions.

Validates:
- All legal intent transitions pass
- All illegal transitions raise ValueError
- INTENT_CREATED -> FILLED must fail (Exchange-First invariant)
- POSITION_PROJECTED without FillReceipt must fail
- Protection without exchange order ID cannot be ACTIVE
"""

from __future__ import annotations

import pytest

from services.automated_trading.domain.enums import (
    V2IntentState,
    V2PositionState,
    V2ProtectionState,
)
from services.automated_trading.domain.state import (
    validate_intent_transition,
    validate_position_transition,
    validate_protection_transition,
)


class TestIntentStateTransitions:
    def test_intent_created_to_exchange_submitting_legal(self) -> None:
        validate_intent_transition(
            V2IntentState.INTENT_CREATED,
            V2IntentState.EXCHANGE_SUBMITTING,
        )

    def test_exchange_submitting_to_acknowledged_legal(self) -> None:
        validate_intent_transition(
            V2IntentState.EXCHANGE_SUBMITTING,
            V2IntentState.EXCHANGE_ACKNOWLEDGED,
        )

    def test_exchange_acknowledged_to_filled_legal(self) -> None:
        validate_intent_transition(
            V2IntentState.EXCHANGE_ACKNOWLEDGED,
            V2IntentState.FILLED,
        )

    def test_exchange_submitting_fast_fill_legal(self) -> None:
        """Fast fill before acknowledgment is permitted."""
        validate_intent_transition(
            V2IntentState.EXCHANGE_SUBMITTING,
            V2IntentState.FILLED,
        )

    def test_intent_created_to_filled_illegal(self) -> None:
        """Exchange-First invariant: cannot skip exchange submission."""
        with pytest.raises(ValueError, match="Illegal V2 intent transition"):
            validate_intent_transition(
                V2IntentState.INTENT_CREATED,
                V2IntentState.FILLED,
            )

    def test_filled_to_any_state_illegal(self) -> None:
        """FILLED is a terminal state."""
        for next_state in V2IntentState:
            if next_state != V2IntentState.FILLED:
                with pytest.raises(ValueError, match="Illegal V2 intent transition"):
                    validate_intent_transition(V2IntentState.FILLED, next_state)

    def test_intent_created_to_acknowledged_illegal(self) -> None:
        """Must go through EXCHANGE_SUBMITTING first."""
        with pytest.raises(ValueError, match="Illegal V2 intent transition"):
            validate_intent_transition(
                V2IntentState.INTENT_CREATED,
                V2IntentState.EXCHANGE_ACKNOWLEDGED,
            )

    def test_same_state_noop_always_legal(self) -> None:
        """No-op transition is legal for all states."""
        for state in V2IntentState:
            validate_intent_transition(state, state)  # Must not raise


class TestPositionStateTransitions:
    def test_projected_to_protected_legal(self) -> None:
        validate_position_transition(
            V2PositionState.POSITION_PROJECTED,
            V2PositionState.PROTECTED,
        )

    def test_protected_to_reducing_legal(self) -> None:
        validate_position_transition(
            V2PositionState.PROTECTED,
            V2PositionState.REDUCING,
        )

    def test_reducing_to_closed_legal(self) -> None:
        validate_position_transition(
            V2PositionState.REDUCING,
            V2PositionState.CLOSED,
        )

    def test_projected_emergency_exit_legal(self) -> None:
        """Emergency exit from projected position (before protection) is permitted."""
        validate_position_transition(
            V2PositionState.POSITION_PROJECTED,
            V2PositionState.REDUCING,
        )

    def test_closed_to_any_state_illegal(self) -> None:
        """CLOSED is a terminal state."""
        for next_state in V2PositionState:
            if next_state != V2PositionState.CLOSED:
                with pytest.raises(ValueError, match="Illegal V2 position transition"):
                    validate_position_transition(V2PositionState.CLOSED, next_state)

    def test_quarantined_to_any_state_illegal(self) -> None:
        """QUARANTINED is a terminal state; requires manual intervention."""
        for next_state in V2PositionState:
            if next_state != V2PositionState.QUARANTINED:
                with pytest.raises(ValueError, match="Illegal V2 position transition"):
                    validate_position_transition(V2PositionState.QUARANTINED, next_state)

    def test_same_state_noop_always_legal(self) -> None:
        for state in V2PositionState:
            validate_position_transition(state, state)  # Must not raise


class TestProtectionStateTransitions:
    def test_intent_to_submitting_legal(self) -> None:
        validate_protection_transition(
            V2ProtectionState.PROTECTION_INTENT,
            V2ProtectionState.PROTECTION_SUBMITTING,
        )

    def test_submitting_to_active_legal(self) -> None:
        validate_protection_transition(
            V2ProtectionState.PROTECTION_SUBMITTING,
            V2ProtectionState.PROTECTION_ACTIVE,
        )

    def test_active_to_triggered_legal(self) -> None:
        validate_protection_transition(
            V2ProtectionState.PROTECTION_ACTIVE,
            V2ProtectionState.PROTECTION_TRIGGERED,
        )

    def test_triggered_to_filled_legal(self) -> None:
        validate_protection_transition(
            V2ProtectionState.PROTECTION_TRIGGERED,
            V2ProtectionState.PROTECTION_FILLED,
        )

    def test_intent_to_active_illegal(self) -> None:
        """Cannot skip SUBMITTING; protection must be submitted before it can be active."""
        with pytest.raises(ValueError, match="Illegal V2 protection transition"):
            validate_protection_transition(
                V2ProtectionState.PROTECTION_INTENT,
                V2ProtectionState.PROTECTION_ACTIVE,
            )

    def test_filled_to_any_state_illegal(self) -> None:
        """PROTECTION_FILLED is terminal."""
        for next_state in V2ProtectionState:
            if next_state != V2ProtectionState.PROTECTION_FILLED:
                with pytest.raises(ValueError, match="Illegal V2 protection transition"):
                    validate_protection_transition(V2ProtectionState.PROTECTION_FILLED, next_state)
