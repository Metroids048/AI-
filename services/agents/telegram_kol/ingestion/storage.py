from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4


@dataclass(frozen=True)
class RawMessageRecord:
    chat_id: str
    message_id: int
    revision: int
    received_at: datetime
    posted_at: datetime | None
    text: str
    source_id: str = "unknown"
    raw_id: str | None = None
    media_path: str | None = None
    media_hash: str | None = None
    reply_to_message_id: int | None = None
    raw_hash: str | None = None
    deleted: bool = False


@dataclass(frozen=True)
class AppendResult:
    record: RawMessageRecord
    created: bool


class RawMessageLedger:
    """Small deterministic ledger used by the collector and offline replay.

    The database repository uses the same key and append-only semantics.  This
    implementation keeps the collector testable without a live database.
    """

    def __init__(self) -> None:
        self._rows: dict[tuple[str, int, int], RawMessageRecord] = {}

    def append(
        self,
        *,
        source_id: str = "unknown",
        chat_id: str,
        message_id: int,
        revision: int,
        received_at: datetime,
        text: str,
        posted_at: datetime | None = None,
        media_path: str | None = None,
        media_hash: str | None = None,
        reply_to_message_id: int | None = None,
        raw_hash: str | None = None,
        deleted: bool = False,
    ) -> AppendResult:
        key = (chat_id, message_id, revision)
        existing = self._rows.get(key)
        if existing is not None:
            return AppendResult(existing, False)
        record = RawMessageRecord(
            source_id=source_id,
            raw_id=str(uuid4()),
            chat_id=chat_id,
            message_id=message_id,
            revision=revision,
            received_at=received_at,
            posted_at=posted_at,
            text=text,
            media_path=media_path,
            media_hash=media_hash,
            reply_to_message_id=reply_to_message_id,
            raw_hash=raw_hash
            or _raw_hash(
                source_id=source_id,
                chat_id=chat_id,
                message_id=message_id,
                revision=revision,
                text=text,
                media_hash=media_hash,
                reply_to_message_id=reply_to_message_id,
                deleted=deleted,
            ),
            deleted=deleted,
        )
        self._rows[key] = record
        return AppendResult(record, True)

    def list_message(self, chat_id: str, message_id: int) -> list[RawMessageRecord]:
        rows = (
            row
            for (row_chat, row_message, _), row in self._rows.items()
            if row_chat == chat_id and row_message == message_id
        )
        return sorted(rows, key=lambda row: row.revision)

    def next_revision(self, chat_id: str, message_id: int) -> int:
        rows = self.list_message(chat_id, message_id)
        return rows[-1].revision + 1 if rows else 0


def _raw_hash(**payload: object) -> str:
    canonical = json.dumps(payload, ensure_ascii=True, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()
