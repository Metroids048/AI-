"""Read-only preflight for autonomous Testnet startup recovery.

This audit deliberately does not repair rows or call an exchange write API. It
establishes whether a stale manual baseline can be acknowledged without
silently hiding an unresolved V2 execution fact.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from services.automated_trading.domain.enums import V2ExecutionMode
from services.automated_trading.infrastructure.models import (
    V2ExchangeFill,
    V2ExchangeOrder,
    V2ExecutionIncident,
    V2ExecutionIntent,
    V2ManagedPosition,
    V2ProtectionRecord,
)

UNRESOLVED_INTENT_STATES = {
    "INTENT_CREATED",
    "EXCHANGE_SUBMITTING",
    "EXCHANGE_UNKNOWN",
    "EXCHANGE_ACKNOWLEDGED",
}


def _row(item: Any, *fields: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for field in fields:
        value = getattr(item, field, None)
        result[field] = value.isoformat() if isinstance(value, datetime) else value
    return result


def audit(database_url: str) -> dict[str, Any]:
    engine = create_engine(database_url)
    try:
        with Session(engine) as session:
            intents = tuple(
                session.scalars(
                    select(V2ExecutionIntent).where(
                        V2ExecutionIntent.execution_mode == V2ExecutionMode.BINANCE_TESTNET.value,
                        V2ExecutionIntent.state.in_(UNRESOLVED_INTENT_STATES),
                    )
                )
            )
            intent_ids = {item.intent_id for item in intents}
            orders = (
                tuple(
                    session.scalars(
                        select(V2ExchangeOrder).where(
                            V2ExchangeOrder.intent_id.in_(intent_ids),
                        )
                    )
                )
                if intent_ids
                else ()
            )
            fills = (
                tuple(
                    session.scalars(
                        select(V2ExchangeFill).where(
                            V2ExchangeFill.intent_id.in_(intent_ids),
                        )
                    )
                )
                if intent_ids
                else ()
            )
            positions = tuple(
                session.scalars(
                    select(V2ManagedPosition).where(
                        V2ManagedPosition.execution_mode == V2ExecutionMode.BINANCE_TESTNET.value,
                        V2ManagedPosition.state.not_in(("CLOSED", "QUARANTINED")),
                    )
                )
            )
            all_testnet_positions = tuple(
                session.scalars(
                    select(V2ManagedPosition).where(
                        V2ManagedPosition.execution_mode == V2ExecutionMode.BINANCE_TESTNET.value,
                    )
                )
            )
            all_testnet_intents = tuple(
                session.scalars(
                    select(V2ExecutionIntent).where(
                        V2ExecutionIntent.execution_mode == V2ExecutionMode.BINANCE_TESTNET.value,
                    )
                )
            )
            intent_by_id = {item.intent_id: item for item in all_testnet_intents}
            order_by_intent = {
                item.intent_id: item
                for item in session.scalars(select(V2ExchangeOrder))
                if item.intent_id in intent_by_id
            }
            position_by_intent = {item.intent_id: item for item in all_testnet_positions}
            entry_fills = tuple(
                item
                for item in session.scalars(select(V2ExchangeFill).where(V2ExchangeFill.reduce_only.is_(False)))
                if item.intent_id in intent_by_id
            )
            historical_gap_incidents = tuple(
                session.scalars(
                    select(V2ExecutionIncident).where(
                        V2ExecutionIncident.incident_type == "HISTORICAL_LEDGER_GAP",
                        V2ExecutionIncident.intent_id.is_not(None),
                    )
                )
            )
            historical_gap_intent_ids = {
                incident.intent_id for incident in historical_gap_incidents if incident.intent_id
            }
            unprojected_fills_by_intent: dict[str, list[V2ExchangeFill]] = {}
            for fill in entry_fills:
                if fill.intent_id not in position_by_intent and fill.intent_id not in historical_gap_intent_ids:
                    unprojected_fills_by_intent.setdefault(fill.intent_id, []).append(fill)
            lifecycle_gaps: list[dict[str, Any]] = []
            for intent_id, persisted_fills in unprojected_fills_by_intent.items():
                intent = intent_by_id[intent_id]
                lifecycle_gaps.append(
                    {
                        "kind": "CONFIRMED_ENTRY_FILL_UNPROJECTED",
                        "intent_id": intent.intent_id,
                        "symbol": intent.symbol,
                        "direction": intent.direction,
                        "state": intent.state,
                        "client_order_id": (
                            order_by_intent[intent.intent_id].client_order_id
                            if intent.intent_id in order_by_intent
                            else None
                        ),
                        "entry_fills": [
                            {
                                "exchange_order_id": fill.exchange_order_id,
                                "trade_id": fill.trade_id,
                                "filled_quantity": str(fill.filled_quantity),
                                "fill_price": str(fill.fill_price),
                                "exchange_event_time": fill.exchange_event_time.isoformat(),
                            }
                            for fill in persisted_fills
                        ],
                    }
                )
            for position in all_testnet_positions:
                if position.state != "QUARANTINED":
                    continue
                quarantined_intent = intent_by_id.get(position.intent_id)
                if quarantined_intent is None:
                    continue
                lifecycle_gaps.append(
                    {
                        "kind": "QUARANTINED_ENTRY_LIFECYCLE",
                        "intent_id": quarantined_intent.intent_id,
                        "position_id": position.position_id,
                        "symbol": quarantined_intent.symbol,
                        "direction": quarantined_intent.direction,
                        "state": quarantined_intent.state,
                    }
                )
            position_ids = {item.position_id for item in positions}
            protections = tuple(
                session.scalars(
                    select(V2ProtectionRecord).where(
                        V2ProtectionRecord.state == "PROTECTION_ACTIVE",
                    )
                )
            )
            # Protection rows inherit execution mode through their managed
            # position; a missing Testnet position is itself an orphan fact.
            testnet_position_ids = {
                item.position_id
                for item in session.scalars(
                    select(V2ManagedPosition).where(
                        V2ManagedPosition.execution_mode == V2ExecutionMode.BINANCE_TESTNET.value,
                    )
                )
            }
            protections = tuple(item for item in protections if item.position_id in testnet_position_ids)
            orphan_protections = tuple(item for item in protections if item.position_id not in position_ids)

            result: dict[str, Any] = {
                "schema_version": 1,
                "generated_at": datetime.now(UTC).isoformat(),
                "execution_mode": V2ExecutionMode.BINANCE_TESTNET.value,
                "unresolved_intents": [_row(item, "intent_id", "symbol", "direction", "state") for item in intents],
                "related_orders": [
                    _row(item, "order_record_id", "intent_id", "client_order_id", "exchange_order_id")
                    for item in orders
                ],
                "related_fills": [
                    _row(item, "fill_id", "intent_id", "exchange_order_id", "trade_id", "filled_quantity")
                    for item in fills
                ],
                "open_managed_positions": [
                    _row(item, "position_id", "intent_id", "symbol", "direction", "quantity", "state")
                    for item in positions
                ],
                "all_testnet_positions_for_active_protections": [
                    _row(item, "position_id", "intent_id", "symbol", "direction", "quantity", "state", "closed_at")
                    for item in all_testnet_positions
                    if item.position_id in {protection.position_id for protection in protections}
                ],
                "active_protections": [
                    _row(item, "protection_id", "position_id", "stop_exchange_order_id", "tp_exchange_order_id")
                    for item in protections
                ],
                "orphan_active_protections": [
                    _row(item, "protection_id", "position_id", "stop_exchange_order_id", "tp_exchange_order_id")
                    for item in orphan_protections
                ],
                "lifecycle_gaps": lifecycle_gaps,
                "historical_ledger_integrity": "DEGRADED" if historical_gap_incidents else "HEALTHY",
                "historical_gap_count": len(historical_gap_incidents),
                "historical_gaps": [
                    {
                        "incident_id": incident.incident_id,
                        "intent_id": incident.intent_id,
                        "position_id": incident.position_id,
                        "resolution": incident.context.get("resolution"),
                        "realized_pnl": incident.context.get("realized_pnl"),
                        "cutover_epoch": incident.context.get("cutover_epoch"),
                    }
                    for incident in historical_gap_incidents
                ],
            }
            result["safe_for_manual_baseline_ack"] = not any(
                (
                    result["unresolved_intents"],
                    result["open_managed_positions"],
                    result["orphan_active_protections"],
                    result["lifecycle_gaps"],
                )
            )
            try:
                from services.automated_trading.infrastructure.binance_adapter import BinanceTestnetAdapter

                snapshot = BinanceTestnetAdapter(
                    execution_mode=V2ExecutionMode.BINANCE_TESTNET
                ).fetch_authoritative_snapshot()
                result["exchange_positions"] = [asdict(item) for item in snapshot.positions]
                result["exchange_pending_orders"] = [asdict(item) for item in snapshot.pending_orders]
                pending_ids = {str(item.exchange_order_id) for item in snapshot.pending_orders}
                result["orphan_protection_orders_live"] = [
                    order_id
                    for protection in result["orphan_active_protections"]
                    for order_id in (protection.get("stop_exchange_order_id"), protection.get("tp_exchange_order_id"))
                    if order_id and str(order_id) in pending_ids
                ]
                result["safe_for_manual_baseline_ack"] = bool(
                    result["safe_for_manual_baseline_ack"] and not result["orphan_protection_orders_live"]
                )
            except Exception as exc:  # noqa: BLE001
                result["exchange_snapshot_error"] = f"{type(exc).__name__}: {exc}"
                result["safe_for_manual_baseline_ack"] = False
            return result
    finally:
        engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--output")
    args = parser.parse_args()
    result = audit(args.database_url)
    rendered = json.dumps(result, ensure_ascii=False, indent=2, default=str)
    print(rendered)
    if args.output:
        destination = Path(args.output)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(rendered + "\n", encoding="utf-8")
    return 0 if result["safe_for_manual_baseline_ack"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
