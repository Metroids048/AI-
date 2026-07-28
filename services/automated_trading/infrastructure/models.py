"""V2 database models for Exchange-First execution persistence.

These models store immutable execution facts as an append-only event log.
State is derived from events, not mutated in place.

Tables:
- v2_execution_cycles: Scheduler cycle records
- v2_execution_intents: Entry intent records
- v2_execution_orders: Exchange order facts (with receipts)
- v2_managed_positions: Position projections (requires fill receipt)
- v2_protection_records: Stop-loss/take-profit records
- v2_execution_events: Append-only event log
- v2_reconciliation_snapshots: Exchange truth snapshots
- v2_execution_incidents: Anomalies and quarantines
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from services.strategy_library.models import Base


def _uuid_str() -> str:
    return str(uuid.uuid4())


class V2ExecutionCycle(Base):
    """Scheduler cycle record. One cycle = one closed bar evaluation."""

    __tablename__ = "v2_execution_cycles"

    cycle_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    timeframe: Mapped[str] = mapped_column(String(10), nullable=False)
    bar_timestamp: Mapped[datetime] = mapped_column(nullable=False, index=True)
    execution_mode: Mapped[str] = mapped_column(String(30), nullable=False)
    fencing_token: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    started_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    decision_terminal: Mapped[str | None] = mapped_column(String(50), nullable=True)

    __table_args__ = (
        Index("ix_v2_cycle_symbol_bar", "symbol", "bar_timestamp"),
        CheckConstraint(
            "execution_mode IN ('BINANCE_TESTNET', 'LOCAL_PAPER')",
            name="ck_v2_cycle_execution_mode",
        ),
    )


class V2ExecutionIntent(Base):
    """Entry intent created from strategy decision.

    One intent may result in zero or one exchange order.
    """

    __tablename__ = "v2_execution_intents"

    intent_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    cycle_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    direction: Mapped[str] = mapped_column(String(10), nullable=False)
    candidate_key: Mapped[str] = mapped_column(String(100), nullable=False)
    candidate_type: Mapped[str] = mapped_column(String(30), nullable=False)
    execution_mode: Mapped[str] = mapped_column(String(30), nullable=False)
    decision_bar_timestamp: Mapped[datetime] = mapped_column(nullable=False)
    decision_funnel_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    state: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        CheckConstraint("direction IN ('long', 'short')", name="ck_v2_intent_direction"),
        CheckConstraint(
            "candidate_type IN ('PRIMARY', 'SAMPLING', 'RESEARCH')",
            name="ck_v2_intent_candidate_type",
        ),
        CheckConstraint(
            "execution_mode IN ('BINANCE_TESTNET', 'LOCAL_PAPER')",
            name="ck_v2_intent_execution_mode",
        ),
        CheckConstraint(
            "state IN ('INTENT_CREATED', 'EXCHANGE_SUBMITTING', 'EXCHANGE_ACKNOWLEDGED', "
            "'FILLED', 'REJECTED', 'CANCELLED', 'EXPIRED')",
            name="ck_v2_intent_state",
        ),
    )


class V2ExchangeOrder(Base):
    """Exchange order facts with fill receipts.

    Exchange-First: This table stores exchange_order_id + trade_ids as proof.
    """

    __tablename__ = "v2_exchange_orders"

    order_record_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    intent_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    client_order_id: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    exchange_order_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    quantity: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    leverage: Mapped[int] = mapped_column(Integer, nullable=False)
    submitted_at: Mapped[datetime | None] = mapped_column(nullable=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(nullable=True)
    filled_quantity: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    average_fill_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 4), nullable=True)
    total_fee: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    fill_timestamp: Mapped[datetime | None] = mapped_column(nullable=True)
    trade_ids: Mapped[dict] = mapped_column(JSON, default=dict)  # {"trade_ids": ["t1", "t2"]}
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_v2_order_quantity_positive"),
        CheckConstraint("leverage > 0", name="ck_v2_order_leverage_positive"),
        CheckConstraint(
            "filled_quantity IS NULL OR filled_quantity > 0",
            name="ck_v2_order_filled_quantity_positive",
        ),
        CheckConstraint(
            "average_fill_price IS NULL OR average_fill_price > 0",
            name="ck_v2_order_fill_price_positive",
        ),
    )


class V2ManagedPosition(Base):
    """Managed position projection from exchange fill.

    Exchange-First Invariant: Cannot exist without a fill receipt.
    One position per (symbol, direction, execution_mode).
    """

    __tablename__ = "v2_managed_positions"

    position_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    intent_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    order_record_id: Mapped[str] = mapped_column(String(36), nullable=False)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    direction: Mapped[str] = mapped_column(String(10), nullable=False)
    execution_mode: Mapped[str] = mapped_column(String(30), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    entry_price: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    entry_fee: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    state: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    projected_at: Mapped[datetime] = mapped_column(nullable=False)
    protected_at: Mapped[datetime | None] = mapped_column(nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    realized_pnl: Mapped[Decimal | None] = mapped_column(Numeric(20, 4), nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "symbol",
            "direction",
            "execution_mode",
            "state",
            name="uq_v2_position_one_open_per_symbol_direction_mode",
            # Only one open position per (symbol, direction, mode)
            # This is enforced when state NOT IN ('CLOSED', 'QUARANTINED')
            # by a partial unique index in the migration
        ),
        CheckConstraint("direction IN ('long', 'short')", name="ck_v2_position_direction"),
        CheckConstraint("quantity > 0", name="ck_v2_position_quantity_positive"),
        CheckConstraint("entry_price > 0", name="ck_v2_position_entry_price_positive"),
        CheckConstraint(
            "state IN ('POSITION_PROJECTED', 'PROTECTED', 'REDUCING', 'CLOSED', 'QUARANTINED')",
            name="ck_v2_position_state",
        ),
    )


class V2ProtectionRecord(Base):
    """Stop-loss and take-profit protection records."""

    __tablename__ = "v2_protection_records"

    protection_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    position_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    stop_loss_price: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    take_profit_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 4), nullable=True)
    stop_client_order_id: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    tp_client_order_id: Mapped[str | None] = mapped_column(String(100), nullable=True, unique=True)
    stop_exchange_order_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    tp_exchange_order_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    state: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())
    activated_at: Mapped[datetime | None] = mapped_column(nullable=True)

    __table_args__ = (
        CheckConstraint("stop_loss_price > 0", name="ck_v2_protection_stop_price_positive"),
        CheckConstraint(
            "take_profit_price IS NULL OR take_profit_price > 0",
            name="ck_v2_protection_tp_price_positive",
        ),
        CheckConstraint(
            "state IN ('PROTECTION_INTENT', 'PROTECTION_SUBMITTING', 'PROTECTION_ACTIVE', "
            "'PROTECTION_TRIGGERED', 'PROTECTION_FILLED', 'PROTECTION_CANCELLED')",
            name="ck_v2_protection_state",
        ),
    )


class V2ExecutionEvent(Base):
    """Append-only event log for state transitions.

    All state mutations are recorded as events. Events are never updated or deleted.
    """

    __tablename__ = "v2_execution_events"

    event_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    aggregate_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    aggregate_type: Mapped[str] = mapped_column(String(30), nullable=False)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    event_payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now(), index=True)

    __table_args__ = (
        Index("ix_v2_event_aggregate_occurred", "aggregate_id", "occurred_at"),
        CheckConstraint(
            "aggregate_type IN ('CYCLE', 'INTENT', 'ORDER', 'POSITION', 'PROTECTION')",
            name="ck_v2_event_aggregate_type",
        ),
    )


class V2ReconciliationSnapshot(Base):
    """Exchange reconciliation snapshots for audit and recovery."""

    __tablename__ = "v2_reconciliation_snapshots"

    snapshot_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    execution_mode: Mapped[str] = mapped_column(String(30), nullable=False)
    exchange_positions: Mapped[dict] = mapped_column(JSON, nullable=False)
    exchange_open_orders: Mapped[dict] = mapped_column(JSON, nullable=False)
    local_positions: Mapped[dict] = mapped_column(JSON, nullable=False)
    discrepancies: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now(), index=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('HEALTHY', 'DEGRADED', 'UNAVAILABLE', 'QUARANTINED')",
            name="ck_v2_recon_status",
        ),
    )


class V2ExecutionIncident(Base):
    """Anomalies, quarantines, and manual interventions."""

    __tablename__ = "v2_execution_incidents"

    incident_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    incident_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    related_aggregate_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    context: Mapped[dict] = mapped_column(JSON, default=dict)
    resolved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now(), index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(nullable=True)

    __table_args__ = (
        CheckConstraint("severity IN ('CRITICAL', 'HIGH', 'MEDIUM', 'LOW')", name="ck_v2_incident_severity"),
    )
