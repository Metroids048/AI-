"""Add immutable historical aggregate-exit adjudication records."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "v2_adjudication_cases",
        sa.Column("adjudication_id", sa.String(100), primary_key=True),
        sa.Column("case_key", sa.String(64), nullable=False, unique=True),
        sa.Column("exchange_account_identity", sa.String(200), nullable=False),
        sa.Column("manifest_hash", sa.String(128), nullable=False, unique=True),
        sa.Column("evidence_hash", sa.String(128), nullable=False),
        sa.Column("symbol", sa.String(20), nullable=False),
        sa.Column("exchange_order_id", sa.String(100), nullable=False),
        sa.Column("exchange_trade_id", sa.String(100), nullable=False),
        sa.Column("exchange_fill_quantity", sa.Numeric(38, 18), nullable=False),
        sa.Column("exchange_fill_side", sa.String(10), nullable=False),
        sa.Column("exchange_fill_price", sa.Numeric(38, 18), nullable=False),
        sa.Column("exchange_fill_timestamp", sa.DateTime(), nullable=False),
        sa.Column("operator_identity", sa.String(200), nullable=False),
        sa.Column("operator_reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("exchange_order_id", "exchange_trade_id", name="uq_v2_adjudication_exchange_trade"),
        sa.CheckConstraint("exchange_fill_quantity > 0", name="ck_v2_adjudication_quantity_positive"),
    )
    op.create_table(
        "v2_adjudication_allocations",
        sa.Column("allocation_id", sa.String(36), primary_key=True),
        sa.Column(
            "adjudication_id",
            sa.String(100),
            sa.ForeignKey("v2_adjudication_cases.adjudication_id"),
            nullable=False,
        ),
        sa.Column("manifest_hash", sa.String(128), nullable=False),
        sa.Column("evidence_hash", sa.String(128), nullable=False),
        sa.Column("database_identity", sa.String(200), nullable=False),
        sa.Column("position_id", sa.String(36), nullable=False),
        sa.Column("allocated_quantity", sa.Numeric(38, 18), nullable=False),
        sa.Column("prepared_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("before_state", sa.String(30), nullable=False, server_default="QUARANTINED"),
        sa.UniqueConstraint(
            "adjudication_id",
            "database_identity",
            "position_id",
            name="uq_v2_adjudication_participant",
        ),
        sa.CheckConstraint("allocated_quantity > 0", name="ck_v2_adjudication_allocation_positive"),
    )
    op.create_index(
        "ix_v2_adjudication_allocations_adjudication_id",
        "v2_adjudication_allocations",
        ["adjudication_id"],
    )
    op.create_index("ix_v2_adjudication_allocations_position_id", "v2_adjudication_allocations", ["position_id"])
    op.create_table(
        "v2_adjudication_finalizations",
        sa.Column("finalization_id", sa.String(36), primary_key=True),
        sa.Column(
            "adjudication_id",
            sa.String(100),
            sa.ForeignKey("v2_adjudication_cases.adjudication_id"),
            nullable=False,
        ),
        sa.Column("manifest_hash", sa.String(128), nullable=False),
        sa.Column("evidence_hash", sa.String(128), nullable=False),
        sa.Column("database_identity", sa.String(200), nullable=False),
        sa.Column("position_id", sa.String(36), nullable=False),
        sa.Column("exchange_order_id", sa.String(100), nullable=False),
        sa.Column("exchange_trade_id", sa.String(100), nullable=False),
        sa.Column("aggregate_quantity", sa.Numeric(38, 18), nullable=False),
        sa.Column("allocated_quantity", sa.Numeric(38, 18), nullable=False),
        sa.Column("before_state", sa.String(30), nullable=False),
        sa.Column("after_state", sa.String(30), nullable=False),
        sa.Column("operator_identity", sa.String(200), nullable=False),
        sa.Column("operator_reason", sa.Text(), nullable=False),
        sa.Column("finalized_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint(
            "adjudication_id",
            "database_identity",
            "position_id",
            name="uq_v2_adjudication_finalization_participant",
        ),
        sa.CheckConstraint("aggregate_quantity > 0", name="ck_v2_adjudication_finalization_aggregate_positive"),
        sa.CheckConstraint("allocated_quantity > 0", name="ck_v2_adjudication_finalization_allocation_positive"),
    )
    op.create_index(
        "ix_v2_adjudication_finalizations_adjudication_id",
        "v2_adjudication_finalizations",
        ["adjudication_id"],
    )
    op.create_index("ix_v2_adjudication_finalizations_position_id", "v2_adjudication_finalizations", ["position_id"])


def downgrade() -> None:
    op.drop_table("v2_adjudication_finalizations")
    op.drop_index("ix_v2_adjudication_allocations_position_id", table_name="v2_adjudication_allocations")
    op.drop_index("ix_v2_adjudication_allocations_adjudication_id", table_name="v2_adjudication_allocations")
    op.drop_table("v2_adjudication_allocations")
    op.drop_table("v2_adjudication_cases")
