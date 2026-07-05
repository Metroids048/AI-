"""persist notification outbox intents

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-04
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "notification_outbox",
        sa.Column("notification_id", sa.String(length=120), primary_key=True),
        sa.Column("event_type", sa.String(length=60), nullable=False),
        sa.Column("severity", sa.String(length=20), nullable=False),
        sa.Column("channel_group", sa.String(length=40), nullable=False, server_default="ops"),
        sa.Column("subject", sa.String(length=240), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("source_ref", sa.String(length=255), nullable=True),
        sa.Column("delivery_status", sa.String(length=40), nullable=False, server_default="pending_adapter"),
        sa.Column("delivery_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_notification_outbox_delivery_status", "notification_outbox", ["delivery_status"])
    op.create_index("ix_notification_outbox_severity", "notification_outbox", ["severity"])
    op.create_index("ix_notification_outbox_event_type", "notification_outbox", ["event_type"])
    op.create_index("ix_notification_outbox_created_at", "notification_outbox", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_notification_outbox_created_at", table_name="notification_outbox")
    op.drop_index("ix_notification_outbox_event_type", table_name="notification_outbox")
    op.drop_index("ix_notification_outbox_severity", table_name="notification_outbox")
    op.drop_index("ix_notification_outbox_delivery_status", table_name="notification_outbox")
    op.drop_table("notification_outbox")
