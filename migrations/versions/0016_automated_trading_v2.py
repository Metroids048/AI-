"""Add V2 automated trading execution tables.

Revision ID: 0016
Revises: 0015
Create Date: 2026-07-28
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # V2 Execution Cycles
    op.create_table(
        "v2_execution_cycles",
        sa.Column("cycle_id", sa.String(36), nullable=False),
        sa.Column("symbol", sa.String(20), nullable=False),
        sa.Column("timeframe", sa.String(10), nullable=False),
        sa.Column("bar_timestamp", sa.DateTime(), nullable=False),
        sa.Column("execution_mode", sa.String(30), nullable=False),
        sa.Column("fencing_token", sa.String(100), nullable=False),
        sa.Column("started_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("decision_terminal", sa.String(50), nullable=True),
        sa.CheckConstraint(
            "execution_mode IN ('BINANCE_TESTNET', 'LOCAL_PAPER')",
            name="ck_v2_cycle_execution_mode",
        ),
        sa.PrimaryKeyConstraint("cycle_id"),
        sa.UniqueConstraint("fencing_token"),
    )
    op.create_index("ix_v2_cycle_symbol_bar", "v2_execution_cycles", ["symbol", "bar_timestamp"])
    op.create_index(op.f("ix_v2_execution_cycles_bar_timestamp"), "v2_execution_cycles", ["bar_timestamp"])
    op.create_index(op.f("ix_v2_execution_cycles_symbol"), "v2_execution_cycles", ["symbol"])

    # V2 Execution Intents
    op.create_table(
        "v2_execution_intents",
        sa.Column("intent_id", sa.String(36), nullable=False),
        sa.Column("cycle_id", sa.String(36), nullable=False),
        sa.Column("symbol", sa.String(20), nullable=False),
        sa.Column("direction", sa.String(10), nullable=False),
        sa.Column("candidate_key", sa.String(100), nullable=False),
        sa.Column("candidate_type", sa.String(30), nullable=False),
        sa.Column("execution_mode", sa.String(30), nullable=False),
        sa.Column("decision_bar_timestamp", sa.DateTime(), nullable=False),
        sa.Column("decision_funnel_id", sa.String(36), nullable=True),
        sa.Column("state", sa.String(30), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.CheckConstraint("direction IN ('long', 'short')", name="ck_v2_intent_direction"),
        sa.CheckConstraint(
            "candidate_type IN ('PRIMARY', 'SAMPLING', 'RESEARCH')",
            name="ck_v2_intent_candidate_type",
        ),
        sa.CheckConstraint(
            "execution_mode IN ('BINANCE_TESTNET', 'LOCAL_PAPER')",
            name="ck_v2_intent_execution_mode",
        ),
        sa.CheckConstraint(
            "state IN ('INTENT_CREATED', 'EXCHANGE_SUBMITTING', 'EXCHANGE_ACKNOWLEDGED', "
            "'FILLED', 'REJECTED', 'CANCELLED', 'EXPIRED')",
            name="ck_v2_intent_state",
        ),
        sa.PrimaryKeyConstraint("intent_id"),
    )
    op.create_index(op.f("ix_v2_execution_intents_cycle_id"), "v2_execution_intents", ["cycle_id"])
    op.create_index(op.f("ix_v2_execution_intents_state"), "v2_execution_intents", ["state"])
    op.create_index(op.f("ix_v2_execution_intents_symbol"), "v2_execution_intents", ["symbol"])

    # V2 Exchange Orders
    op.create_table(
        "v2_exchange_orders",
        sa.Column("order_record_id", sa.String(36), nullable=False),
        sa.Column("intent_id", sa.String(36), nullable=False),
        sa.Column("client_order_id", sa.String(100), nullable=False),
        sa.Column("exchange_order_id", sa.String(100), nullable=True),
        sa.Column("quantity", sa.Numeric(20, 8), nullable=False),
        sa.Column("leverage", sa.Integer(), nullable=False),
        sa.Column("submitted_at", sa.DateTime(), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(), nullable=True),
        sa.Column("filled_quantity", sa.Numeric(20, 8), nullable=True),
        sa.Column("average_fill_price", sa.Numeric(20, 4), nullable=True),
        sa.Column("total_fee", sa.Numeric(20, 8), nullable=True),
        sa.Column("fill_timestamp", sa.DateTime(), nullable=True),
        sa.Column("trade_ids", sa.JSON(), nullable=False),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.CheckConstraint("quantity > 0", name="ck_v2_order_quantity_positive"),
        sa.CheckConstraint("leverage > 0", name="ck_v2_order_leverage_positive"),
        sa.CheckConstraint(
            "filled_quantity IS NULL OR filled_quantity > 0",
            name="ck_v2_order_filled_quantity_positive",
        ),
        sa.CheckConstraint(
            "average_fill_price IS NULL OR average_fill_price > 0",
            name="ck_v2_order_fill_price_positive",
        ),
        sa.PrimaryKeyConstraint("order_record_id"),
        sa.UniqueConstraint("client_order_id"),
    )
    op.create_index(op.f("ix_v2_exchange_orders_exchange_order_id"), "v2_exchange_orders", ["exchange_order_id"])
    op.create_index(op.f("ix_v2_exchange_orders_intent_id"), "v2_exchange_orders", ["intent_id"])

    # V2 Managed Positions
    op.create_table(
        "v2_managed_positions",
        sa.Column("position_id", sa.String(36), nullable=False),
        sa.Column("intent_id", sa.String(36), nullable=False),
        sa.Column("order_record_id", sa.String(36), nullable=False),
        sa.Column("symbol", sa.String(20), nullable=False),
        sa.Column("direction", sa.String(10), nullable=False),
        sa.Column("execution_mode", sa.String(30), nullable=False),
        sa.Column("quantity", sa.Numeric(20, 8), nullable=False),
        sa.Column("entry_price", sa.Numeric(20, 4), nullable=False),
        sa.Column("entry_fee", sa.Numeric(20, 8), nullable=False),
        sa.Column("state", sa.String(30), nullable=False),
        sa.Column("projected_at", sa.DateTime(), nullable=False),
        sa.Column("protected_at", sa.DateTime(), nullable=True),
        sa.Column("closed_at", sa.DateTime(), nullable=True),
        sa.Column("realized_pnl", sa.Numeric(20, 4), nullable=True),
        sa.CheckConstraint("direction IN ('long', 'short')", name="ck_v2_position_direction"),
        sa.CheckConstraint("quantity > 0", name="ck_v2_position_quantity_positive"),
        sa.CheckConstraint("entry_price > 0", name="ck_v2_position_entry_price_positive"),
        sa.CheckConstraint(
            "state IN ('POSITION_PROJECTED', 'PROTECTED', 'REDUCING', 'CLOSED', 'QUARANTINED')",
            name="ck_v2_position_state",
        ),
        sa.PrimaryKeyConstraint("position_id"),
        sa.UniqueConstraint("intent_id"),
    )
    op.create_index(op.f("ix_v2_managed_positions_state"), "v2_managed_positions", ["state"])
    op.create_index(op.f("ix_v2_managed_positions_symbol"), "v2_managed_positions", ["symbol"])

    # Partial unique index: only one open position per (symbol, direction, execution_mode)
    # when state NOT IN ('CLOSED', 'QUARANTINED')
    op.create_index(
        "ix_v2_position_one_open_per_symbol_direction_mode",
        "v2_managed_positions",
        ["symbol", "direction", "execution_mode", "state"],
        unique=True,
        sqlite_where=sa.text("state NOT IN ('CLOSED', 'QUARANTINED')"),
    )

    # V2 Protection Records
    op.create_table(
        "v2_protection_records",
        sa.Column("protection_id", sa.String(36), nullable=False),
        sa.Column("position_id", sa.String(36), nullable=False),
        sa.Column("stop_loss_price", sa.Numeric(20, 4), nullable=False),
        sa.Column("take_profit_price", sa.Numeric(20, 4), nullable=True),
        sa.Column("stop_client_order_id", sa.String(100), nullable=False),
        sa.Column("tp_client_order_id", sa.String(100), nullable=True),
        sa.Column("stop_exchange_order_id", sa.String(100), nullable=True),
        sa.Column("tp_exchange_order_id", sa.String(100), nullable=True),
        sa.Column("state", sa.String(30), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("activated_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint("stop_loss_price > 0", name="ck_v2_protection_stop_price_positive"),
        sa.CheckConstraint(
            "take_profit_price IS NULL OR take_profit_price > 0",
            name="ck_v2_protection_tp_price_positive",
        ),
        sa.CheckConstraint(
            "state IN ('PROTECTION_INTENT', 'PROTECTION_SUBMITTING', 'PROTECTION_ACTIVE', "
            "'PROTECTION_TRIGGERED', 'PROTECTION_FILLED', 'PROTECTION_CANCELLED')",
            name="ck_v2_protection_state",
        ),
        sa.PrimaryKeyConstraint("protection_id"),
        sa.UniqueConstraint("stop_client_order_id"),
        sa.UniqueConstraint("tp_client_order_id"),
    )
    op.create_index(op.f("ix_v2_protection_records_position_id"), "v2_protection_records", ["position_id"])
    op.create_index(op.f("ix_v2_protection_records_state"), "v2_protection_records", ["state"])

    # V2 Execution Events (append-only event log)
    op.create_table(
        "v2_execution_events",
        sa.Column("event_id", sa.String(36), nullable=False),
        sa.Column("aggregate_id", sa.String(36), nullable=False),
        sa.Column("aggregate_type", sa.String(30), nullable=False),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("event_payload", sa.JSON(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.CheckConstraint(
            "aggregate_type IN ('CYCLE', 'INTENT', 'ORDER', 'POSITION', 'PROTECTION')",
            name="ck_v2_event_aggregate_type",
        ),
        sa.PrimaryKeyConstraint("event_id"),
    )
    op.create_index("ix_v2_event_aggregate_occurred", "v2_execution_events", ["aggregate_id", "occurred_at"])
    op.create_index(op.f("ix_v2_execution_events_aggregate_id"), "v2_execution_events", ["aggregate_id"])
    op.create_index(op.f("ix_v2_execution_events_occurred_at"), "v2_execution_events", ["occurred_at"])

    # V2 Reconciliation Snapshots
    op.create_table(
        "v2_reconciliation_snapshots",
        sa.Column("snapshot_id", sa.String(36), nullable=False),
        sa.Column("execution_mode", sa.String(30), nullable=False),
        sa.Column("exchange_positions", sa.JSON(), nullable=False),
        sa.Column("exchange_open_orders", sa.JSON(), nullable=False),
        sa.Column("local_positions", sa.JSON(), nullable=False),
        sa.Column("discrepancies", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("captured_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.CheckConstraint(
            "status IN ('HEALTHY', 'DEGRADED', 'UNAVAILABLE', 'QUARANTINED')",
            name="ck_v2_recon_status",
        ),
        sa.PrimaryKeyConstraint("snapshot_id"),
    )
    op.create_index(op.f("ix_v2_reconciliation_snapshots_captured_at"), "v2_reconciliation_snapshots", ["captured_at"])

    # V2 Execution Incidents
    op.create_table(
        "v2_execution_incidents",
        sa.Column("incident_id", sa.String(36), nullable=False),
        sa.Column("incident_type", sa.String(50), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("related_aggregate_id", sa.String(36), nullable=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("context", sa.JSON(), nullable=False),
        sa.Column("resolved", sa.Boolean(), nullable=False, default=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint("severity IN ('CRITICAL', 'HIGH', 'MEDIUM', 'LOW')", name="ck_v2_incident_severity"),
        sa.PrimaryKeyConstraint("incident_id"),
    )
    op.create_index(op.f("ix_v2_execution_incidents_created_at"), "v2_execution_incidents", ["created_at"])
    op.create_index(op.f("ix_v2_execution_incidents_incident_type"), "v2_execution_incidents", ["incident_type"])
    op.create_index(
        op.f("ix_v2_execution_incidents_related_aggregate_id"),
        "v2_execution_incidents",
        ["related_aggregate_id"],
    )
    op.create_index(op.f("ix_v2_execution_incidents_severity"), "v2_execution_incidents", ["severity"])


def downgrade() -> None:
    op.drop_table("v2_execution_incidents")
    op.drop_table("v2_reconciliation_snapshots")
    op.drop_table("v2_execution_events")
    op.drop_table("v2_protection_records")
    op.drop_table("v2_managed_positions")
    op.drop_table("v2_exchange_orders")
    op.drop_table("v2_execution_intents")
    op.drop_table("v2_execution_cycles")
