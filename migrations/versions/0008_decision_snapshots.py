"""add decision snapshot history table

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-14
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "decision_snapshots",
        sa.Column("decision_snapshot_id", sa.String(length=36), nullable=False),
        sa.Column("paper_run_id", sa.String(length=36), nullable=False),
        sa.Column("symbol", sa.String(length=30), nullable=False),
        sa.Column("action", sa.String(length=60), nullable=False),
        sa.Column("pipeline_status", sa.String(length=60), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("decision_trace", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("cycle_time", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["paper_run_id"], ["paper_runs.paper_run_id"]),
        sa.PrimaryKeyConstraint("decision_snapshot_id"),
    )
    op.create_index(op.f("ix_decision_snapshots_paper_run_id"), "decision_snapshots", ["paper_run_id"])
    op.create_index(op.f("ix_decision_snapshots_symbol"), "decision_snapshots", ["symbol"])
    op.create_index(op.f("ix_decision_snapshots_pipeline_status"), "decision_snapshots", ["pipeline_status"])
    op.create_index(op.f("ix_decision_snapshots_cycle_time"), "decision_snapshots", ["cycle_time"])


def downgrade() -> None:
    op.drop_index(op.f("ix_decision_snapshots_cycle_time"), table_name="decision_snapshots")
    op.drop_index(op.f("ix_decision_snapshots_pipeline_status"), table_name="decision_snapshots")
    op.drop_index(op.f("ix_decision_snapshots_symbol"), table_name="decision_snapshots")
    op.drop_index(op.f("ix_decision_snapshots_paper_run_id"), table_name="decision_snapshots")
    op.drop_table("decision_snapshots")
