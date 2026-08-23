"""Add Telegram KOL raw ledger, lifecycle and candidate inbox tables."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "telegram_sources",
        sa.Column("source_id", sa.String(120), primary_key=True),
        sa.Column("chat_id", sa.String(80), nullable=False, unique=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("chat_type", sa.String(40), nullable=False, server_default="unknown"),
        sa.Column("state", sa.String(30), nullable=False, server_default="CAPTURE_ONLY"),
        sa.Column("discovered_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )
    op.create_table(
        "telegram_raw_messages",
        sa.Column("raw_id", sa.String(36), primary_key=True),
        sa.Column("source_id", sa.String(120), nullable=False),
        sa.Column("chat_id", sa.String(80), nullable=False),
        sa.Column("message_id", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("posted_at", sa.DateTime(), nullable=True),
        sa.Column("received_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("edited_at", sa.DateTime(), nullable=True),
        sa.Column("text", sa.Text(), nullable=False, server_default=""),
        sa.Column("media_path", sa.Text(), nullable=True),
        sa.Column("media_hash", sa.String(128), nullable=True),
        sa.Column("reply_to_message_id", sa.Integer(), nullable=True),
        sa.Column("raw_hash", sa.String(128), nullable=True),
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.UniqueConstraint("chat_id", "message_id", "revision", name="uq_telegram_raw_message_revision"),
    )
    op.create_index("ix_telegram_raw_messages_source_id", "telegram_raw_messages", ["source_id"])
    op.create_index("ix_telegram_raw_message_chat_received", "telegram_raw_messages", ["chat_id", "received_at"])
    op.create_index("ix_telegram_raw_messages_media_hash", "telegram_raw_messages", ["media_hash"])
    op.create_index("ix_telegram_raw_messages_raw_hash", "telegram_raw_messages", ["raw_hash"])
    op.create_table(
        "telegram_media_artifacts",
        sa.Column("media_hash", sa.String(128), primary_key=True),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("media_type", sa.String(40), nullable=False, server_default="photo"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
    )
    op.create_table(
        "telegram_checkpoints",
        sa.Column("collector_id", sa.String(120), primary_key=True),
        sa.Column("last_chat_id", sa.String(80), nullable=True),
        sa.Column("last_message_id", sa.Integer(), nullable=True),
        sa.Column("last_revision", sa.Integer(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
    )
    op.create_table(
        "telegram_parsed_events",
        sa.Column("event_id", sa.String(36), primary_key=True),
        sa.Column("raw_id", sa.String(36), nullable=False),
        sa.Column("source_id", sa.String(120), nullable=False),
        sa.Column("event_type", sa.String(40), nullable=False),
        sa.Column("symbol", sa.String(30), nullable=True),
        sa.Column("completeness", sa.String(30), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("reason_code", sa.String(80), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.UniqueConstraint("raw_id", name="uq_telegram_parsed_event_raw"),
    )
    op.create_index("ix_telegram_parsed_events_raw_id", "telegram_parsed_events", ["raw_id"])
    op.create_index("ix_telegram_parsed_events_source_id", "telegram_parsed_events", ["source_id"])
    op.create_table(
        "telegram_trade_threads",
        sa.Column("thread_id", sa.String(120), primary_key=True),
        sa.Column("source_id", sa.String(120), nullable=False),
        sa.Column("symbol", sa.String(30), nullable=False),
        sa.Column("side", sa.String(10), nullable=True),
        sa.Column("state", sa.String(30), nullable=False),
        sa.Column("opened_message_id", sa.Integer(), nullable=False),
        sa.Column("last_message_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
    )
    op.create_index("ix_telegram_trade_threads_source_id", "telegram_trade_threads", ["source_id"])
    op.create_index("ix_telegram_thread_active", "telegram_trade_threads", ["source_id", "symbol", "state"])
    op.create_table(
        "telegram_shadow_trades",
        sa.Column("shadow_id", sa.String(36), primary_key=True),
        sa.Column("source_id", sa.String(120), nullable=False),
        sa.Column("thread_id", sa.String(120), nullable=False),
        sa.Column("symbol", sa.String(30), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="OPEN"),
        sa.Column("opened_at", sa.DateTime(), nullable=False),
        sa.Column("closed_at", sa.DateTime(), nullable=True),
        sa.Column("metrics", sa.JSON(), nullable=False),
    )
    op.create_index("ix_telegram_shadow_trades_source_id", "telegram_shadow_trades", ["source_id"])
    op.create_index("ix_telegram_shadow_trades_thread_id", "telegram_shadow_trades", ["thread_id"])
    op.create_index("ix_telegram_shadow_trades_symbol", "telegram_shadow_trades", ["symbol"])
    op.create_table(
        "telegram_candidate_inbox",
        sa.Column("inbox_id", sa.String(36), primary_key=True),
        sa.Column("candidate_key", sa.String(240), nullable=False),
        sa.Column("source_id", sa.String(120), nullable=False),
        sa.Column("thread_id", sa.String(120), nullable=False),
        sa.Column("symbol", sa.String(30), nullable=False),
        sa.Column("state", sa.String(30), nullable=False, server_default="PENDING"),
        sa.Column("blocked_reason", sa.String(100), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("dispatched_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("candidate_key", name="uq_telegram_candidate_key"),
    )
    op.create_index("ix_telegram_candidate_inbox_candidate_key", "telegram_candidate_inbox", ["candidate_key"])
    op.create_index("ix_telegram_candidate_inbox_source_id", "telegram_candidate_inbox", ["source_id"])
    op.create_index("ix_telegram_candidate_inbox_symbol", "telegram_candidate_inbox", ["symbol"])


def downgrade() -> None:
    op.drop_table("telegram_candidate_inbox")
    op.drop_table("telegram_shadow_trades")
    op.drop_table("telegram_trade_threads")
    op.drop_table("telegram_parsed_events")
    op.drop_table("telegram_checkpoints")
    op.drop_table("telegram_media_artifacts")
    op.drop_index("ix_telegram_raw_messages_raw_hash", table_name="telegram_raw_messages")
    op.drop_index("ix_telegram_raw_messages_media_hash", table_name="telegram_raw_messages")
    op.drop_table("telegram_raw_messages")
    op.drop_table("telegram_sources")
