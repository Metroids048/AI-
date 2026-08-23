from __future__ import annotations

from dataclasses import replace
from datetime import UTC
from uuid import uuid4

from ..domain.events import KolEventType, KolTradeEvent
from ..domain.messages import MessageEnvelope
from ..domain.threads import ThreadLinkResult, ThreadState, TradeThread


class TradeThreadEngine:
    def __init__(self) -> None:
        self._threads: list[TradeThread] = []

    def apply(self, event: KolTradeEvent, message: MessageEnvelope) -> TradeThread | None:
        if event.event_type is not KolEventType.OPEN or not event.symbol:
            return None
        now = message.received_at.astimezone(UTC)
        thread_id = str(uuid4())
        thread = TradeThread(
            thread_id=thread_id,
            source_id=event.source_id,
            symbol=event.symbol,
            side=event.side,
            state=ThreadState.OPEN,
            opened_message_id=message.message_id,
            last_message_id=message.message_id,
            created_at=now,
            updated_at=now,
            events=[replace(event, thread_id=thread_id)],
        )
        self._threads.append(thread)
        return thread

    def link(self, event: KolTradeEvent, message: MessageEnvelope) -> ThreadLinkResult:
        active = [
            thread
            for thread in self._threads
            if thread.source_id == event.source_id
            and thread.state
            in {
                ThreadState.OPEN,
                ThreadState.PARTIALLY_CLOSED,
                ThreadState.WAITING_ENTRY,
            }
        ]
        if message.reply_to_message_id is not None:
            replied = [
                thread
                for thread in active
                if thread.last_message_id == message.reply_to_message_id
                or thread.opened_message_id == message.reply_to_message_id
            ]
            if len(replied) == 1:
                return self._attach(replied[0], event, message)
        if event.symbol:
            active = [thread for thread in active if thread.symbol == event.symbol]
        if event.side:
            conflicting = [thread for thread in active if thread.side not in {None, event.side}]
            if conflicting and not [thread for thread in active if thread.side in {None, event.side}]:
                return ThreadLinkResult(None, "DIRECTION_CONFLICT")
            active = [thread for thread in active if thread.side in {None, event.side}]
        if len(active) == 1:
            return self._attach(active[0], event, message)
        if len(active) > 1:
            return ThreadLinkResult(None, "AMBIGUOUS_THREAD")
        return ThreadLinkResult(None, "NO_ACTIVE_THREAD")

    @staticmethod
    def _attach(thread: TradeThread, event: KolTradeEvent, message: MessageEnvelope) -> ThreadLinkResult:
        thread.events.append(replace(event, thread_id=thread.thread_id))
        thread.last_message_id = message.message_id
        thread.updated_at = message.received_at.astimezone(UTC)
        if event.event_type is KolEventType.PARTIAL_CLOSE:
            thread.state = ThreadState.PARTIALLY_CLOSED
        elif event.event_type is KolEventType.CLOSE_ALL:
            thread.state = ThreadState.CLOSED
        elif event.event_type is KolEventType.CANCEL_ENTRY:
            thread.state = ThreadState.CANCELLED
        return ThreadLinkResult(thread, "LINKED")
