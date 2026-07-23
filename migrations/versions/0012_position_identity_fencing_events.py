"""add position identity, lease fencing, and execution event fields

Revision ID: 0012
Revises: 0011
Create Date: 2026-07-23
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _column_names(bind, table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())

    if "position_records" not in tables:
        op.create_table(
            "position_records",
            sa.Column("position_record_id", sa.String(length=36), primary_key=True),
            sa.Column("exchange_account", sa.String(length=120), nullable=False),
            sa.Column("symbol", sa.String(length=30), nullable=False),
            sa.Column("position_side", sa.String(length=20), nullable=False),
            sa.Column("entry_order_id", sa.String(length=120), nullable=True),
            sa.Column("entry_fill_id", sa.String(length=120), nullable=True),
            sa.Column("opened_at", sa.DateTime(), nullable=False),
            sa.Column("quantity", sa.Float(), nullable=False),
            sa.Column("order_origin", sa.String(length=50), nullable=False),
            sa.Column("strategy_id", sa.String(length=36), nullable=True),
            sa.Column("run_id", sa.String(length=120), nullable=True),
            sa.Column("management_status", sa.String(length=50), nullable=False),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        )
        for column in (
            "exchange_account",
            "symbol",
            "position_side",
            "entry_order_id",
            "opened_at",
            "order_origin",
            "strategy_id",
            "run_id",
            "management_status",
        ):
            op.create_index(f"ix_position_records_{column}", "position_records", [column])

    if "protection_records" not in tables:
        op.create_table(
            "protection_records",
            sa.Column("protection_record_id", sa.String(length=36), primary_key=True),
            sa.Column(
                "position_record_id",
                sa.String(length=36),
                sa.ForeignKey("position_records.position_record_id"),
                nullable=False,
            ),
            sa.Column("stop_price", sa.Float(), nullable=True),
            sa.Column("take_profit_price", sa.Float(), nullable=True),
            sa.Column("protection_source", sa.String(length=50), nullable=False),
            sa.Column("status", sa.String(length=50), nullable=False),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        )
        op.create_index("ix_protection_records_position_record_id", "protection_records", ["position_record_id"])
        op.create_index("ix_protection_records_status", "protection_records", ["status"])

    order_columns = _column_names(bind, "order_executions")
    if "position_record_id" not in order_columns:
        with op.batch_alter_table("order_executions") as batch_op:
            batch_op.add_column(sa.Column("position_record_id", sa.String(length=36), nullable=True))
            batch_op.create_foreign_key(
                "fk_order_executions_position_record_id",
                "position_records",
                ["position_record_id"],
                ["position_record_id"],
            )
            batch_op.create_index("ix_order_executions_position_record_id", ["position_record_id"])

    position_columns = _column_names(bind, "position_snapshots")
    if "position_record_id" not in position_columns:
        with op.batch_alter_table("position_snapshots") as batch_op:
            batch_op.add_column(sa.Column("position_record_id", sa.String(length=36), nullable=True))
            batch_op.create_foreign_key(
                "fk_position_snapshots_position_record_id",
                "position_records",
                ["position_record_id"],
                ["position_record_id"],
            )
            batch_op.create_index("ix_position_snapshots_position_record_id", ["position_record_id"])

    lease_columns = _column_names(bind, "scheduler_leases")
    if "fencing_token" not in lease_columns:
        with op.batch_alter_table("scheduler_leases") as batch_op:
            batch_op.add_column(sa.Column("fencing_token", sa.Integer(), nullable=False, server_default="1"))

    cycle_columns = _column_names(bind, "scheduler_cycles")
    if "fencing_token" not in cycle_columns:
        with op.batch_alter_table("scheduler_cycles") as batch_op:
            batch_op.add_column(sa.Column("fencing_token", sa.Integer(), nullable=True))

    event_columns = _column_names(bind, "decision_events")
    event_additions = (
        ("run_id", sa.String(length=120), True),
        ("position_side", sa.String(length=20), False),
        ("order_origin", sa.String(length=50), False),
        ("position_record_id", sa.String(length=36), True),
        ("reason_code", sa.String(length=120), True),
    )
    with op.batch_alter_table("decision_events") as batch_op:
        for name, column_type, indexed in event_additions:
            if name not in event_columns:
                batch_op.add_column(sa.Column(name, column_type, nullable=True))
                if indexed:
                    batch_op.create_index(f"ix_decision_events_{name}", [name])


def downgrade() -> None:
    # Identity and fencing rows are execution evidence and are retained.
    pass
