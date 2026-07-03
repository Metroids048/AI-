"""Binance A-level market data normalization and collection helpers."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Protocol

from shared.models import Exchange, MarketExtras, OHLCVBar, Timeframe

from .repository import DataRepository

STABLE_OR_LEVERAGED_SUFFIXES = (
    "UP/USDT",
    "DOWN/USDT",
    "BULL/USDT",
    "BEAR/USDT",
)
STABLE_SYMBOLS = {"USDC/USDT", "FDUSD/USDT", "TUSD/USDT", "DAI/USDT", "USDP/USDT"}
DEFAULT_BACKFILL_LIMIT = 1000
DEFAULT_FUNDING_LIMIT = 1000
DEFAULT_OHLCV_BACKFILL_DAYS = 14
DEFAULT_FUNDING_BACKFILL_DAYS = 30
BINANCE_SPOT_WS_BASE = "wss://stream.binance.com:9443/ws"
BINANCE_USDM_WS_BASE = "wss://fstream.binance.com/ws"
TIMEFRAME_TO_SECONDS = {
    "1m": 60,
    "5m": 5 * 60,
    "15m": 15 * 60,
    "1h": 60 * 60,
    "4h": 4 * 60 * 60,
    "1d": 24 * 60 * 60,
}


class BinanceUniverseSelector:
    """Select liquid Binance USDT pairs for initial ingestion jobs."""

    def select_top_symbols(self, tickers: Mapping[str, Mapping], *, limit: int = 20) -> list[str]:
        ranked: list[tuple[str, Decimal]] = []
        for symbol, payload in tickers.items():
            if not symbol.endswith("/USDT"):
                continue
            if symbol in STABLE_SYMBOLS or symbol.endswith(STABLE_OR_LEVERAGED_SUFFIXES):
                continue
            quote_volume = payload.get("quoteVolume") or payload.get("quote_volume") or 0
            try:
                volume = Decimal(str(quote_volume))
            except Exception:
                volume = Decimal("0")
            ranked.append((symbol, volume))
        ranked.sort(key=lambda item: item[1], reverse=True)
        return [symbol for symbol, _ in ranked[:limit]]


def _from_millis(value: int | float | str) -> datetime:
    return datetime.fromtimestamp(int(value) / 1000, tz=UTC)


def _to_millis(value: datetime) -> int:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return int(value.timestamp() * 1000)


def _timeframe_millis(timeframe: str) -> int:
    if timeframe not in TIMEFRAME_TO_SECONDS:
        raise ValueError(f"unsupported timeframe: {timeframe}")
    return TIMEFRAME_TO_SECONDS[timeframe] * 1000


def normalize_datetime(value: datetime | None, *, default: datetime) -> datetime:
    timestamp = value or default
    if timestamp.tzinfo is None:
        return timestamp.replace(tzinfo=UTC)
    return timestamp.astimezone(UTC)


def spot_to_usdm_perp_symbol(symbol: str) -> str:
    if symbol.endswith(":USDT"):
        return symbol
    if not symbol.endswith("/USDT"):
        raise ValueError(f"unsupported Binance USD-M symbol: {symbol}")
    return f"{symbol}:USDT"


def stream_symbol(symbol: str) -> str:
    base = symbol.replace(":USDT", "").replace("/", "")
    return base.lower()


def normalize_ohlcv_rows(
    *, rows: Iterable[Sequence], symbol: str, timeframe: str, exchange: str = "binance"
) -> list[OHLCVBar]:
    """Normalize CCXT OHLCV rows into the platform OHLCV contract."""

    bars: list[OHLCVBar] = []
    for row in rows:
        if len(row) < 6:
            raise ValueError("OHLCV row must contain timestamp, open, high, low, close, volume")
        bars.append(
            OHLCVBar(
                symbol=symbol,
                exchange=Exchange(exchange),
                timeframe=Timeframe(timeframe),
                time=_from_millis(row[0]),
                open=Decimal(str(row[1])),
                high=Decimal(str(row[2])),
                low=Decimal(str(row[3])),
                close=Decimal(str(row[4])),
                volume=Decimal(str(row[5])),
            )
        )
    return bars


def normalize_funding_rate_history(*, rows: Iterable[Mapping], symbol: str) -> list[MarketExtras]:
    """Normalize Binance funding-rate records into MarketExtras."""

    extras: list[MarketExtras] = []
    for row in rows:
        timestamp = row.get("timestamp") or row.get("fundingTime") or row.get("time")
        if timestamp is None:
            raise ValueError("funding row missing timestamp")
        rate = row.get("fundingRate") or row.get("funding_rate") or row.get("rate")
        extras.append(
            MarketExtras(
                symbol=symbol,
                time=_from_millis(timestamp),
                funding_rate=Decimal(str(rate)) if rate is not None else None,
            )
        )
    return extras


def normalize_ws_kline_event(
    payload: Mapping[str, Any], *, symbol: str, timeframe: str, exchange: str = "binance"
) -> OHLCVBar | None:
    """Normalize a Binance WS kline event, ignoring in-progress candles."""

    event = payload.get("data") if "data" in payload else payload
    kline = event.get("k") if isinstance(event, Mapping) else None
    if not isinstance(kline, Mapping) or not kline.get("x"):
        return None
    return OHLCVBar(
        symbol=symbol,
        exchange=Exchange(exchange),
        timeframe=Timeframe(timeframe),
        time=_from_millis(kline["t"]),
        open=Decimal(str(kline["o"])),
        high=Decimal(str(kline["h"])),
        low=Decimal(str(kline["l"])),
        close=Decimal(str(kline["c"])),
        volume=Decimal(str(kline["v"])),
    )


def normalize_ws_mark_price_event(payload: Mapping[str, Any], *, symbol: str) -> MarketExtras | None:
    """Normalize Binance USD-M mark-price updates into funding-rate extras."""

    event = payload.get("data") if "data" in payload else payload
    if not isinstance(event, Mapping):
        return None
    timestamp = event.get("E") or event.get("T")
    rate = event.get("r")
    if timestamp is None or rate is None:
        return None
    return MarketExtras(
        symbol=symbol,
        time=_from_millis(timestamp),
        funding_rate=Decimal(str(rate)),
    )


class CcxtLikeExchange(Protocol):
    def load_markets(self) -> Any: ...

    def fetch_ohlcv(
        self, symbol: str, timeframe: str, since: int | None = None, limit: int | None = None
    ) -> list[Sequence]: ...

    def fetch_funding_rate_history(
        self, symbol: str, since: int | None = None, limit: int | None = None
    ) -> list[Mapping[str, Any]]: ...

    def close(self) -> Any: ...


@dataclass
class BinanceBackfillResult:
    symbol: str
    timeframe: str | None
    rows_fetched: int
    rows_written: int
    start_at: datetime
    end_at: datetime


class BinanceCcxtClient:
    """Small CCXT adapter for Binance public market-data calls."""

    def __init__(
        self,
        *,
        spot_exchange: CcxtLikeExchange | None = None,
        usdm_exchange: CcxtLikeExchange | None = None,
    ):
        if spot_exchange is None or usdm_exchange is None:
            import ccxt

        self.spot_exchange = spot_exchange or ccxt.binance({"enableRateLimit": True})
        self.usdm_exchange = usdm_exchange or ccxt.binanceusdm({"enableRateLimit": True})
        self._markets_loaded = False

    def load_markets(self) -> None:
        if self._markets_loaded:
            return
        self.spot_exchange.load_markets()
        self.usdm_exchange.load_markets()
        self._markets_loaded = True

    def _exchange_for_symbol(self, symbol: str) -> CcxtLikeExchange:
        return self.usdm_exchange if symbol.endswith(":USDT") else self.spot_exchange

    def fetch_ohlcv_history(
        self,
        *,
        symbol: str,
        timeframe: str,
        start_at: datetime,
        end_at: datetime,
        limit: int = DEFAULT_BACKFILL_LIMIT,
    ) -> list[OHLCVBar]:
        self.load_markets()
        exchange = self._exchange_for_symbol(symbol)
        start_ms = _to_millis(start_at)
        end_ms = _to_millis(end_at)
        step_ms = _timeframe_millis(timeframe)
        since = start_ms
        rows: list[Sequence] = []
        while since <= end_ms:
            batch = exchange.fetch_ohlcv(symbol, timeframe, since, limit)
            if not batch:
                since += step_ms * limit
                continue
            filtered = [row for row in batch if start_ms <= int(row[0]) <= end_ms]
            rows.extend(filtered)
            last_ms = int(batch[-1][0])
            next_since = last_ms + step_ms
            if next_since <= since:
                break
            since = next_since
            if len(batch) < limit:
                break
        unique = {int(row[0]): row for row in rows}
        return normalize_ohlcv_rows(
            rows=[unique[key] for key in sorted(unique)],
            symbol=symbol,
            timeframe=timeframe,
        )

    def fetch_funding_rate_history(
        self,
        *,
        symbol: str,
        start_at: datetime,
        end_at: datetime,
        limit: int = DEFAULT_FUNDING_LIMIT,
    ) -> list[MarketExtras]:
        self.load_markets()
        start_ms = _to_millis(start_at)
        end_ms = _to_millis(end_at)
        since = start_ms
        rows: list[Mapping[str, Any]] = []
        while since <= end_ms:
            batch = self.usdm_exchange.fetch_funding_rate_history(symbol, since, limit)
            if not batch:
                break
            filtered = []
            for row in batch:
                timestamp = row.get("timestamp") or row.get("fundingTime") or row.get("time")
                if timestamp is None:
                    continue
                timestamp_ms = int(timestamp)
                if start_ms <= timestamp_ms <= end_ms:
                    filtered.append(row)
            rows.extend(filtered)
            last_timestamp = batch[-1].get("timestamp") or batch[-1].get("fundingTime") or batch[-1].get("time")
            if last_timestamp is None:
                break
            next_since = int(last_timestamp) + 1
            if next_since <= since:
                break
            since = next_since
            if len(batch) < limit:
                break
        unique: dict[int, Mapping[str, Any]] = {}
        for row in rows:
            timestamp = row.get("timestamp") or row.get("fundingTime") or row.get("time")
            if timestamp is not None:
                unique[int(timestamp)] = row
        return normalize_funding_rate_history(
            rows=[unique[key] for key in sorted(unique)],
            symbol=symbol,
        )

    def close(self) -> None:
        for exchange in (self.spot_exchange, self.usdm_exchange):
            close = getattr(exchange, "close", None)
            if callable(close):
                close()


class BinanceBackfillService:
    """Backfill Binance public market data into the DataRepository."""

    def __init__(self, *, data_repo: DataRepository, client: BinanceCcxtClient | None = None):
        self.data_repo = data_repo
        self.client = client or BinanceCcxtClient()

    def backfill_ohlcv(
        self,
        *,
        symbols: Sequence[str],
        timeframe: str,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
    ) -> list[BinanceBackfillResult]:
        end = normalize_datetime(end_at, default=datetime.now(UTC))
        start = normalize_datetime(start_at, default=end - timedelta(days=DEFAULT_OHLCV_BACKFILL_DAYS))
        results: list[BinanceBackfillResult] = []
        for symbol in symbols:
            bars = self.client.fetch_ohlcv_history(
                symbol=symbol,
                timeframe=timeframe,
                start_at=start,
                end_at=end,
            )
            written = self.data_repo.store_ohlcv_bars(bars)
            results.append(
                BinanceBackfillResult(
                    symbol=symbol,
                    timeframe=timeframe,
                    rows_fetched=len(bars),
                    rows_written=written,
                    start_at=start,
                    end_at=end,
                )
            )
        return results

    def backfill_funding(
        self,
        *,
        symbols: Sequence[str],
        start_at: datetime | None = None,
        end_at: datetime | None = None,
    ) -> list[BinanceBackfillResult]:
        end = normalize_datetime(end_at, default=datetime.now(UTC))
        start = normalize_datetime(start_at, default=end - timedelta(days=DEFAULT_FUNDING_BACKFILL_DAYS))
        results: list[BinanceBackfillResult] = []
        for symbol in symbols:
            extras = self.client.fetch_funding_rate_history(
                symbol=spot_to_usdm_perp_symbol(symbol),
                start_at=start,
                end_at=end,
            )
            written = self.data_repo.store_market_extras(extras)
            results.append(
                BinanceBackfillResult(
                    symbol=spot_to_usdm_perp_symbol(symbol),
                    timeframe=None,
                    rows_fetched=len(extras),
                    rows_written=written,
                    start_at=start,
                    end_at=end,
                )
            )
        return results


class BinanceLiveMarketCollector:
    """Persist closed Binance Kline candles and funding-rate updates from WS streams."""

    def __init__(self, *, data_repo: DataRepository):
        self.data_repo = data_repo

    def build_kline_stream_url(self, *, symbol: str, timeframe: str) -> str:
        base = BINANCE_USDM_WS_BASE if symbol.endswith(":USDT") else BINANCE_SPOT_WS_BASE
        return f"{base}/{stream_symbol(symbol)}@kline_{timeframe}"

    def build_mark_price_stream_url(self, *, symbol: str) -> str:
        return f"{BINANCE_USDM_WS_BASE}/{stream_symbol(symbol)}@markPrice@1s"

    def persist_kline_payload(self, payload: Mapping[str, Any], *, symbol: str, timeframe: str) -> int:
        bar = normalize_ws_kline_event(payload, symbol=symbol, timeframe=timeframe)
        return self.data_repo.store_ohlcv_bars([bar]) if bar is not None else 0

    def persist_mark_price_payload(self, payload: Mapping[str, Any], *, symbol: str) -> int:
        extra = normalize_ws_mark_price_event(payload, symbol=spot_to_usdm_perp_symbol(symbol))
        return self.data_repo.store_market_extras([extra]) if extra is not None else 0

    async def consume_kline_stream(
        self,
        *,
        symbol: str,
        timeframe: str,
        messages: AsyncIterator[str | Mapping[str, Any]] | None = None,
    ) -> None:
        async for payload in self._message_source(
            self.build_kline_stream_url(symbol=symbol, timeframe=timeframe),
            messages,
        ):
            self.persist_kline_payload(payload, symbol=symbol, timeframe=timeframe)

    async def consume_mark_price_stream(
        self,
        *,
        symbol: str,
        messages: AsyncIterator[str | Mapping[str, Any]] | None = None,
    ) -> None:
        async for payload in self._message_source(
            self.build_mark_price_stream_url(symbol=symbol),
            messages,
        ):
            self.persist_mark_price_payload(payload, symbol=symbol)

    async def _message_source(
        self,
        url: str,
        messages: AsyncIterator[str | Mapping[str, Any]] | None,
    ) -> AsyncIterator[Mapping[str, Any]]:
        if messages is not None:
            async for injected_message in messages:
                yield json.loads(injected_message) if isinstance(injected_message, str) else injected_message
            return
        try:
            import websockets
        except ImportError as exc:  # pragma: no cover - depends on runtime extras
            raise RuntimeError("websockets package is required for Binance live collectors") from exc
        async with websockets.connect(url, ping_interval=20, ping_timeout=20) as websocket:
            async for ws_message in websocket:
                message_text = ws_message.decode("utf-8") if isinstance(ws_message, bytes) else str(ws_message)
                yield json.loads(message_text)


async def run_live_collector_forever(
    *,
    collector_factory: Callable[[], BinanceLiveMarketCollector],
    symbol: str = "BTC/USDT",
    perp_symbol: str = "BTC/USDT:USDT",
    timeframe: str = "1m",
) -> None:
    """Run first-tranche public WS collectors with conservative restart delay."""

    while True:
        collector = collector_factory()
        try:
            await asyncio.gather(
                collector.consume_kline_stream(symbol=symbol, timeframe=timeframe),
                collector.consume_kline_stream(symbol=perp_symbol, timeframe=timeframe),
                collector.consume_mark_price_stream(symbol=perp_symbol),
            )
        except Exception:
            await asyncio.sleep(5)
