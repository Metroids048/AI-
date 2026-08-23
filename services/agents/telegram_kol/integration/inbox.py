from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class InboxItem:
    candidate_key: str
    source_id: str
    thread_id: str
    symbol: str
    payload: dict
    created_at: datetime
    state: str = "PENDING"
    blocked_reason: str | None = None
    dispatched_at: datetime | None = None


class CandidateInbox:
    def __init__(self) -> None:
        self._items: dict[str, InboxItem] = {}

    def put(self, item: InboxItem) -> bool:
        if item.candidate_key in self._items:
            return False
        self._items[item.candidate_key] = item
        return True

    def pending(self) -> list[InboxItem]:
        return [item for item in self._items.values() if item.state == "PENDING"]

    def mark_dispatched(self, candidate_key: str, *, dispatched_at: datetime) -> None:
        item = self._items[candidate_key]
        item.state = "DISPATCHED"
        item.dispatched_at = dispatched_at

    def mark_blocked(self, candidate_key: str, *, reason: str) -> None:
        item = self._items[candidate_key]
        item.state = "BLOCKED"
        item.blocked_reason = reason
