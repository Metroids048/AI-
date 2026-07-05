"""add validation memory and gateway runtime tables

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-04
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    is_sqlite = op.get_bind().dialect.name == "sqlite"

    op.create_table(
        "hypotheses",
        sa.Column("hypothesis_id", sa.String(length=36), nullable=False),
        sa.Column("strategy_id", sa.String(length=36), nullable=True),
        sa.Column("idea_id", sa.String(length=36), nullable=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("benchmark_plan", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("acceptance_criteria", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="draft"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["idea_id"], ["strategy_ideas.idea_id"]),
        sa.ForeignKeyConstraint(["strategy_id"], ["strategies.id"]),
        sa.PrimaryKeyConstraint("hypothesis_id"),
    )
    op.create_index(op.f("ix_hypotheses_idea_id"), "hypotheses", ["idea_id"], unique=False)
    op.create_index(op.f("ix_hypotheses_strategy_id"), "hypotheses", ["strategy_id"], unique=False)

    op.create_table(
        "decision_memory_entries",
        sa.Column("decision_memory_id", sa.String(length=36), nullable=False),
        sa.Column("scope_type", sa.String(length=40), nullable=False),
        sa.Column("scope_id", sa.String(length=36), nullable=False),
        sa.Column("decision_type", sa.String(length=60), nullable=False),
        sa.Column("verdict", sa.String(length=30), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("tags", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("evidence_refs", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("context_payload", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("decision_memory_id"),
    )
    op.create_index(
        op.f("ix_decision_memory_entries_scope_type"),
        "decision_memory_entries",
        ["scope_type"],
        unique=False,
    )
    op.create_index(op.f("ix_decision_memory_entries_scope_id"), "decision_memory_entries", ["scope_id"], unique=False)
    op.create_index(
        op.f("ix_decision_memory_entries_decision_type"),
        "decision_memory_entries",
        ["decision_type"],
        unique=False,
    )
    op.create_index(op.f("ix_decision_memory_entries_verdict"), "decision_memory_entries", ["verdict"], unique=False)
    op.create_index(
        op.f("ix_decision_memory_entries_created_at"),
        "decision_memory_entries",
        ["created_at"],
        unique=False,
    )

    op.add_column("agent_tasks", sa.Column("executor_name", sa.String(length=80), nullable=True))
    op.add_column("agent_tasks", sa.Column("attempt_history", sa.JSON(), nullable=False, server_default="[]"))
    op.add_column("agent_tasks", sa.Column("provider_trace", sa.JSON(), nullable=False, server_default="{}"))
    op.add_column("agent_tasks", sa.Column("schema_validation_status", sa.String(length=30), nullable=True))

    op.add_column(
        "live_runs",
        sa.Column("validation_backtest_run_id", sa.String(length=36), nullable=True),
    )
    op.create_index(
        op.f("ix_live_runs_validation_backtest_run_id"),
        "live_runs",
        ["validation_backtest_run_id"],
        unique=False,
    )
    if not is_sqlite:
        op.create_foreign_key(
            "fk_live_runs_validation_backtest_run_id",
            "live_runs",
            "backtest_runs",
            ["validation_backtest_run_id"],
            ["backtest_run_id"],
        )

    op.create_table(
        "exchange_account_snapshots",
        sa.Column("snapshot_id", sa.String(length=36), nullable=False),
        sa.Column("live_run_id", sa.String(length=36), nullable=False),
        sa.Column("exchange", sa.String(length=30), nullable=False),
        sa.Column("wallet_balance", sa.Float(), nullable=False),
        sa.Column("available_balance", sa.Float(), nullable=False),
        sa.Column("margin_balance", sa.Float(), nullable=False),
        sa.Column("unrealized_pnl", sa.Float(), nullable=False, server_default="0"),
        sa.Column("open_position_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("source_ref", sa.String(length=255), nullable=True),
        sa.Column("snapshot_time", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["live_run_id"], ["live_runs.live_run_id"]),
        sa.PrimaryKeyConstraint("snapshot_id"),
    )
    op.create_index(
        op.f("ix_exchange_account_snapshots_live_run_id"),
        "exchange_account_snapshots",
        ["live_run_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_exchange_account_snapshots_snapshot_time"),
        "exchange_account_snapshots",
        ["snapshot_time"],
        unique=False,
    )

    op.create_table(
        "reconciliation_records",
        sa.Column("reconciliation_id", sa.String(length=36), nullable=False),
        sa.Column("live_run_id", sa.String(length=36), nullable=False),
        sa.Column("reconciliation_status", sa.String(length=30), nullable=False, server_default="ok"),
        sa.Column("open_order_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("position_mismatches", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("notes", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["live_run_id"], ["live_runs.live_run_id"]),
        sa.PrimaryKeyConstraint("reconciliation_id"),
    )
    op.create_index(
        op.f("ix_reconciliation_records_live_run_id"),
        "reconciliation_records",
        ["live_run_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_reconciliation_records_reconciliation_status"),
        "reconciliation_records",
        ["reconciliation_status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_reconciliation_records_created_at"),
        "reconciliation_records",
        ["created_at"],
        unique=False,
    )

    op.add_column("order_executions", sa.Column("gateway_name", sa.String(length=80), nullable=True))
    op.add_column("order_executions", sa.Column("gateway_order_id", sa.String(length=120), nullable=True))
    op.add_column("order_executions", sa.Column("gateway_status", sa.String(length=30), nullable=True))
    op.add_column("order_executions", sa.Column("lifecycle_history", sa.JSON(), nullable=False, server_default="[]"))
    op.add_column("order_executions", sa.Column("reconciliation_status", sa.String(length=30), nullable=True))
    op.add_column("order_executions", sa.Column("last_gateway_update_at", sa.DateTime(), nullable=True))
    op.create_index(
        op.f("ix_order_executions_gateway_order_id"),
        "order_executions",
        ["gateway_order_id"],
        unique=False,
    )
    op.create_index(op.f("ix_order_executions_gateway_status"), "order_executions", ["gateway_status"], unique=False)


def downgrade() -> None:
    is_sqlite = op.get_bind().dialect.name == "sqlite"

    op.drop_index(op.f("ix_order_executions_gateway_status"), table_name="order_executions")
    op.drop_index(op.f("ix_order_executions_gateway_order_id"), table_name="order_executions")
    op.drop_column("order_executions", "last_gateway_update_at")
    op.drop_column("order_executions", "reconciliation_status")
    op.drop_column("order_executions", "lifecycle_history")
    op.drop_column("order_executions", "gateway_status")
    op.drop_column("order_executions", "gateway_order_id")
    op.drop_column("order_executions", "gateway_name")

    op.drop_index(op.f("ix_reconciliation_records_created_at"), table_name="reconciliation_records")
    op.drop_index(op.f("ix_reconciliation_records_reconciliation_status"), table_name="reconciliation_records")
    op.drop_index(op.f("ix_reconciliation_records_live_run_id"), table_name="reconciliation_records")
    op.drop_table("reconciliation_records")

    op.drop_index(op.f("ix_exchange_account_snapshots_snapshot_time"), table_name="exchange_account_snapshots")
    op.drop_index(op.f("ix_exchange_account_snapshots_live_run_id"), table_name="exchange_account_snapshots")
    op.drop_table("exchange_account_snapshots")

    if not is_sqlite:
        op.drop_constraint("fk_live_runs_validation_backtest_run_id", "live_runs", type_="foreignkey")
    op.drop_index(op.f("ix_live_runs_validation_backtest_run_id"), table_name="live_runs")
    op.drop_column("live_runs", "validation_backtest_run_id")

    op.drop_column("agent_tasks", "schema_validation_status")
    op.drop_column("agent_tasks", "provider_trace")
    op.drop_column("agent_tasks", "attempt_history")
    op.drop_column("agent_tasks", "executor_name")

    op.drop_index(op.f("ix_decision_memory_entries_created_at"), table_name="decision_memory_entries")
    op.drop_index(op.f("ix_decision_memory_entries_verdict"), table_name="decision_memory_entries")
    op.drop_index(op.f("ix_decision_memory_entries_decision_type"), table_name="decision_memory_entries")
    op.drop_index(op.f("ix_decision_memory_entries_scope_id"), table_name="decision_memory_entries")
    op.drop_index(op.f("ix_decision_memory_entries_scope_type"), table_name="decision_memory_entries")
    op.drop_table("decision_memory_entries")

    op.drop_index(op.f("ix_hypotheses_strategy_id"), table_name="hypotheses")
    op.drop_index(op.f("ix_hypotheses_idea_id"), table_name="hypotheses")
    op.drop_table("hypotheses")
