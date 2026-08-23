from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

from ..domain.messages import MessageEnvelope
from ..parsing.parser import UniversalKolParser


class RawLedgerPort(Protocol):
    def append(self, **kwargs: Any) -> Any: ...

    def next_revision(self, chat_id: str, message_id: int) -> int: ...


class TelegramCollector:
    """Persist-first collector facade used by Telethon callbacks and replay."""

    def __init__(
        self,
        *,
        ledger: Any,
        parser: UniversalKolParser | None = None,
        ocr: Any | None = None,
    ) -> None:
        self.ledger = ledger
        self.parser = parser or UniversalKolParser()
        self.ocr = ocr
        self.last_received_at: datetime | None = None
        self.processed_count = 0
        self.duplicate_count = 0
        self.last_raw_record: Any | None = None

    def ingest(
        self,
        *,
        source_id: str,
        chat_id: str,
        message_id: int,
        posted_at: datetime,
        received_at: datetime,
        text: str = "",
        caption: str = "",
        media_path: str | None = None,
        media_hash: str | None = None,
        reply_to_message_id: int | None = None,
        revision: int | None = None,
    ) -> Any:
        resolved_revision = self.ledger.next_revision(chat_id, message_id) if revision is None else revision
        result = self.ledger.append(
            source_id=source_id,
            chat_id=chat_id,
            message_id=message_id,
            revision=resolved_revision,
            received_at=received_at,
            posted_at=posted_at,
            text="\n".join(part for part in (text, caption) if part),
            media_path=media_path,
            media_hash=media_hash,
            reply_to_message_id=reply_to_message_id,
        )
        self.last_received_at = received_at
        if not result.created:
            self.duplicate_count += 1
            return None
        self.last_raw_record = result.record
        self.processed_count += 1
        parsed_text = text
        if self.ocr is not None and media_path:
            try:
                ocr_text = self.ocr.extract(media_path)
            except Exception:  # noqa: BLE001
                ocr_text = ""
            parsed_text = "\n".join(part for part in (text, caption, ocr_text) if part)
        return self.parser.parse(
            MessageEnvelope(
                source_id=source_id,
                chat_id=chat_id,
                message_id=message_id,
                revision=resolved_revision,
                posted_at=posted_at,
                received_at=received_at,
                text=parsed_text,
                caption="",
                media_path=media_path,
                media_hash=media_hash,
                reply_to_message_id=reply_to_message_id,
            )
        )

    def delete(
        self,
        *,
        chat_id: str,
        message_id: int,
        received_at: datetime,
        source_id: str = "unknown",
    ) -> None:
        self.ledger.append(
            source_id=source_id,
            chat_id=chat_id,
            message_id=message_id,
            revision=self.ledger.next_revision(chat_id, message_id),
            received_at=received_at,
            text="",
            deleted=True,
        )
