"""V2 database models for Exchange-First execution persistence.

Tables:
- v2_execution_cycles
- v2_execution_decisions
- v2_execution_intents
- v2_exchange_orders
- v2_exchange_fills
- v2_managed_positions
- v2_protection_records
- v2_execution_events
- v2_reconciliation_snapshots
- v2_execution_incidents
- v2_runtime_controls
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
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


class V2ExecutionDecision(Base):
    """Persisted strategy/decision node for a cycle."""

    __tablename__ = "v2_execution_decisions"

    decision_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    cycle_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("v2_execution_cycles.cycle_id"), nullable=False, index=True
    )
    candidate_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    terminal_reason: Mapped[str | None] = mapped_column(String(100), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())


class V2ExecutionIntent(Base):
    """Entry intent created from strategy decision."""

    __tablename__ = "v2_execution_intents"

    intent_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    cycle_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("v2_execution_cycles.cycle_id"), nullable=False, index=True
    )
    decision_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("v2_execution_decisions.decision_id"), nullable=True, index=True
    )
    symbol: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    direction: Mapped[str] = mapped_column(String(10), nullable=False)
    candidate_key: Mapped[str] = mapped_column(String(100), nullable=False)
    candidate_type: Mapped[str] = mapped_column(String(30), nullable=False)
    execution_mode: Mapped[str] = mapped_column(String(30), nullable=False)
    decision_bar_timestamp: Mapped[datetime] = mapped_column(nullable=False)
    decision_funnel_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    state: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
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
            "state IN ('INTENT_CREATED', 'EXCHANGE_SUBMITTING', 'EXCHANGE_UNKNOWN', "
            "'EXCHANGE_ACKNOWLEDGED', 'FILLED', 'REJECTED', 'CANCELLED', 'EXPIRED')",
            name="ck_v2_intent_state",
        ),
    )


class V2ExchangeOrder(Base):
    """Exchange order facts with aggregate fill cache."""

    __tablename__ = "v2_exchange_orders"

    order_record_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    intent_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("v2_execution_intents.intent_id"), nullable=False, index=True
    )
    client_order_id: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    exchange_order_id: Mapped[str | None] = mapped_column(String(100), nullable=True, unique=True, index=True)
    quantity: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    leverage: Mapped[int] = mapped_column(Integer, nullable=False)
    submitted_at: Mapped[datetime | None] = mapped_column(nullable=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(nullable=True)
    filled_quantity: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    average_fill_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 4), nullable=True)
    total_fee: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    fill_timestamp: Mapped[datetime | None] = mapped_column(nullable=True)
    trade_ids: Mapped[dict] = mapped_column(JSON, default=dict)
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


class V2ExchangeFill(Base):
    """Per-trade exchange fill fact. UNIQUE(account_id, trade_id)."""

    __tablename__ = "v2_exchange_fills"

    fill_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    intent_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("v2_execution_intents.intent_id"), nullable=False, index=True
    )
    exchange_order_record_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("v2_exchange_orders.order_record_id"), nullable=False, index=True
    )
    account_id: Mapped[str] = mapped_column(String(64), nullable=False)
    exchange_order_id: Mapped[str] = mapped_column(String(100), nullable=False)
    trade_id: Mapped[str] = mapped_column(String(100), nullable=False)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    side: Mapped[str] = mapped_column(String(10), nullable=False)
    reduce_only: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    filled_quantity: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    fill_price: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    commission: Mapped[Decimal | None] = mapped_column(Numeric(38, 18), nullable=True)
    commission_asset: Mapped[str | None] = mapped_column(String(20), nullable=True)
    exchange_event_time: Mapped[datetime] = mapped_column(nullable=False)
    received_at: Mapped[datetime] = mapped_column(nullable=False)
    raw_hash: Mapped[str] = mapped_column(String(128), nullable=False)

    __table_args__ = (
        UniqueConstraint("account_id", "trade_id", name="uq_v2_fill_account_trade"),
        CheckConstraint("filled_quantity > 0", name="ck_v2_fill_qty_positive"),
        CheckConstraint("fill_price > 0", name="ck_v2_fill_price_positive"),
    )


class V2ManagedPosition(Base):
    """Managed position projection from confirmed exchange fills."""

    __tablename__ = "v2_managed_positions"

    position_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    intent_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("v2_execution_intents.intent_id"), nullable=False, unique=True
    )
    order_record_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("v2_exchange_orders.order_record_id"), nullable=False
    )
    symbol: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    direction: Mapped[str] = mapped_column(String(10), nullable=False)
    execution_mode: Mapped[str] = mapped_column(String(30), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    entry_price: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    entry_fee: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    state: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    projected_at: Mapped[datetime] = mapped_column(nullable=False)
    protected_at: Mapped[datetime | None] = mapped_column(nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    realized_pnl: Mapped[Decimal | None] = mapped_column(Numeric(20, 4), nullable=True)

    __table_args__ = (
        Index(
            "ix_v2_position_one_open_per_symbol_direction_mode",
            "symbol",
            "direction",
            "execution_mode",
            unique=True,
            sqlite_where=text("state NOT IN ('CLOSED', 'QUARANTINED')"),
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
    position_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("v2_managed_positions.position_id"), nullable=False, index=True
    )
    stop_loss_price: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    take_profit_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 4), nullable=True)
    stop_client_order_id: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    tp_client_order_id: Mapped[str | None] = mapped_column(String(100), nullable=True, unique=True)
    stop_exchange_order_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    tp_exchange_order_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    state: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
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
            "'PROTECTION_TRIGGERED', 'PROTECTION_FILLED', 'PROTECTION_CANCELLED', "
            "'PROTECTION_FAILED', 'PROTECTION_UNKNOWN')",
            name="ck_v2_protection_state",
        ),
        CheckConstraint(
            "state != 'PROTECTION_ACTIVE' OR "
            "(stop_exchange_order_id IS NOT NULL AND length(stop_exchange_order_id) > 0)",
            name="ck_v2_protection_active_requires_stop_xo",
        ),
    )


class V2ExecutionEvent(Base):
    """Append-only event log for state transitions."""

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
            "aggregate_type IN ('CYCLE', 'INTENT', 'ORDER', 'POSITION', 'PROTECTION', 'DECISION')",
            name="ck_v2_event_aggregate_type",
        ),
    )


class V2ReconciliationSnapshot(Base):
    """Exchange reconciliation snapshots for audit and recovery."""

    __tablename__ = "v2_reconciliation_snapshots"

    snapshot_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    cycle_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("v2_execution_cycles.cycle_id"), nullable=True, index=True
    )
    execution_mode: Mapped[str] = mapped_column(String(30), nullable=False)
    exchange_positions: Mapped[dict] = mapped_column(JSON, nullable=False)
    exchange_open_orders: Mapped[dict] = mapped_column(JSON, nullable=False)
    local_positions: Mapped[dict] = mapped_column(JSON, nullable=False)
    discrepancies: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now(), index=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('HEALTHY', 'DEGRADED', 'UNAVAILABLE', 'RECOVERY_REQUIRED', 'QUARANTINED')",
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
    intent_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("v2_execution_intents.intent_id"), nullable=True, index=True
    )
    position_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("v2_managed_positions.position_id"), nullable=True, index=True
    )
    description: Mapped[str] = mapped_column(Text, nullable=False)
    context: Mapped[dict] = mapped_column(JSON, default=dict)
    resolved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now(), index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(nullable=True)

    __table_args__ = (
        CheckConstraint("severity IN ('CRITICAL', 'HIGH', 'MEDIUM', 'LOW')", name="ck_v2_incident_severity"),
    )


class V2RuntimeControl(Base):
    """Persisted runtime control flags. entry_enabled defaults false until Gate 5."""

    __tablename__ = "v2_runtime_controls"

    scope: Mapped[str] = mapped_column(String(64), primary_key=True)
    entry_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now(), onupdate=func.now())
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
