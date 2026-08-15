"""Add immutable forward decision and Shadow evidence tables.

Revision ID: 0021
Revises: 0020
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "v2_decision_snapshots",
        sa.Column("snapshot_id", sa.String(length=36), nullable=False),
        sa.Column("cycle_id", sa.String(length=36), nullable=False),
        sa.Column("decision_id", sa.String(length=36), nullable=False),
        sa.Column("symbol", sa.String(length=20), nullable=False),
        sa.Column("decision_time", sa.DateTime(), nullable=False),
        sa.Column("snapshot_hash", sa.String(length=128), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["cycle_id"], ["v2_execution_cycles.cycle_id"]),
        sa.ForeignKeyConstraint(["decision_id"], ["v2_execution_decisions.decision_id"]),
        sa.PrimaryKeyConstraint("snapshot_id"),
        sa.UniqueConstraint("decision_id", name="uq_v2_decision_snapshot_decision"),
        sa.UniqueConstraint("snapshot_hash"),
    )
    op.create_index("ix_v2_decision_snapshots_cycle_id", "v2_decision_snapshots", ["cycle_id"])
    op.create_index("ix_v2_decision_snapshots_decision_id", "v2_decision_snapshots", ["decision_id"])
    op.create_index("ix_v2_decision_snapshots_symbol", "v2_decision_snapshots", ["symbol"])
    op.create_index("ix_v2_decision_snapshots_decision_time", "v2_decision_snapshots", ["decision_time"])

    op.create_table(
        "v2_shadow_records",
        sa.Column("shadow_id", sa.String(length=36), nullable=False),
        sa.Column("snapshot_id", sa.String(length=36), nullable=False),
        sa.Column("variant", sa.String(length=20), nullable=False),
        sa.Column("payload_hash", sa.String(length=128), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["snapshot_id"], ["v2_decision_snapshots.snapshot_id"]),
        sa.PrimaryKeyConstraint("shadow_id"),
        sa.UniqueConstraint("snapshot_id", "variant", name="uq_v2_shadow_snapshot_variant"),
    )
    op.create_index("ix_v2_shadow_records_snapshot_id", "v2_shadow_records", ["snapshot_id"])
    op.create_index("ix_v2_shadow_records_variant", "v2_shadow_records", ["variant"])

    op.create_table(
        "v2_shadow_outcomes",
        sa.Column("outcome_id", sa.String(length=36), nullable=False),
        sa.Column("shadow_id", sa.String(length=36), nullable=False),
        sa.Column("outcome_hash", sa.String(length=128), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["shadow_id"], ["v2_shadow_records.shadow_id"]),
        sa.PrimaryKeyConstraint("outcome_id"),
        sa.UniqueConstraint("shadow_id", name="uq_v2_shadow_outcome_shadow"),
        sa.UniqueConstraint("outcome_hash"),
    )
    op.create_index("ix_v2_shadow_outcomes_shadow_id", "v2_shadow_outcomes", ["shadow_id"])


def downgrade() -> None:
    op.drop_index("ix_v2_shadow_outcomes_shadow_id", table_name="v2_shadow_outcomes")
    op.drop_table("v2_shadow_outcomes")
    op.drop_index("ix_v2_shadow_records_variant", table_name="v2_shadow_records")
    op.drop_index("ix_v2_shadow_records_snapshot_id", table_name="v2_shadow_records")
    op.drop_table("v2_shadow_records")
    op.drop_index("ix_v2_decision_snapshots_decision_time", table_name="v2_decision_snapshots")
    op.drop_index("ix_v2_decision_snapshots_symbol", table_name="v2_decision_snapshots")
    op.drop_index("ix_v2_decision_snapshots_decision_id", table_name="v2_decision_snapshots")
    op.drop_index("ix_v2_decision_snapshots_cycle_id", table_name="v2_decision_snapshots")
    op.drop_table("v2_decision_snapshots")
