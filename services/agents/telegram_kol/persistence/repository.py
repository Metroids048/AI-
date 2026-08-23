from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from services.database import get_session_factory

from ..domain.events import KolTradeEvent
from ..ingestion.storage import AppendResult, RawMessageRecord
from .models import (
    TelegramCandidateInbox,
    TelegramCheckpoint,
    TelegramParsedEvent,
    TelegramRawMessage,
    TelegramSource,
    TelegramTradeThread,
)


class TelegramKolRepository:
    """Database adapter preserving append-only raw and idempotent inbox rules."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def append_raw(self, record: RawMessageRecord, *, source_id: str) -> AppendResult:
        existing = self.session.scalar(
            select(TelegramRawMessage).where(
                TelegramRawMessage.chat_id == record.chat_id,
                TelegramRawMessage.message_id == record.message_id,
                TelegramRawMessage.revision == record.revision,
            )
        )
        if existing is not None:
            return AppendResult(self._to_raw(existing), False)
        row = TelegramRawMessage(
            source_id=source_id,
            chat_id=record.chat_id,
            message_id=record.message_id,
            revision=record.revision,
            posted_at=record.posted_at,
            received_at=record.received_at,
            text=record.text,
            media_path=record.media_path,
            media_hash=record.media_hash,
            reply_to_message_id=record.reply_to_message_id,
            raw_hash=record.raw_hash,
            deleted=record.deleted,
        )
        self.session.add(row)
        self.session.flush()
        return AppendResult(self._to_raw(row), True)

    def upsert_source(
        self,
        *,
        source_id: str,
        chat_id: str,
        title: str,
        chat_type: str,
        state: str = "CAPTURE_ONLY",
    ) -> None:
        row = self.session.get(TelegramSource, source_id)
        if row is None:
            row = TelegramSource(source_id=source_id, chat_id=chat_id, title=title, chat_type=chat_type, state=state)
            self.session.add(row)
        else:
            row.chat_id = chat_id
            row.title = title
            row.chat_type = chat_type
            row.state = state
        self.session.flush()

    def save_event(self, *, raw_id: str, event: KolTradeEvent, reason_code: str | None = None) -> None:
        row = TelegramParsedEvent(
            raw_id=raw_id,
            source_id=event.source_id,
            event_type=event.event_type.value,
            symbol=event.symbol,
            completeness=event.completeness.value,
            reason_code=reason_code,
            payload={
                "side": event.side,
                "entry_semantics": event.entry_semantics.value,
                "entry_price": str(event.entry_price) if event.entry_price is not None else None,
                "entry_low": str(event.entry_low) if event.entry_low is not None else None,
                "entry_high": str(event.entry_high) if event.entry_high is not None else None,
                "stop_loss": str(event.stop_loss) if event.stop_loss is not None else None,
                "take_profits": [str(value) for value in event.take_profits],
            },
        )
        self.session.add(row)
        self.session.flush()

    def save_thread(self, thread: Any) -> None:
        from ..domain.threads import TradeThread

        if not isinstance(thread, TradeThread):
            raise TypeError("thread must be a TradeThread")
        row = self.session.get(TelegramTradeThread, thread.thread_id)
        if row is None:
            row = TelegramTradeThread(
                thread_id=thread.thread_id,
                source_id=thread.source_id,
                symbol=thread.symbol,
                side=thread.side,
                state=thread.state.value,
                opened_message_id=thread.opened_message_id,
                last_message_id=thread.last_message_id,
                created_at=thread.created_at,
                updated_at=thread.updated_at,
            )
            self.session.add(row)
        else:
            row.side = thread.side
            row.state = thread.state.value
            row.last_message_id = thread.last_message_id
            row.updated_at = thread.updated_at
        self.session.flush()

    def enqueue_candidate(
        self,
        *,
        candidate_key: str,
        source_id: str,
        thread_id: str,
        symbol: str,
        payload: dict[str, Any],
    ) -> bool:
        existing = self.session.scalar(
            select(TelegramCandidateInbox).where(TelegramCandidateInbox.candidate_key == candidate_key)
        )
        if existing is not None:
            return False
        self.session.add(
            TelegramCandidateInbox(
                candidate_key=candidate_key,
                source_id=source_id,
                thread_id=thread_id,
                symbol=symbol,
                payload=payload,
            )
        )
        self.session.flush()
        return True

    def checkpoint(self, collector_id: str) -> TelegramCheckpoint | None:
        return self.session.get(TelegramCheckpoint, collector_id)

    def save_checkpoint(self, *, collector_id: str, chat_id: str, message_id: int, revision: int) -> None:
        row = self.session.get(TelegramCheckpoint, collector_id)
        if row is None:
            row = TelegramCheckpoint(collector_id=collector_id)
            self.session.add(row)
        row.last_chat_id = chat_id
        row.last_message_id = message_id
        row.last_revision = revision
        self.session.flush()

    @staticmethod
    def _to_raw(row: TelegramRawMessage) -> RawMessageRecord:
        return RawMessageRecord(
            raw_id=row.raw_id,
            source_id=row.source_id,
            chat_id=row.chat_id,
            message_id=row.message_id,
            revision=row.revision,
            received_at=row.received_at,
            posted_at=row.posted_at,
            text=row.text,
            media_path=row.media_path,
            media_hash=row.media_hash,
            reply_to_message_id=row.reply_to_message_id,
            raw_hash=row.raw_hash,
            deleted=row.deleted,
        )


class SqlSourceRegistry:
    """Persist folder-derived source membership without making config a second registry."""

    def __init__(self, database_url: str | None = None) -> None:
        self.database_url = database_url

    def sync(self, sources: list[dict[str, object]]) -> None:
        discovered_ids = {str(source["source_id"]) for source in sources}
        with get_session_factory(self.database_url)() as session:
            repository = TelegramKolRepository(session)
            for source in sources:
                repository.upsert_source(
                    source_id=str(source["source_id"]),
                    chat_id=str(source["chat_id"]),
                    title=str(source.get("title") or source["chat_id"]),
                    chat_type=str(source.get("chat_type") or "unknown"),
                    state="CAPTURE_ONLY",
                )
            rows = session.scalars(select(TelegramSource)).all()
            for row in rows:
                if row.source_id not in discovered_ids:
                    row.state = "DISABLED"
            session.commit()


class SqlRawMessageLedger:
    """Raw ledger facade backed by the project's configured SQL database."""

    def __init__(self, database_url: str | None = None) -> None:
        self.database_url = database_url

    def append(self, **kwargs: Any) -> AppendResult:
        source_id = str(kwargs.pop("source_id", "unknown"))
        record = RawMessageRecord(source_id=source_id, **kwargs)
        with get_session_factory(self.database_url)() as session:
            result = TelegramKolRepository(session).append_raw(record, source_id=source_id)
            session.commit()
            return result

    def list_message(self, chat_id: str, message_id: int) -> list[RawMessageRecord]:
        with get_session_factory(self.database_url)() as session:
            rows = session.scalars(
                select(TelegramRawMessage)
                .where(
                    TelegramRawMessage.chat_id == chat_id,
                    TelegramRawMessage.message_id == message_id,
                )
                .order_by(TelegramRawMessage.revision)
            )
            return [TelegramKolRepository._to_raw(row) for row in rows]

    def next_revision(self, chat_id: str, message_id: int) -> int:
        rows = self.list_message(chat_id, message_id)
        return rows[-1].revision + 1 if rows else 0
