"""add hedge metadata to persisted position snapshots

Revision ID: 0009
Revises: 0008
Create Date: 2026-07-16
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("position_snapshots", sa.Column("hedge_group_id", sa.String(length=60), nullable=True))
    op.add_column(
        "position_snapshots",
        sa.Column("is_hedge_leg", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index(op.f("ix_position_snapshots_hedge_group_id"), "position_snapshots", ["hedge_group_id"])


def downgrade() -> None:
    with op.batch_alter_table("position_snapshots") as batch_op:
        batch_op.drop_index(op.f("ix_position_snapshots_hedge_group_id"))
        batch_op.drop_column("is_hedge_leg")
        batch_op.drop_column("hedge_group_id")
