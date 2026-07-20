from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from services.execution.state_machine import (
    ExchangeOrderEvent,
    ExecutionStateMachine,
    InvalidExecutionTransition,
)
from shared.models import ExecutionState


def test_execution_state_machine_rejects_backward_transition() -> None:
    machine = ExecutionStateMachine(initial_state=ExecutionState.FILLED)

    with pytest.raises(InvalidExecutionTransition):
        machine.transition(ExecutionState.SUBMITTED)


def test_user_event_reducer_ignores_duplicates_and_out_of_order_events() -> None:
    machine = ExecutionStateMachine(initial_state=ExecutionState.SUBMITTED)
    base = datetime(2026, 7, 20, 7, 0, tzinfo=UTC)
    filled = ExchangeOrderEvent(
        event_id="evt-2",
        client_order_id="aqrp-1",
        state=ExecutionState.FILLED,
        filled_quantity=Decimal("0.003"),
        exchange_update_time=base + timedelta(seconds=2),
    )
    stale_partial = ExchangeOrderEvent(
        event_id="evt-1",
        client_order_id="aqrp-1",
        state=ExecutionState.PARTIALLY_FILLED,
        filled_quantity=Decimal("0.001"),
        exchange_update_time=base + timedelta(seconds=1),
    )

    assert machine.apply_exchange_event(filled) is True
    assert machine.apply_exchange_event(filled) is False
    assert machine.apply_exchange_event(stale_partial) is False
    assert machine.state is ExecutionState.FILLED
    assert machine.filled_quantity == Decimal("0.003")


@pytest.mark.parametrize("state", [ExecutionState.UNKNOWN, ExecutionState.RECOVERY_REQUIRED])
def test_unknown_or_recovery_state_blocks_new_open(state: ExecutionState) -> None:
    assert ExecutionStateMachine(initial_state=state).allows_new_open is False
