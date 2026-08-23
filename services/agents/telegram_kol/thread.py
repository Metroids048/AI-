from __future__ import annotations

from dataclasses import dataclass, field

from .models import KolTradeEvent


@dataclass
class TradeThread:
    source_chat: str
    symbol: str
    side: str
    state: str = "OPEN"
    stop_loss: str | None = None
    take_profits: list[str] = field(default_factory=list)
    linked_event_ids: list[str] = field(default_factory=list)


class TradeThreadBook:
    def __init__(self) -> None:
        self._threads: dict[tuple[str, str], TradeThread] = {}

    def get(self, source_chat: str, symbol: str) -> TradeThread | None:
        return self._threads.get((source_chat, symbol))

    def apply(self, event: KolTradeEvent) -> TradeThread | None:
        if not event.symbol:
            return None
        key = (event.source_chat, event.symbol)
        if event.event_type == "OPEN":
            if not event.side:
                return None
            thread = TradeThread(
                source_chat=event.source_chat,
                symbol=event.symbol,
                side=event.side,
                state="OPEN",
                stop_loss=str(event.stop_loss) if event.stop_loss is not None else None,
                take_profits=[str(tp.price) for tp in event.take_profits],
                linked_event_ids=[event.event_id],
            )
            self._threads[key] = thread
            return thread
        thread = self._threads.get(key)
        if thread is None:
            return None
        if event.event_type == "POSITION_UPDATE":
            for action in event.actions:
                if action.action_type == "PARTIAL_CLOSE":
                    thread.state = "PARTIALLY_CLOSED"
                elif action.action_type == "MOVE_STOP" and action.price is not None:
                    thread.stop_loss = str(action.price)
                elif action.action_type == "CANCEL_TAKE_PROFIT":
                    thread.take_profits = []
                elif action.action_type == "CLOSE_ALL":
                    thread.state = "CLOSED"
            thread.linked_event_ids.append(event.event_id)
            return thread
        if event.event_type == "STOP_REPORTED":
            thread.state = "CLOSED"
            thread.linked_event_ids.append(event.event_id)
            return thread
        return thread if event.event_type == "ADD_POSITION" else None
