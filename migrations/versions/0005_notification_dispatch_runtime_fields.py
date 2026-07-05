"""add notification dispatch runtime fields

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-04
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "notification_outbox",
        sa.Column("delivery_channels", sa.JSON(), nullable=False, server_default='["telegram", "webhook"]'),
    )
    op.add_column("notification_outbox", sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("notification_outbox", sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("notification_outbox", sa.Column("attempt_history", sa.JSON(), nullable=False, server_default="[]"))


def downgrade() -> None:
    op.drop_column("notification_outbox", "attempt_history")
    op.drop_column("notification_outbox", "last_attempt_at")
    op.drop_column("notification_outbox", "next_attempt_at")
    op.drop_column("notification_outbox", "delivery_channels")
