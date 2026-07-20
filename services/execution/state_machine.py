"""Controlled execution lifecycle and deterministic exchange-event reducer."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from shared.models import ExecutionState


class InvalidExecutionTransition(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ExchangeOrderEvent:
    event_id: str
    client_order_id: str
    state: ExecutionState
    filled_quantity: Decimal
    exchange_update_time: datetime


_ALLOWED_TRANSITIONS: dict[ExecutionState, frozenset[ExecutionState]] = {
    ExecutionState.INTENT_CREATED: frozenset({ExecutionState.NORMALIZED, ExecutionState.REJECTED}),
    ExecutionState.NORMALIZED: frozenset({ExecutionState.SUBMITTING, ExecutionState.REJECTED}),
    ExecutionState.SUBMITTING: frozenset({ExecutionState.SUBMITTED, ExecutionState.UNKNOWN, ExecutionState.REJECTED}),
    ExecutionState.SUBMITTED: frozenset(
        {
            ExecutionState.PARTIALLY_FILLED,
            ExecutionState.FILLED,
            ExecutionState.CANCELED,
            ExecutionState.REJECTED,
            ExecutionState.UNKNOWN,
        }
    ),
    ExecutionState.PARTIALLY_FILLED: frozenset(
        {
            ExecutionState.PARTIALLY_FILLED,
            ExecutionState.FILLED,
            ExecutionState.CANCELED,
            ExecutionState.UNKNOWN,
        }
    ),
    ExecutionState.FILLED: frozenset(
        {ExecutionState.PROTECTION_PENDING, ExecutionState.PROTECTED, ExecutionState.RECOVERY_REQUIRED}
    ),
    ExecutionState.PROTECTION_PENDING: frozenset({ExecutionState.PROTECTED, ExecutionState.RECOVERY_REQUIRED}),
    ExecutionState.PROTECTED: frozenset({ExecutionState.CLOSED, ExecutionState.RECOVERY_REQUIRED}),
    ExecutionState.UNKNOWN: frozenset(
        {
            ExecutionState.SUBMITTED,
            ExecutionState.PARTIALLY_FILLED,
            ExecutionState.FILLED,
            ExecutionState.CANCELED,
            ExecutionState.REJECTED,
            ExecutionState.RECOVERY_REQUIRED,
        }
    ),
    ExecutionState.RECOVERY_REQUIRED: frozenset(
        {ExecutionState.PROTECTED, ExecutionState.CLOSED, ExecutionState.CANCELED}
    ),
    ExecutionState.CANCELED: frozenset(),
    ExecutionState.REJECTED: frozenset(),
    ExecutionState.CLOSED: frozenset(),
}

_STATE_PRIORITY = {
    ExecutionState.INTENT_CREATED: 0,
    ExecutionState.NORMALIZED: 1,
    ExecutionState.SUBMITTING: 2,
    ExecutionState.SUBMITTED: 3,
    ExecutionState.PARTIALLY_FILLED: 4,
    ExecutionState.FILLED: 5,
    ExecutionState.PROTECTION_PENDING: 6,
    ExecutionState.PROTECTED: 7,
    ExecutionState.CANCELED: 8,
    ExecutionState.REJECTED: 8,
    ExecutionState.UNKNOWN: 9,
    ExecutionState.RECOVERY_REQUIRED: 10,
    ExecutionState.CLOSED: 11,
}


class ExecutionStateMachine:
    def __init__(self, *, initial_state: ExecutionState = ExecutionState.INTENT_CREATED) -> None:
        self.state = initial_state
        self.filled_quantity = Decimal("0")
        self.last_exchange_update_time: datetime | None = None
        self._event_ids: set[str] = set()

    @property
    def allows_new_open(self) -> bool:
        return self.state not in {
            ExecutionState.UNKNOWN,
            ExecutionState.RECOVERY_REQUIRED,
            ExecutionState.PROTECTION_PENDING,
        }

    def transition(self, target: ExecutionState) -> None:
        if target == self.state:
            return
        if target not in _ALLOWED_TRANSITIONS[self.state]:
            raise InvalidExecutionTransition(f"invalid execution transition: {self.state} -> {target}")
        self.state = target

    def apply_exchange_event(self, event: ExchangeOrderEvent) -> bool:
        if event.event_id in self._event_ids:
            return False
        if self.last_exchange_update_time is not None:
            if event.exchange_update_time < self.last_exchange_update_time:
                return False
            if (
                event.exchange_update_time == self.last_exchange_update_time
                and _STATE_PRIORITY[event.state] <= _STATE_PRIORITY[self.state]
            ):
                return False

        self.transition(event.state)
        self._event_ids.add(event.event_id)
        self.last_exchange_update_time = event.exchange_update_time
        self.filled_quantity = max(self.filled_quantity, event.filled_quantity)
        return True
