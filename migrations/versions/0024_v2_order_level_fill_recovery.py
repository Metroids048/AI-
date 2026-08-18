"""Allow authoritative order-level fill recovery without fake trade ids."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("v2_exchange_fills") as batch:
        batch.alter_column("trade_id", existing_type=sa.String(length=100), nullable=True)
        batch.add_column(
            sa.Column("fill_source", sa.String(length=64), nullable=False, server_default="BINANCE_USER_TRADE")
        )


def downgrade() -> None:
    with op.batch_alter_table("v2_exchange_fills") as batch:
        batch.drop_column("fill_source")
        batch.alter_column("trade_id", existing_type=sa.String(length=100), nullable=False)
