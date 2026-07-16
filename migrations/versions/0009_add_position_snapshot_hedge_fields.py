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

_HEDGE_INDEX = "ix_position_snapshots_hedge_group_id"


def upgrade() -> None:
    # Local SQLite can already carry these columns/index when ORM create_all or a
    # prior non-transactional DDL attempt outran alembic_version (stuck at 0008).
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("position_snapshots")}
    indexes = {index["name"] for index in inspector.get_indexes("position_snapshots")}

    if "hedge_group_id" not in columns:
        op.add_column("position_snapshots", sa.Column("hedge_group_id", sa.String(length=60), nullable=True))
    if "is_hedge_leg" not in columns:
        op.add_column(
            "position_snapshots",
            sa.Column("is_hedge_leg", sa.Boolean(), nullable=False, server_default=sa.false()),
        )
    if _HEDGE_INDEX not in indexes:
        op.create_index(op.f(_HEDGE_INDEX), "position_snapshots", ["hedge_group_id"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("position_snapshots")}
    indexes = {index["name"] for index in inspector.get_indexes("position_snapshots")}

    with op.batch_alter_table("position_snapshots") as batch_op:
        if _HEDGE_INDEX in indexes:
            batch_op.drop_index(op.f(_HEDGE_INDEX))
        if "is_hedge_leg" in columns:
            batch_op.drop_column("is_hedge_leg")
        if "hedge_group_id" in columns:
            batch_op.drop_column("hedge_group_id")
