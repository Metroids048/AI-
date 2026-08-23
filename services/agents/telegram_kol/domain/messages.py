from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class MessageEnvelope:
    source_id: str
    chat_id: str
    message_id: int
    revision: int
    posted_at: datetime
    received_at: datetime
    text: str = ""
    caption: str = ""
    media_path: str | None = None
    media_hash: str | None = None
    reply_to_message_id: int | None = None
    forwarded_from: str | None = None
    edited_at: datetime | None = None

    @property
    def normalized_text(self) -> str:
        return "\n".join(part.strip() for part in (self.text, self.caption) if part and part.strip()).strip()
