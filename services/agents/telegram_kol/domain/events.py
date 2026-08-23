from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum


class KolEventType(StrEnum):
    OPEN = "OPEN"
    ADD_POSITION = "ADD_POSITION"
    PARTIAL_CLOSE = "PARTIAL_CLOSE"
    CLOSE_ALL = "CLOSE_ALL"
    MOVE_STOP = "MOVE_STOP"
    SET_TAKE_PROFIT = "SET_TAKE_PROFIT"
    CANCEL_TAKE_PROFIT = "CANCEL_TAKE_PROFIT"
    CANCEL_ENTRY = "CANCEL_ENTRY"
    STOP_REPORTED = "STOP_REPORTED"
    RESULT_REPORT = "RESULT_REPORT"
    COMMENTARY = "COMMENTARY"
    ADVERTISEMENT = "ADVERTISEMENT"
    AMBIGUOUS = "AMBIGUOUS"


class EntrySemantics(StrEnum):
    MARKET = "MARKET"
    NEAR_PRICE = "NEAR_PRICE"
    RANGE = "RANGE"
    LIMIT = "LIMIT"
    CONDITIONAL = "CONDITIONAL"
    UNKNOWN = "UNKNOWN"


class Completeness(StrEnum):
    COMPLETE = "COMPLETE"
    MANAGEABLE = "MANAGEABLE"
    INCOMPLETE = "INCOMPLETE"
    NON_SIGNAL = "NON_SIGNAL"


@dataclass(frozen=True)
class KolTradeEvent:
    event_type: KolEventType
    source_id: str
    chat_id: str
    message_id: int
    revision: int
    symbol: str | None = None
    side: str | None = None
    entry_semantics: EntrySemantics = EntrySemantics.UNKNOWN
    entry_price: Decimal | None = None
    entry_low: Decimal | None = None
    entry_high: Decimal | None = None
    stop_loss: Decimal | None = None
    take_profits: tuple[Decimal, ...] = ()
    close_fraction: Decimal | None = None
    new_stop: Decimal | None = None
    claimed_leverage: Decimal | None = None
    claimed_position_fraction: Decimal | None = None
    completeness: Completeness = Completeness.NON_SIGNAL
    raw_text: str = ""
    detected_at: datetime | None = None
    confidence: Decimal = Decimal("1")
    tags: tuple[str, ...] = field(default_factory=tuple)
    thread_id: str | None = None

    @property
    def is_directional(self) -> bool:
        return self.side in {"LONG", "SHORT"}

    @property
    def candidate_key(self) -> str:
        thread = self.thread_id or f"{self.source_id}:{self.symbol or 'unknown'}"
        return f"telegram:{self.source_id}:{thread}:{self.message_id}:{self.revision}"
