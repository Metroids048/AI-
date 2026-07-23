"""Minimal persisted execution lifecycle events backed by decision_events."""

from __future__ import annotations

from datetime import UTC, datetime

from services.strategy_library import DecisionEventRepository
from shared.models import DecisionEvent, DecisionEventType, ExecutionOrderRequest, OrderExecution, PaperRun


def record_execution_event(
    *,
    repository: DecisionEventRepository,
    event_type: DecisionEventType,
    paper_run: PaperRun,
    request: ExecutionOrderRequest | None = None,
    identity_order: OrderExecution | None = None,
    position_record_id: str | None = None,
    reason_code: str | None = None,
    payload: dict | None = None,
    timestamp: datetime | None = None,
) -> DecisionEvent | None:
    """Append an event when an immutable decision identity is available."""

    intent = request.trade_intent if request is not None else None
    cycle_id = intent.cycle_id if intent is not None else (identity_order.cycle_id if identity_order else None)
    decision_id = intent.decision_id if intent is not None else (identity_order.decision_id if identity_order else None)
    config_snapshot_id = (
        intent.config_snapshot_id
        if intent is not None
        else (identity_order.config_snapshot_id if identity_order else None)
    )
    config_hash = intent.config_hash if intent is not None else (identity_order.config_hash if identity_order else None)
    candle_close_time = (
        intent.signal_candle_close_time
        if intent is not None
        else (identity_order.signal_candle_close_time if identity_order else None)
    )
    strategy_version = (
        intent.strategy_version if intent is not None else (identity_order.version_id if identity_order else None)
    )
    strategy_id = intent.strategy_id if intent is not None else (identity_order.strategy_id if identity_order else None)
    if not all(
        (
            paper_run.paper_run_id,
            cycle_id,
            decision_id,
            config_snapshot_id,
            config_hash,
            candle_close_time,
            strategy_id,
        )
    ):
        return None
    source_request = request
    symbol = intent.symbol if intent is not None else (identity_order.symbol if identity_order else "")
    position_side = (
        intent.position_side.value
        if intent is not None
        else (str(identity_order.direction) if identity_order is not None else None)
    )
    event = DecisionEvent(
        paper_run_id=paper_run.paper_run_id or "",
        cycle_id=cycle_id or "",
        decision_id=decision_id or "",
        event_type=event_type,
        strategy_id=strategy_id or "",
        strategy_version=strategy_version or "",
        config_snapshot_id=config_snapshot_id or "",
        config_hash=config_hash or "",
        symbol=symbol,
        timeframe=str(
            (source_request.entry_context.get("timeframe") if source_request is not None else None)
            or (identity_order.timeframe if identity_order is not None else None)
            or "1m"
        ),
        candle_close_time=candle_close_time or datetime.now(UTC),
        run_id=paper_run.paper_run_id,
        position_side=position_side,
        order_origin=(
            source_request.order_origin
            if source_request is not None
            else (identity_order.order_origin if identity_order is not None else None)
        ),
        position_record_id=position_record_id,
        reason_code=reason_code,
        payload=payload or {},
        created_at=timestamp or datetime.now(UTC),
    )
    return repository.append(event)
