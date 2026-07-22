"""add scheduler coordination, order provenance, and candle intent uniqueness

Revision ID: 0011
Revises: 0010
Create Date: 2026-07-22
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "scheduler_leases" not in tables:
        op.create_table(
            "scheduler_leases",
            sa.Column("lease_name", sa.String(length=120), primary_key=True),
            sa.Column("owner_id", sa.String(length=120), nullable=False),
            sa.Column("hostname", sa.String(length=255), nullable=False),
            sa.Column("process_id", sa.Integer(), nullable=False),
            sa.Column("acquired_at", sa.DateTime(), nullable=False),
            sa.Column("heartbeat_at", sa.DateTime(), nullable=False),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_scheduler_leases_owner_id", "scheduler_leases", ["owner_id"])
        op.create_index("ix_scheduler_leases_expires_at", "scheduler_leases", ["expires_at"])

    if "scheduler_cycles" not in tables:
        op.create_table(
            "scheduler_cycles",
            sa.Column("scheduler_cycle_id", sa.String(length=36), primary_key=True),
            sa.Column("job_name", sa.String(length=120), nullable=False),
            sa.Column("scheduled_for", sa.DateTime(), nullable=False),
            sa.Column("scheduler_instance_id", sa.String(length=120), nullable=False),
            sa.Column("cycle_source", sa.String(length=50), nullable=False),
            sa.Column("run_mode", sa.String(length=50), nullable=False),
            sa.Column("deployment_sha", sa.String(length=120), nullable=True),
            sa.Column("hostname", sa.String(length=255), nullable=False),
            sa.Column("process_id", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(length=30), nullable=False),
            sa.Column("started_at", sa.DateTime(), nullable=False),
            sa.Column("completed_at", sa.DateTime(), nullable=True),
            sa.Column("failure_reason", sa.Text(), nullable=True),
            sa.UniqueConstraint("job_name", "scheduled_for", name="uq_scheduler_job_slot"),
        )
        for column in ("job_name", "scheduled_for", "scheduler_instance_id", "status"):
            op.create_index(f"ix_scheduler_cycles_{column}", "scheduler_cycles", [column])

    order_columns = {column["name"] for column in sa.inspect(bind).get_columns("order_executions")}
    additions = (
        ("intent_type", sa.String(length=30)),
        ("timeframe", sa.String(length=20)),
        ("signal_candle_close_time", sa.DateTime()),
        ("order_origin", sa.String(length=50), "unspecified"),
        ("run_mode", sa.String(length=50), "unspecified"),
        ("test_run_id", sa.String(length=120)),
        ("deployment_sha", sa.String(length=120)),
        ("scheduler_instance_id", sa.String(length=120)),
        ("process_id", sa.Integer()),
        ("worker_id", sa.String(length=120)),
        ("container_id", sa.String(length=120)),
        ("cycle_source", sa.String(length=50)),
        ("scheduled_for", sa.DateTime()),
    )
    with op.batch_alter_table("order_executions") as batch_op:
        for addition in additions:
            name, column_type, *default = addition
            if name not in order_columns:
                batch_op.add_column(
                    sa.Column(
                        name,
                        column_type,
                        nullable=not default,
                        server_default=default[0] if default else None,
                    )
                )

    inspector = sa.inspect(bind)
    existing_indexes = {index["name"] for index in inspector.get_indexes("order_executions")}
    for column in (
        "intent_type",
        "timeframe",
        "signal_candle_close_time",
        "order_origin",
        "run_mode",
        "test_run_id",
        "scheduler_instance_id",
        "scheduled_for",
    ):
        index_name = f"ix_order_executions_{column}"
        if index_name not in existing_indexes:
            op.create_index(index_name, "order_executions", [column])
    existing_unique = {constraint.get("name") for constraint in inspector.get_unique_constraints("order_executions")}
    if "uq_order_strategy_symbol_candle_intent" not in existing_unique:
        with op.batch_alter_table("order_executions") as batch_op:
            batch_op.create_unique_constraint(
                "uq_order_strategy_symbol_candle_intent",
                ["strategy_id", "symbol", "timeframe", "signal_candle_close_time", "intent_type"],
            )


def downgrade() -> None:
    # Runtime coordination and provenance are audit evidence and are retained.
    pass
