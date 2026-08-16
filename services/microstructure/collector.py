from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import ccxt

from services.database import get_session_factory
from services.microstructure.health import refresh_health
from services.microstructure.storage import persist_snapshot, update_checkpoint

logger = logging.getLogger(__name__)


class MicrostructureCollector:
    """Failure-isolated polling collector for Binance USDT-M Testnet books."""

    def __init__(self, database_url: str, symbols: tuple[str, ...] = ("BTC/USDT", "ETH/USDT"), depth: int = 20):
        self.database_url = database_url
        self.symbols = symbols
        self.depth = depth
        self.collector_id = "binance_testnet_orderbook_v1"
        self.exchange = ccxt.binanceusdm({"enableRateLimit": True, "timeout": 10_000})
        self.exchange.set_sandbox_mode(True)

    def collect_once(self, symbol: str) -> dict[str, Any]:
        started = time.time()
        book = self.exchange.fetch_order_book(symbol, limit=self.depth)
        ticker = self.exchange.fetch_ticker(symbol)
        received = datetime.now(UTC)
        bids = [[float(p), float(q)] for p, q in (book.get("bids") or [])[: self.depth]]
        asks = [[float(p), float(q)] for p, q in (book.get("asks") or [])[: self.depth]]
        best_bid = Decimal(str(bids[0][0]))
        best_ask = Decimal(str(asks[0][0]))
        last_price = Decimal(str(ticker.get("last") or (best_bid + best_ask) / 2))
        exchange_ts = int(book.get("timestamp") or ticker.get("timestamp") or int(time.time() * 1000))
        spread_bps = (best_ask - best_bid) / ((best_ask + best_bid) / 2) * Decimal(10_000)
        payload = {
            "symbol": symbol,
            "exchange_timestamp_ms": exchange_ts,
            "received_at": received,
            "last_price": last_price,
            "mark_price": ticker.get("info", {}).get("markPrice") if isinstance(ticker.get("info"), dict) else None,
            "best_bid": best_bid,
            "best_ask": best_ask,
            "spread_bps": spread_bps,
            "bids": bids,
            "asks": asks,
            "sequence": book.get("nonce"),
            "latency_ms": int((time.time() - started) * 1000),
            "clock_skew_ms": int(received.timestamp() * 1000) - exchange_ts,
        }
        session = get_session_factory(self.database_url)()
        try:
            inserted = persist_snapshot(session, payload)
            update_checkpoint(session, self.collector_id, exchange_timestamp_ms=exchange_ts, sequence=book.get("nonce"))
            refresh_health(session, symbol)
            return {"symbol": symbol, "inserted": inserted, "exchange_timestamp_ms": exchange_ts}
        finally:
            session.close()

    def run_forever(self, interval_seconds: float = 1.0) -> None:
        while True:
            for symbol in self.symbols:
                try:
                    self.collect_once(symbol)
                except Exception:  # collector must never take down scheduler
                    logger.exception("microstructure collection failed for %s", symbol)
            time.sleep(interval_seconds)
