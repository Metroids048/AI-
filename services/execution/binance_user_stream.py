"""Deterministic reducer for Binance USD-M user order events."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from services.execution.state_machine import ExchangeOrderEvent, ExecutionStateMachine
from shared.models import ExecutionState

_STATUS_MAP = {
    "NEW": ExecutionState.SUBMITTED,
    "PARTIALLY_FILLED": ExecutionState.PARTIALLY_FILLED,
    "FILLED": ExecutionState.FILLED,
    "CANCELED": ExecutionState.CANCELED,
    "EXPIRED": ExecutionState.CANCELED,
    "REJECTED": ExecutionState.REJECTED,
}


class BinanceUserDataReducer:
    def __init__(self, *, platform_client_prefix: str = "aqrp-") -> None:
        self.platform_client_prefix = platform_client_prefix
        self.external_order_ids: set[str] = set()

    def apply(self, payload: dict[str, Any], machine: ExecutionStateMachine) -> bool:
        if payload.get("e") != "ORDER_TRADE_UPDATE":
            return False
        order = payload.get("o")
        if not isinstance(order, dict):
            return False
        client_order_id = str(order.get("c") or "")
        exchange_order_id = str(order.get("i") or "")
        if not client_order_id.startswith(self.platform_client_prefix):
            if exchange_order_id:
                self.external_order_ids.add(exchange_order_id)
            return False
        update_ms = int(order.get("T") or payload.get("E") or 0)
        state = _STATUS_MAP.get(str(order.get("X") or "").upper(), ExecutionState.UNKNOWN)
        event = ExchangeOrderEvent(
            event_id=f"{exchange_order_id}:{update_ms}:{state.value}",
            client_order_id=client_order_id,
            state=state,
            filled_quantity=Decimal(str(order.get("z") or "0")),
            exchange_update_time=datetime.fromtimestamp(update_ms / 1000, tz=UTC),
        )
        return machine.apply_exchange_event(event)
