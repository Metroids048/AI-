from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from services.data.binance import run_live_collector_forever
from services.data.live_feed_bus import LiveFeedBus
from shared.models import OHLCVBar


def _bar(close: str = "42300") -> OHLCVBar:
    return OHLCVBar(
        symbol="BTC/USDT",
        exchange="binance",
        timeframe="1m",
        time=datetime(2024, 4, 1, 0, 0, tzinfo=UTC),
        open=Decimal("42000"),
        high=Decimal("42500"),
        low=Decimal("41800"),
        close=Decimal(close),
        volume=Decimal("12.5"),
    )


@pytest.mark.asyncio
async def test_live_feed_bus_fans_out_to_multiple_subscribers() -> None:
    bus = LiveFeedBus()
    first = await bus.subscribe(symbol="BTC/USDT", timeframe="1m")
    second = await bus.subscribe(symbol="BTC/USDT", timeframe="1m")

    await bus.publish_candle(_bar())

    assert (await first.queue.get())["payload"]["close"] == "42300"
    assert (await second.queue.get())["payload"]["close"] == "42300"
    status = bus.status(symbol="BTC/USDT", timeframe="1m")
    assert status["status"] == "live"
    assert status["subscribers"] == 2


@pytest.mark.asyncio
async def test_live_feed_bus_drops_old_messages_for_slow_subscribers() -> None:
    bus = LiveFeedBus(max_queue_size=1)
    subscription = await bus.subscribe(symbol="BTC/USDT", timeframe="1m")

    await bus.publish_candle(_bar("42300"))
    await bus.publish_candle(_bar("42400"))

    assert (await subscription.queue.get())["payload"]["close"] == "42400"
    assert bus.status(symbol="BTC/USDT", timeframe="1m")["dropped_count"] == 1


@pytest.mark.asyncio
async def test_live_feed_bus_records_reconnect_errors() -> None:
    bus = LiveFeedBus()

    await bus.set_error(symbol="BTC/USDT", timeframe="1m", error="network down")

    status = bus.status(symbol="BTC/USDT", timeframe="1m")
    assert status["status"] == "reconnecting"
    assert status["last_error"] == "network down"


@pytest.mark.asyncio
async def test_live_collector_restart_path_reports_bus_error() -> None:
    bus = LiveFeedBus()
    error_seen = asyncio.Event()
    closed = {"value": False}

    class FailingCollector:
        async def consume_kline_stream(self, *, symbol: str, timeframe: str) -> None:
            raise RuntimeError(f"stream down for {symbol}:{timeframe}")

        async def consume_mark_price_stream(self, *, symbol: str) -> None:
            await asyncio.sleep(60)

        def close(self) -> None:
            closed["value"] = True

    async def report_error(exc: Exception) -> None:
        await bus.set_error(symbol="BTC/USDT", timeframe="1m", error=str(exc))
        error_seen.set()

    task = asyncio.create_task(
        run_live_collector_forever(
            collector_factory=FailingCollector,
            symbol="BTC/USDT",
            perp_symbol="BTC/USDT:USDT",
            timeframe="1m",
            reconnect_error_handler=report_error,
            reconnect_delay_seconds=0.01,
        )
    )
    await asyncio.wait_for(error_seen.wait(), timeout=1)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

    status = bus.status(symbol="BTC/USDT", timeframe="1m")
    assert status["status"] == "reconnecting"
    assert "stream down" in status["last_error"]
    assert closed["value"] is True
