"""Read-only 24h audit for the Runtime observability closeout loop."""

from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select

from apps.api.routers.runtime import _reason_category, _v2_decision_payload
from services.automated_trading.infrastructure.models import (
    V2ExchangeFill,
    V2ExchangeOrder,
    V2ExecutionCycle,
    V2ExecutionDecision,
    V2ExecutionIntent,
    V2ManagedPosition,
    V2ProtectionRecord,
)
from services.database import get_session_factory
from services.execution.runtime_state import load_external_scheduler_state
from shared.models.risk import TESTNET_CANARY_RUNTIME_CONTRACT

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts" / "runtime_closeout" / "SAMPLING_24H_AUDIT.json"


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def main() -> None:
    observed_at = datetime.now(UTC)
    window_start = observed_at - timedelta(hours=24)
    session = get_session_factory()()
    try:
        cycles = list(
            session.scalars(
                select(V2ExecutionCycle)
                .where(V2ExecutionCycle.execution_mode == "BINANCE_TESTNET")
                .where(V2ExecutionCycle.bar_timestamp >= window_start)
                .order_by(V2ExecutionCycle.bar_timestamp.asc())
            )
        )
        decisions = list(
            session.scalars(
                select(V2ExecutionDecision)
                .where(V2ExecutionDecision.created_at >= window_start)
                .order_by(V2ExecutionDecision.created_at.asc())
            )
        )
        cycle_by_id = {cycle.cycle_id: cycle for cycle in cycles}
        rows = [
            _v2_decision_payload(decision, cycle_by_id[decision.cycle_id])
            for decision in decisions
            if decision.cycle_id in cycle_by_id
        ]
        effective = [row for row in rows if row["terminal_reason"] != "DUPLICATE_DECISION"]
        reason_counts = Counter(row["terminal_reason"] for row in effective)
        categories: dict[str, Counter[str]] = {
            "strategy_filter_counts": Counter(),
            "operational_block_counts": Counter(),
            "system_failure_counts": Counter(),
        }
        for reason, count in reason_counts.items():
            if reason in {"OK", "CANDIDATE_READY", "ENTRY_INTENT_CREATED"}:
                continue
            categories[
                {
                    "strategy_filter": "strategy_filter_counts",
                    "operational_block": "operational_block_counts",
                    "system_failure": "system_failure_counts",
                }[_reason_category(reason)]
            ][reason] = count

        intents = list(
            session.scalars(
                select(V2ExecutionIntent)
                .where(V2ExecutionIntent.execution_mode == "BINANCE_TESTNET")
                .where(V2ExecutionIntent.created_at >= window_start)
            )
        )
        orders = list(session.scalars(select(V2ExchangeOrder).where(V2ExchangeOrder.created_at >= window_start)))
        fills = list(
            session.scalars(
                select(V2ExchangeFill)
                .where(V2ExchangeFill.received_at >= window_start)
                .where(V2ExchangeFill.reduce_only.is_(False))
            )
        )
        protections = list(
            session.scalars(select(V2ProtectionRecord).where(V2ProtectionRecord.created_at >= window_start))
        )
        closed_positions = list(
            session.scalars(
                select(V2ManagedPosition)
                .where(V2ManagedPosition.execution_mode == "BINANCE_TESTNET")
                .where(V2ManagedPosition.closed_at >= window_start)
            )
        )
        state = load_external_scheduler_state(now=observed_at)
        at_capacity = False
        current_open_positions = None
        try:
            current_open_positions = len(
                list(
                    session.scalars(
                        select(V2ManagedPosition).where(
                            V2ManagedPosition.execution_mode == "BINANCE_TESTNET",
                            V2ManagedPosition.state.not_in(("CLOSED", "QUARANTINED")),
                        )
                    )
                )
            )
            at_capacity = current_open_positions >= int(TESTNET_CANARY_RUNTIME_CONTRACT["max_open_positions"])
        except Exception:
            current_open_positions = None
        runtime_healthy = bool(
            state.running and state.data_fresh and state.exchange_info_ready and not state.scheduler_error
        )
        sampling_drought = bool(
            runtime_healthy
            and not at_capacity
            and len(effective) >= 300
            and not any(row["candidate_created"] for row in effective)
        )
        result = {
            "observed_at": observed_at.isoformat(),
            "window_start": window_start.isoformat(),
            "window_hours": 24,
            "effective_decisions": len(effective),
            "duplicates": len(rows) - len(effective),
            "base_signals": sum(bool(row["base_signal_detected"]) for row in effective),
            "mtf_passes": sum(bool(row["mtf_alignment_passed"]) for row in effective),
            "candidates": sum(bool(row["candidate_created"]) for row in effective),
            "intents": len(intents),
            "orders": sum(order.submitted_at is not None for order in orders),
            "fills": len(fills),
            "closed_positions": len(closed_positions),
            "strategy_filter_counts": dict(categories["strategy_filter_counts"]),
            "operational_block_counts": dict(categories["operational_block_counts"]),
            "system_failure_counts": dict(categories["system_failure_counts"]),
            "current_status": {
                "runtime_healthy": runtime_healthy,
                "current_active_blocker": "MAX_OPEN_EXPOSURES" if at_capacity else None,
                "current_open_positions": current_open_positions,
                "effective_max_open_positions": int(TESTNET_CANARY_RUNTIME_CONTRACT["max_open_positions"]),
                "capacity_source": "TESTNET_CANARY_RUNTIME_CONTRACT",
            },
            "sampling_drought_confirmed": sampling_drought,
            "terminal_state": "SAMPLING_DROUGHT_CONFIRMED" if sampling_drought else "HEALTHY_WAITING_FOR_MARKET",
            "natural_end_to_end_evidence": {
                "entry_to_protection": bool(fills and protections),
                "closed_position": bool(closed_positions),
                "exchange_source": "BINANCE_TESTNET",
            },
        }
    finally:
        session.close()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=True, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
