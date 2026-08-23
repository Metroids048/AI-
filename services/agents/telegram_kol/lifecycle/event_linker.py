from __future__ import annotations

from ..domain.events import KolTradeEvent
from ..domain.messages import MessageEnvelope
from ..domain.threads import ThreadLinkResult
from .thread_engine import TradeThreadEngine


class TradeEventLinker:
    """Named lifecycle boundary for linking parsed events to active threads."""

    def __init__(self, engine: TradeThreadEngine | None = None) -> None:
        self.engine = engine or TradeThreadEngine()

    def link(self, event: KolTradeEvent, message: MessageEnvelope) -> ThreadLinkResult:
        if event.event_type.value == "OPEN":
            thread = self.engine.apply(event, message)
            return ThreadLinkResult(thread, "CREATED" if thread is not None else "OPEN_REJECTED")
        return self.engine.link(event, message)
