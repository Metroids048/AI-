from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from services.strategy_library.models import Base


def _id() -> str:
    return str(uuid.uuid4())


class TelegramSource(Base):
    __tablename__ = "telegram_sources"

    source_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    chat_id: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    chat_type: Mapped[str] = mapped_column(String(40), nullable=False, default="unknown")
    state: Mapped[str] = mapped_column(String(30), nullable=False, default="CAPTURE_ONLY")
    discovered_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())
    last_seen_at: Mapped[datetime | None] = mapped_column(nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class TelegramRawMessage(Base):
    __tablename__ = "telegram_raw_messages"
    __table_args__ = (
        UniqueConstraint("chat_id", "message_id", "revision", name="uq_telegram_raw_message_revision"),
        Index("ix_telegram_raw_message_chat_received", "chat_id", "received_at"),
    )

    raw_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    source_id: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    chat_id: Mapped[str] = mapped_column(String(80), nullable=False)
    message_id: Mapped[int] = mapped_column(Integer, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    posted_at: Mapped[datetime | None] = mapped_column(nullable=True)
    received_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())
    edited_at: Mapped[datetime | None] = mapped_column(nullable=True)
    text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    media_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    media_hash: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    reply_to_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    raw_hash: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class TelegramMediaArtifact(Base):
    __tablename__ = "telegram_media_artifacts"

    media_hash: Mapped[str] = mapped_column(String(128), primary_key=True)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    media_type: Mapped[str] = mapped_column(String(40), nullable=False, default="photo")
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())


class TelegramCheckpoint(Base):
    __tablename__ = "telegram_checkpoints"

    collector_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    last_chat_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    last_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now(), onupdate=func.now())


class TelegramParsedEvent(Base):
    __tablename__ = "telegram_parsed_events"
    __table_args__ = (UniqueConstraint("raw_id", name="uq_telegram_parsed_event_raw"),)

    event_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    raw_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    source_id: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    symbol: Mapped[str | None] = mapped_column(String(30), nullable=True)
    completeness: Mapped[str] = mapped_column(String(30), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    reason_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())


class TelegramTradeThread(Base):
    __tablename__ = "telegram_trade_threads"
    __table_args__ = (Index("ix_telegram_thread_active", "source_id", "symbol", "state"),)

    thread_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    symbol: Mapped[str] = mapped_column(String(30), nullable=False)
    side: Mapped[str | None] = mapped_column(String(10), nullable=True)
    state: Mapped[str] = mapped_column(String(30), nullable=False)
    opened_message_id: Mapped[int] = mapped_column(Integer, nullable=False)
    last_message_id: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now(), onupdate=func.now())


class TelegramShadowTrade(Base):
    __tablename__ = "telegram_shadow_trades"

    shadow_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    source_id: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    thread_id: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    symbol: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="OPEN")
    opened_at: Mapped[datetime] = mapped_column(nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    metrics: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class TelegramCandidateInbox(Base):
    __tablename__ = "telegram_candidate_inbox"
    __table_args__ = (UniqueConstraint("candidate_key", name="uq_telegram_candidate_key"),)

    inbox_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    candidate_key: Mapped[str] = mapped_column(String(240), nullable=False, index=True)
    source_id: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    thread_id: Mapped[str] = mapped_column(String(120), nullable=False)
    symbol: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    state: Mapped[str] = mapped_column(String(30), nullable=False, default="PENDING")
    blocked_reason: Mapped[str | None] = mapped_column(String(100), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())
    dispatched_at: Mapped[datetime | None] = mapped_column(nullable=True)
