"""Enforce one open managed position per exchange account, symbol, and side.

Revision ID: 0015
Revises: 0014
Create Date: 2026-07-27
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    indexes = {item["name"] for item in sa.inspect(bind).get_indexes("position_records")}
    if "uq_open_managed_position_identity" not in indexes:
        predicate = sa.text("management_status = 'MANAGED_STRATEGY'")
        op.create_index(
            "uq_open_managed_position_identity",
            "position_records",
            ["exchange_account", "symbol", "position_side"],
            unique=True,
            sqlite_where=predicate,
            postgresql_where=predicate,
        )


def downgrade() -> None:
    # The invariant is intentionally not weakened by automated downgrade.
    pass
