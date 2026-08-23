from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from .events import KolTradeEvent


class ThreadState(StrEnum):
    WAITING_ENTRY = "WAITING_ENTRY"
    OPEN = "OPEN"
    PARTIALLY_CLOSED = "PARTIALLY_CLOSED"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"


@dataclass
class TradeThread:
    thread_id: str
    source_id: str
    symbol: str
    side: str | None
    state: ThreadState
    opened_message_id: int
    last_message_id: int
    created_at: datetime
    updated_at: datetime
    events: list[KolTradeEvent] = field(default_factory=list)


@dataclass(frozen=True)
class ThreadLinkResult:
    thread: TradeThread | None
    reason_code: str
