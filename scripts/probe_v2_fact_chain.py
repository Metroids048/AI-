#!/usr/bin/env python3
"""Read-only probe for one persisted V2 Scheduler cycle fact chain."""

from __future__ import annotations

import argparse
import json
from typing import Any

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from services.automated_trading.infrastructure.models import (
    V2ExchangeFill,
    V2ExchangeOrder,
    V2ExecutionCycle,
    V2ExecutionDecision,
    V2ExecutionIntent,
    V2ManagedPosition,
    V2ProtectionRecord,
)


def _value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def probe(database_url: str, cycle_id: str) -> dict[str, Any]:
    """Return the persisted chain without mutating the database."""
    engine = create_engine(database_url)
    try:
        with Session(engine) as session:
            cycle = session.get(V2ExecutionCycle, cycle_id)
            decisions = tuple(
                session.scalars(select(V2ExecutionDecision).where(V2ExecutionDecision.cycle_id == cycle_id))
            )
            intents = tuple(session.scalars(select(V2ExecutionIntent).where(V2ExecutionIntent.cycle_id == cycle_id)))
            intent_ids = [item.intent_id for item in intents]
            orders = (
                tuple(session.scalars(select(V2ExchangeOrder).where(V2ExchangeOrder.intent_id.in_(intent_ids))))
                if intent_ids
                else ()
            )
            fills = (
                tuple(session.scalars(select(V2ExchangeFill).where(V2ExchangeFill.intent_id.in_(intent_ids))))
                if intent_ids
                else ()
            )
            positions = (
                tuple(session.scalars(select(V2ManagedPosition).where(V2ManagedPosition.intent_id.in_(intent_ids))))
                if intent_ids
                else ()
            )
            related_exit_position_ids = {
                item.candidate_key.split(":", 2)[1]
                for item in intents
                if item.candidate_key.startswith("exit:") and len(item.candidate_key.split(":", 2)) == 3
            }
            if related_exit_position_ids:
                related_positions = tuple(
                    session.scalars(
                        select(V2ManagedPosition).where(V2ManagedPosition.position_id.in_(related_exit_position_ids))
                    )
                )
                positions = tuple({item.position_id: item for item in (*positions, *related_positions)}.values())
            position_ids = [item.position_id for item in positions]
            protections = (
                tuple(
                    session.scalars(select(V2ProtectionRecord).where(V2ProtectionRecord.position_id.in_(position_ids)))
                )
                if position_ids
                else ()
            )

            return {
                "cycle": (
                    {
                        "cycle_id": cycle.cycle_id,
                        "symbol": cycle.symbol,
                        "bar_timestamp": _value(cycle.bar_timestamp),
                        "decision_terminal": cycle.decision_terminal,
                        "started_at": _value(cycle.started_at),
                        "completed_at": _value(cycle.completed_at),
                    }
                    if cycle
                    else None
                ),
                "decisions": [
                    {
                        "decision_id": item.decision_id,
                        "candidate_key": item.candidate_key,
                        "terminal_reason": item.terminal_reason,
                        "payload": item.payload,
                    }
                    for item in decisions
                ],
                "intents": [
                    {
                        "intent_id": item.intent_id,
                        "decision_id": item.decision_id,
                        "symbol": item.symbol,
                        "direction": item.direction,
                        "state": item.state,
                        "candidate_key": item.candidate_key,
                    }
                    for item in intents
                ],
                "orders": [
                    {
                        "order_record_id": item.order_record_id,
                        "intent_id": item.intent_id,
                        "client_order_id": item.client_order_id,
                        "exchange_order_id": item.exchange_order_id,
                        "quantity": _value(item.quantity),
                        "leverage": item.leverage,
                        "submitted_at": _value(item.submitted_at),
                        "acknowledged_at": _value(item.acknowledged_at),
                        "filled_quantity": _value(item.filled_quantity),
                        "average_fill_price": _value(item.average_fill_price),
                        "rejection_reason": item.rejection_reason,
                    }
                    for item in orders
                ],
                "fills": [
                    {
                        "fill_id": item.fill_id,
                        "intent_id": item.intent_id,
                        "exchange_order_id": item.exchange_order_id,
                        "trade_id": item.trade_id,
                        "filled_quantity": _value(item.filled_quantity),
                        "fill_price": _value(item.fill_price),
                        "reduce_only": item.reduce_only,
                    }
                    for item in fills
                ],
                "positions": [
                    {
                        "position_id": item.position_id,
                        "intent_id": item.intent_id,
                        "symbol": item.symbol,
                        "direction": item.direction,
                        "quantity": _value(item.quantity),
                        "entry_price": _value(item.entry_price),
                        "state": item.state,
                        "closed_at": _value(item.closed_at),
                        "realized_pnl": _value(item.realized_pnl),
                    }
                    for item in positions
                ],
                "protections": [
                    {
                        "protection_id": item.protection_id,
                        "position_id": item.position_id,
                        "state": item.state,
                        "stop_client_order_id": item.stop_client_order_id,
                        "tp_client_order_id": item.tp_client_order_id,
                        "stop_exchange_order_id": item.stop_exchange_order_id,
                        "tp_exchange_order_id": item.tp_exchange_order_id,
                    }
                    for item in protections
                ],
            }
    finally:
        engine.dispose()


def cycle_id_for_client_order(database_url: str, client_order_id: str) -> str | None:
    engine = create_engine(database_url)
    try:
        with Session(engine) as session:
            return session.scalar(
                select(V2ExecutionIntent.cycle_id)
                .join(V2ExchangeOrder, V2ExchangeOrder.intent_id == V2ExecutionIntent.intent_id)
                .where(V2ExchangeOrder.client_order_id == client_order_id)
            )
    finally:
        engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", required=True)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--cycle-id")
    group.add_argument("--client-order-id")
    args = parser.parse_args()
    cycle_id = args.cycle_id or cycle_id_for_client_order(
        args.database_url,
        args.client_order_id,
    )
    if not cycle_id:
        raise SystemExit("no persisted cycle found for the requested client order id")
    print(json.dumps(probe(args.database_url, cycle_id), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
