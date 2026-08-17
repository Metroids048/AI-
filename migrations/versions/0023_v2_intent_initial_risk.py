"""Persist durable entry initial-risk reservations."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "v2_execution_intents",
        sa.Column("initial_risk_usdt", sa.Numeric(20, 8), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("v2_execution_intents", "initial_risk_usdt")
