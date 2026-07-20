from __future__ import annotations

from services.execution.binance_user_stream import BinanceUserDataReducer
from services.execution.state_machine import ExecutionStateMachine
from shared.models import ExecutionState


def test_binance_user_reducer_deduplicates_order_trade_updates() -> None:
    machine = ExecutionStateMachine(initial_state=ExecutionState.SUBMITTED)
    payload = {
        "e": "ORDER_TRADE_UPDATE",
        "E": 1_784_537_200_000,
        "o": {
            "c": "aqrp-abc",
            "i": 123,
            "X": "FILLED",
            "z": "0.003",
            "T": 1_784_537_200_000,
        },
    }
    reducer = BinanceUserDataReducer()

    assert reducer.apply(payload, machine) is True
    assert reducer.apply(payload, machine) is False
    assert machine.state is ExecutionState.FILLED


def test_binance_user_reducer_reports_external_orders_without_mutating_machine() -> None:
    machine = ExecutionStateMachine(initial_state=ExecutionState.SUBMITTED)
    payload = {
        "e": "ORDER_TRADE_UPDATE",
        "E": 1_784_537_200_000,
        "o": {"c": "manual-order", "i": 123, "X": "FILLED", "z": "0.003", "T": 1_784_537_200_000},
    }
    reducer = BinanceUserDataReducer()

    assert reducer.apply(payload, machine) is False
    assert reducer.external_order_ids == {"123"}
    assert machine.state is ExecutionState.SUBMITTED
