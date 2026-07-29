"""Gate 1: V2 database integrity — fills, FKs, runtime controls, forced uniqueness.

Revision ID: 0019
Revises: 0018
Create Date: 2026-07-29
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "v2_execution_decisions",
        sa.Column("decision_id", sa.String(36), nullable=False),
        sa.Column("cycle_id", sa.String(36), nullable=False),
        sa.Column("candidate_key", sa.String(100), nullable=True),
        sa.Column("terminal_reason", sa.String(100), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["cycle_id"], ["v2_execution_cycles.cycle_id"]),
        sa.PrimaryKeyConstraint("decision_id"),
    )
    op.create_index("ix_v2_execution_decisions_cycle_id", "v2_execution_decisions", ["cycle_id"])

    with op.batch_alter_table("v2_execution_intents") as batch:
        batch.add_column(sa.Column("decision_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("version", sa.Integer(), nullable=False, server_default="0"))
        batch.create_foreign_key(
            "fk_v2_intent_cycle",
            "v2_execution_cycles",
            ["cycle_id"],
            ["cycle_id"],
        )
        batch.create_foreign_key(
            "fk_v2_intent_decision",
            "v2_execution_decisions",
            ["decision_id"],
            ["decision_id"],
        )
        batch.create_index("ix_v2_execution_intents_decision_id", ["decision_id"])

    # Replace non-unique exchange_order_id index with unique constraint
    op.drop_index("ix_v2_exchange_orders_exchange_order_id", table_name="v2_exchange_orders")
    with op.batch_alter_table("v2_exchange_orders") as batch:
        batch.create_foreign_key(
            "fk_v2_order_intent",
            "v2_execution_intents",
            ["intent_id"],
            ["intent_id"],
        )
        batch.create_unique_constraint("uq_v2_exchange_order_id", ["exchange_order_id"])
        batch.create_index("ix_v2_exchange_orders_exchange_order_id", ["exchange_order_id"])

    op.create_table(
        "v2_exchange_fills",
        sa.Column("fill_id", sa.String(36), nullable=False),
        sa.Column("intent_id", sa.String(36), nullable=False),
        sa.Column("exchange_order_record_id", sa.String(36), nullable=False),
        sa.Column("account_id", sa.String(64), nullable=False),
        sa.Column("exchange_order_id", sa.String(100), nullable=False),
        sa.Column("trade_id", sa.String(100), nullable=False),
        sa.Column("symbol", sa.String(20), nullable=False),
        sa.Column("side", sa.String(10), nullable=False),
        sa.Column("reduce_only", sa.Boolean(), nullable=False),
        sa.Column("filled_quantity", sa.Numeric(38, 18), nullable=False),
        sa.Column("fill_price", sa.Numeric(38, 18), nullable=False),
        sa.Column("commission", sa.Numeric(38, 18), nullable=True),
        sa.Column("commission_asset", sa.String(20), nullable=True),
        sa.Column("exchange_event_time", sa.DateTime(), nullable=False),
        sa.Column("received_at", sa.DateTime(), nullable=False),
        sa.Column("raw_hash", sa.String(128), nullable=False),
        sa.CheckConstraint("filled_quantity > 0", name="ck_v2_fill_qty_positive"),
        sa.CheckConstraint("fill_price > 0", name="ck_v2_fill_price_positive"),
        sa.ForeignKeyConstraint(["intent_id"], ["v2_execution_intents.intent_id"]),
        sa.ForeignKeyConstraint(
            ["exchange_order_record_id"],
            ["v2_exchange_orders.order_record_id"],
        ),
        sa.PrimaryKeyConstraint("fill_id"),
        sa.UniqueConstraint("account_id", "trade_id", name="uq_v2_fill_account_trade"),
    )
    op.create_index("ix_v2_exchange_fills_intent_id", "v2_exchange_fills", ["intent_id"])
    op.create_index(
        "ix_v2_exchange_fills_exchange_order_record_id",
        "v2_exchange_fills",
        ["exchange_order_record_id"],
    )

    with op.batch_alter_table("v2_managed_positions") as batch:
        batch.add_column(sa.Column("version", sa.Integer(), nullable=False, server_default="0"))
        batch.create_foreign_key(
            "fk_v2_position_intent",
            "v2_execution_intents",
            ["intent_id"],
            ["intent_id"],
        )
        batch.create_foreign_key(
            "fk_v2_position_order",
            "v2_exchange_orders",
            ["order_record_id"],
            ["order_record_id"],
        )

    op.drop_index(
        "ix_v2_position_one_open_per_symbol_direction_mode",
        table_name="v2_managed_positions",
    )
    op.create_index(
        "ix_v2_position_one_open_per_symbol_direction_mode",
        "v2_managed_positions",
        ["symbol", "direction", "execution_mode"],
        unique=True,
        sqlite_where=sa.text("state NOT IN ('CLOSED', 'QUARANTINED')"),
    )

    with op.batch_alter_table("v2_protection_records") as batch:
        batch.add_column(sa.Column("version", sa.Integer(), nullable=False, server_default="0"))
        batch.create_foreign_key(
            "fk_v2_protection_position",
            "v2_managed_positions",
            ["position_id"],
            ["position_id"],
        )

    # Rebuild reconciliation snapshots: add cycle_id FK + RECOVERY_REQUIRED status
    op.execute("PRAGMA foreign_keys=OFF")
    op.execute(
        """
        CREATE TABLE v2_reconciliation_snapshots__new (
            snapshot_id VARCHAR(36) NOT NULL,
            cycle_id VARCHAR(36),
            execution_mode VARCHAR(30) NOT NULL,
            exchange_positions JSON NOT NULL,
            exchange_open_orders JSON NOT NULL,
            local_positions JSON NOT NULL,
            discrepancies JSON,
            status VARCHAR(30) NOT NULL,
            captured_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
            PRIMARY KEY (snapshot_id),
            FOREIGN KEY(cycle_id) REFERENCES v2_execution_cycles (cycle_id),
            CONSTRAINT ck_v2_recon_status CHECK (
                status IN (
                    'HEALTHY', 'DEGRADED', 'UNAVAILABLE',
                    'RECOVERY_REQUIRED', 'QUARANTINED'
                )
            )
        )
        """
    )
    op.execute(
        """
        INSERT INTO v2_reconciliation_snapshots__new
            (snapshot_id, cycle_id, execution_mode, exchange_positions,
             exchange_open_orders, local_positions, discrepancies, status, captured_at)
        SELECT snapshot_id, NULL, execution_mode, exchange_positions,
               exchange_open_orders, local_positions, discrepancies, status, captured_at
        FROM v2_reconciliation_snapshots
        """
    )
    op.execute("DROP TABLE v2_reconciliation_snapshots")
    op.execute("ALTER TABLE v2_reconciliation_snapshots__new RENAME TO v2_reconciliation_snapshots")
    op.create_index(
        "ix_v2_reconciliation_snapshots_captured_at",
        "v2_reconciliation_snapshots",
        ["captured_at"],
    )
    op.create_index(
        "ix_v2_reconciliation_snapshots_cycle_id",
        "v2_reconciliation_snapshots",
        ["cycle_id"],
    )
    op.execute("PRAGMA foreign_keys=ON")

    with op.batch_alter_table("v2_execution_incidents") as batch:
        batch.add_column(sa.Column("intent_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("position_id", sa.String(36), nullable=True))
        batch.create_foreign_key(
            "fk_v2_incident_intent",
            "v2_execution_intents",
            ["intent_id"],
            ["intent_id"],
        )
        batch.create_foreign_key(
            "fk_v2_incident_position",
            "v2_managed_positions",
            ["position_id"],
            ["position_id"],
        )
        batch.create_index("ix_v2_execution_incidents_intent_id", ["intent_id"])
        batch.create_index("ix_v2_execution_incidents_position_id", ["position_id"])

    op.create_table(
        "v2_runtime_controls",
        sa.Column("scope", sa.String(64), nullable=False),
        sa.Column("entry_enabled", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("updated_by", sa.String(100), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("scope"),
    )
    op.execute(
        """
        INSERT INTO v2_runtime_controls (scope, entry_enabled, reason, updated_by, version)
        VALUES ('global', 0, 'default_disabled_until_gate5', 'migration_0019', 0)
        """
    )


def downgrade() -> None:
    op.drop_table("v2_runtime_controls")
    op.drop_table("v2_exchange_fills")
    op.drop_table("v2_execution_decisions")
