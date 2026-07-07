"""In-process fan-out bus for live market feed updates."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from shared.models import OHLCVBar

FeedKey = tuple[str, str]


@dataclass
class LiveFeedState:
    symbol: str
    timeframe: str
    status: str = "idle"
    source: str = "binance_public_ws"
    last_event_at: datetime | None = None
    last_payload: dict[str, Any] | None = None
    subscribers: int = 0
    published_count: int = 0
    dropped_count: int = 0
    last_error: str | None = None

    def model_dump(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "status": self.status,
            "source": self.source,
            "last_event_at": self.last_event_at.isoformat() if self.last_event_at else None,
            "last_payload": self.last_payload,
            "subscribers": self.subscribers,
            "published_count": self.published_count,
            "dropped_count": self.dropped_count,
            "last_error": self.last_error,
        }


@dataclass
class LiveFeedSubscription:
    symbol: str
    timeframe: str
    queue: asyncio.Queue[dict[str, Any]]


class LiveFeedBus:
    def __init__(self, *, max_queue_size: int = 100) -> None:
        self.max_queue_size = max_queue_size
        self._states: dict[FeedKey, LiveFeedState] = {}
        self._subscribers: dict[FeedKey, set[asyncio.Queue[dict[str, Any]]]] = {}
        self._lock = asyncio.Lock()

    async def publish_candle(self, bar: OHLCVBar, *, source: str = "binance_public_ws") -> dict[str, Any]:
        payload = {
            "event": "live_candle",
            "source": source,
            "feed_status": "live",
            "symbol": bar.symbol,
            "timeframe": str(bar.timeframe),
            "published_at": datetime.now(UTC).isoformat(),
            "payload": bar.model_dump(mode="json", by_alias=True),
        }
        key = (bar.symbol, str(bar.timeframe))
        async with self._lock:
            state = self._state_for(key)
            state.status = "live"
            state.source = source
            state.last_event_at = datetime.now(UTC)
            state.last_payload = payload
            state.last_error = None
            state.published_count += 1
            subscribers = list(self._subscribers.get(key, set()))
            state.subscribers = len(subscribers)
            for queue in subscribers:
                if queue.full():
                    try:
                        queue.get_nowait()
                        state.dropped_count += 1
                    except asyncio.QueueEmpty:
                        pass
                queue.put_nowait(payload)
        return payload

    async def set_error(self, *, symbol: str, timeframe: str, error: str) -> None:
        async with self._lock:
            state = self._state_for((symbol, timeframe))
            state.status = "reconnecting"
            state.last_error = error
            state.last_event_at = datetime.now(UTC)

    async def subscribe(self, *, symbol: str, timeframe: str) -> LiveFeedSubscription:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=self.max_queue_size)
        key = (symbol, timeframe)
        async with self._lock:
            self._subscribers.setdefault(key, set()).add(queue)
            state = self._state_for(key)
            state.subscribers = len(self._subscribers[key])
        return LiveFeedSubscription(symbol=symbol, timeframe=timeframe, queue=queue)

    async def unsubscribe(self, subscription: LiveFeedSubscription) -> None:
        key = (subscription.symbol, subscription.timeframe)
        async with self._lock:
            subscribers = self._subscribers.get(key)
            if subscribers is not None:
                subscribers.discard(subscription.queue)
                if not subscribers:
                    self._subscribers.pop(key, None)
            state = self._state_for(key)
            state.subscribers = len(self._subscribers.get(key, set()))

    def status(self, *, symbol: str | None = None, timeframe: str | None = None) -> dict[str, Any]:
        if symbol is not None and timeframe is not None:
            return self._state_for((symbol, timeframe)).model_dump()
        return {f"{key[0]}:{key[1]}": state.model_dump() for key, state in self._states.items()}

    def latest_payload(self, *, symbol: str, timeframe: str) -> dict[str, Any] | None:
        return self._state_for((symbol, timeframe)).last_payload

    def _state_for(self, key: FeedKey) -> LiveFeedState:
        if key not in self._states:
            self._states[key] = LiveFeedState(symbol=key[0], timeframe=key[1])
        return self._states[key]


live_feed_bus = LiveFeedBus()
