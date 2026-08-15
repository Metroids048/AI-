"""Persist original protection geometry for one-way profit protection."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("v2_protection_records", sa.Column("original_stop_loss_price", sa.Numeric(20, 4), nullable=True))
    op.add_column("v2_protection_records", sa.Column("policy", sa.String(20), nullable=False, server_default="P1"))
    op.execute(
        "UPDATE v2_protection_records SET original_stop_loss_price = stop_loss_price "
        "WHERE original_stop_loss_price IS NULL"
    )


def downgrade() -> None:
    op.drop_column("v2_protection_records", "policy")
    op.drop_column("v2_protection_records", "original_stop_loss_price")
