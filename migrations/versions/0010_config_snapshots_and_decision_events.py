"""add immutable trading config snapshots and append-only decision events

Revision ID: 0010
Revises: 0009
Create Date: 2026-07-20
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "trading_config_snapshots" not in tables:
        op.create_table(
            "trading_config_snapshots",
            sa.Column("config_snapshot_id", sa.String(length=36), nullable=False),
            sa.Column("paper_run_id", sa.String(length=36), nullable=False),
            sa.Column("config_payload", sa.JSON(), nullable=False),
            sa.Column("config_hash", sa.String(length=80), nullable=False),
            sa.Column("created_by", sa.String(length=120), nullable=False),
            sa.Column("effective_cycle_id", sa.String(length=120), nullable=False),
            sa.Column("previous_snapshot_id", sa.String(length=36), nullable=True),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["paper_run_id"], ["paper_runs.paper_run_id"]),
            sa.PrimaryKeyConstraint("config_snapshot_id"),
            sa.UniqueConstraint("paper_run_id", "config_hash", name="uq_config_snapshot_run_hash"),
        )
        op.create_index("ix_trading_config_snapshots_paper_run_id", "trading_config_snapshots", ["paper_run_id"])
        op.create_index("ix_trading_config_snapshots_config_hash", "trading_config_snapshots", ["config_hash"])
        op.create_index(
            "ix_trading_config_snapshots_effective_cycle_id",
            "trading_config_snapshots",
            ["effective_cycle_id"],
        )

    if "decision_events" not in tables:
        op.create_table(
            "decision_events",
            sa.Column("event_id", sa.String(length=36), nullable=False),
            sa.Column("paper_run_id", sa.String(length=36), nullable=False),
            sa.Column("cycle_id", sa.String(length=120), nullable=False),
            sa.Column("decision_id", sa.String(length=120), nullable=False),
            sa.Column("event_type", sa.String(length=60), nullable=False),
            sa.Column("block_code", sa.String(length=80), nullable=True),
            sa.Column("strategy_id", sa.String(length=36), nullable=False),
            sa.Column("strategy_version", sa.String(length=120), nullable=False),
            sa.Column("config_snapshot_id", sa.String(length=36), nullable=False),
            sa.Column("config_hash", sa.String(length=80), nullable=False),
            sa.Column("symbol", sa.String(length=30), nullable=False),
            sa.Column("timeframe", sa.String(length=20), nullable=False),
            sa.Column("candle_close_time", sa.DateTime(), nullable=False),
            sa.Column("payload", sa.JSON(), nullable=False),
            sa.Column("decision_key", sa.String(length=220), nullable=True),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["paper_run_id"], ["paper_runs.paper_run_id"]),
            sa.PrimaryKeyConstraint("event_id"),
            sa.UniqueConstraint("decision_key"),
        )
        for column in (
            "paper_run_id",
            "cycle_id",
            "decision_id",
            "event_type",
            "block_code",
            "strategy_id",
            "config_snapshot_id",
            "config_hash",
            "symbol",
            "candle_close_time",
            "created_at",
        ):
            op.create_index(f"ix_decision_events_{column}", "decision_events", [column])

    paper_columns = {column["name"] for column in inspector.get_columns("paper_runs")}
    additions = (
        ("active_config_snapshot_id", sa.String(length=36)),
        ("active_config_hash", sa.String(length=80)),
        ("pending_config_snapshot_id", sa.String(length=36)),
        ("pending_config_hash", sa.String(length=80)),
    )
    with op.batch_alter_table("paper_runs") as batch_op:
        for name, column_type in additions:
            if name not in paper_columns:
                batch_op.add_column(sa.Column(name, column_type, nullable=True))
    refreshed_indexes = {index["name"] for index in sa.inspect(bind).get_indexes("paper_runs")}
    for column in ("active_config_snapshot_id", "pending_config_snapshot_id"):
        index_name = f"ix_paper_runs_{column}"
        if index_name not in refreshed_indexes:
            op.create_index(index_name, "paper_runs", [column])

    order_columns = {column["name"] for column in sa.inspect(bind).get_columns("order_executions")}
    order_additions = (
        ("intent_id", sa.String(length=120)),
        ("cycle_id", sa.String(length=120)),
        ("decision_id", sa.String(length=120)),
        ("config_snapshot_id", sa.String(length=36)),
        ("config_hash", sa.String(length=80)),
        ("normalized_order", sa.JSON()),
    )
    with op.batch_alter_table("order_executions") as batch_op:
        for name, column_type in order_additions:
            if name not in order_columns:
                batch_op.add_column(sa.Column(name, column_type, nullable=True))
    order_indexes = {index["name"] for index in sa.inspect(bind).get_indexes("order_executions")}
    for column in ("intent_id", "cycle_id", "decision_id", "config_snapshot_id", "config_hash"):
        index_name = f"ix_order_executions_{column}"
        if index_name not in order_indexes:
            op.create_index(index_name, "order_executions", [column])


def downgrade() -> None:
    # Audit facts are intentionally retained. A downgrade only moves the
    # Alembic revision marker; a forward upgrade can safely reuse the schema.
    pass
