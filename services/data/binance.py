"""Binance A-level market data normalization and collection helpers."""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Protocol
from urllib.parse import urlencode

from shared.binance_network import binance_urlopen_json
from shared.config import settings
from shared.models import (
    Exchange,
    MarketExtras,
    MarketOrderBookResponse,
    MarketTrade,
    MarketTradesResponse,
    OHLCVBar,
    OrderBookLevel,
    Timeframe,
)

from .repository import DataRepository
from .universe import platform_to_exchange_symbol

STABLE_OR_LEVERAGED_SUFFIXES = (
    "UP/USDT",
    "DOWN/USDT",
    "BULL/USDT",
    "BEAR/USDT",
)
STABLE_SYMBOLS = {"USDC/USDT", "FDUSD/USDT", "TUSD/USDT", "DAI/USDT", "USDP/USDT"}
USDM_EXCLUDED_BASES = {"USDC", "FDUSD", "TUSD", "BTCDOM"}
DEFAULT_BACKFILL_LIMIT = 1000
DEFAULT_FUNDING_LIMIT = 1000
DEFAULT_OHLCV_BACKFILL_DAYS = 14
DEFAULT_FUNDING_BACKFILL_DAYS = 30


def binance_spot_rest_base() -> str:
    return settings.binance_spot_rest_base.rstrip("/")


def binance_usdm_rest_base() -> str:
    return settings.binance_usdm_rest_base.rstrip("/")


def binance_spot_ws_base() -> str:
    return settings.binance_spot_ws_base.rstrip("/")


def binance_usdm_ws_base() -> str:
    return settings.binance_usdm_ws_base.rstrip("/")


# Backward-compatible aliases; prefer calling binance_*_base() at use sites.
BINANCE_SPOT_WS_BASE = "wss://stream.binance.com:9443/ws"
BINANCE_USDM_WS_BASE = "wss://fstream.binance.com/ws"
BINANCE_SPOT_REST_BASE = "https://api.binance.com"
BINANCE_USDM_REST_BASE = "https://fapi.binance.com"
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

    def select_top_usdm_symbols(self, tickers: Iterable[Mapping[str, Any]], *, limit: int = 20) -> list[str]:
        ranked: list[tuple[str, Decimal]] = []
        for payload in tickers:
            raw_symbol = str(payload.get("symbol", ""))
            if not raw_symbol.endswith("USDT"):
                continue
            base = raw_symbol.removesuffix("USDT")
            if base in USDM_EXCLUDED_BASES or base.endswith(("UP", "DOWN", "BULL", "BEAR")):
                continue
            quote_volume = payload.get("quoteVolume") or payload.get("quote_volume") or 0
            try:
                volume = Decimal(str(quote_volume))
            except Exception:
                volume = Decimal("0")
            if volume <= 0:
                continue
            ranked.append((f"{base}/USDT", volume))
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


def platform_symbol_to_binance_raw(symbol: str) -> str:
    """Convert platform symbols like BTC/USDT:USDT to Binance raw BTCUSDT."""

    return platform_to_exchange_symbol(symbol)


def fetch_usdm_exchange_info_symbols() -> list[dict[str, Any]]:
    """Fetch Binance USD-M exchangeInfo symbols without requiring CCXT."""

    payload = binance_urlopen_json(f"{binance_usdm_rest_base()}/fapi/v1/exchangeInfo")
    symbols = payload.get("symbols") if isinstance(payload, Mapping) else None
    return symbols if isinstance(symbols, list) else []


def stream_symbol(symbol: str) -> str:
    base = symbol.replace(":USDT", "").replace("/", "")
    return base.lower()


def websocket_connect_options(connect: Callable[..., Any]) -> dict[str, Any]:
    """Disable ambient proxy discovery on websockets versions that support it."""

    try:
        parameters = inspect.signature(connect).parameters
    except (TypeError, ValueError):
        return {}
    return {"proxy": None} if "proxy" in parameters else {}


def fetch_usdm_24h_tickers() -> list[dict[str, Any]]:
    """Fetch Binance USD-M 24h tickers without requiring CCXT."""

    payload = binance_urlopen_json(f"{binance_usdm_rest_base()}/fapi/v1/ticker/24hr")
    return payload if isinstance(payload, list) else []


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


def normalize_ws_kline_event_with_status(
    payload: Mapping[str, Any], *, symbol: str, timeframe: str, exchange: str = "binance"
) -> tuple[OHLCVBar, bool] | None:
    """Normalize a Binance WS kline event and expose whether the candle closed."""

    event = payload.get("data") if "data" in payload else payload
    kline = event.get("k") if isinstance(event, Mapping) else None
    if not isinstance(kline, Mapping):
        return None
    return (
        OHLCVBar(
            symbol=symbol,
            exchange=Exchange(exchange),
            timeframe=Timeframe(timeframe),
            time=_from_millis(kline["t"]),
            open=Decimal(str(kline["o"])),
            high=Decimal(str(kline["h"])),
            low=Decimal(str(kline["l"])),
            close=Decimal(str(kline["c"])),
            volume=Decimal(str(kline["v"])),
        ),
        bool(kline.get("x")),
    )


def normalize_ws_kline_event(
    payload: Mapping[str, Any], *, symbol: str, timeframe: str, exchange: str = "binance"
) -> OHLCVBar | None:
    """Normalize a Binance WS kline event, ignoring in-progress candles."""

    normalized = normalize_ws_kline_event_with_status(payload, symbol=symbol, timeframe=timeframe, exchange=exchange)
    if normalized is None:
        return None
    bar, closed = normalized
    return bar if closed else None


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

    def fetch_order_book(self, symbol: str, limit: int | None = None) -> Mapping[str, Any]: ...

    def fetch_trades(
        self, symbol: str, since: int | None = None, limit: int | None = None
    ) -> list[Mapping[str, Any]]: ...

    def close(self) -> Any: ...


class BinancePublicRestExchange:
    """Minimal Binance public REST adapter used when CCXT is unavailable."""

    def __init__(self, *, market_type: str, base_url: str | None = None):
        self.market_type = market_type
        if base_url is not None:
            self.base_url = base_url.rstrip("/")
        else:
            self.base_url = binance_usdm_rest_base() if market_type == "usdm" else binance_spot_rest_base()

    def load_markets(self) -> None:
        return None

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        since: int | None = None,
        limit: int | None = None,
    ) -> list[Sequence]:
        payload = self._get(
            "/fapi/v1/klines" if self.market_type == "usdm" else "/api/v3/klines",
            {
                "symbol": platform_symbol_to_binance_raw(symbol),
                "interval": timeframe,
                "limit": max(1, min(limit or 500, 1000)),
                **({"startTime": since} if since is not None else {}),
            },
        )
        return [
            [row[0], row[1], row[2], row[3], row[4], row[5]]
            for row in payload
            if isinstance(row, Sequence) and len(row) >= 6
        ]

    def fetch_funding_rate_history(
        self,
        symbol: str,
        since: int | None = None,
        limit: int | None = None,
    ) -> list[Mapping[str, Any]]:
        if self.market_type != "usdm":
            return []
        payload = self._get(
            "/fapi/v1/fundingRate",
            {
                "symbol": platform_symbol_to_binance_raw(symbol),
                "limit": max(1, min(limit or 100, 1000)),
                **({"startTime": since} if since is not None else {}),
            },
        )
        return payload if isinstance(payload, list) else []

    def fetch_order_book(self, symbol: str, limit: int | None = None) -> Mapping[str, Any]:
        payload = self._get(
            "/fapi/v1/depth" if self.market_type == "usdm" else "/api/v3/depth",
            {
                "symbol": platform_symbol_to_binance_raw(symbol),
                "limit": max(5, min(limit or 20, 100)),
            },
        )
        return payload if isinstance(payload, Mapping) else {}

    def fetch_trades(
        self,
        symbol: str,
        since: int | None = None,
        limit: int | None = None,
    ) -> list[Mapping[str, Any]]:
        payload = self._get(
            "/fapi/v1/trades" if self.market_type == "usdm" else "/api/v3/trades",
            {
                "symbol": platform_symbol_to_binance_raw(symbol),
                "limit": max(1, min(limit or 50, 1000)),
            },
        )
        if not isinstance(payload, list):
            return []
        trades: list[Mapping[str, Any]] = []
        for row in payload:
            if not isinstance(row, Mapping):
                continue
            is_buyer_maker = bool(row.get("isBuyerMaker"))
            trades.append(
                {
                    "id": row.get("id"),
                    "timestamp": row.get("time"),
                    "price": row.get("price"),
                    "amount": row.get("qty"),
                    "side": "sell" if is_buyer_maker else "buy",
                }
            )
        return trades

    def fapiPublicGetPremiumIndex(self, params: Mapping[str, Any]) -> Mapping[str, Any]:
        payload = self._get("/fapi/v1/premiumIndex", {"symbol": params.get("symbol")})
        return payload if isinstance(payload, Mapping) else {}

    def close(self) -> None:
        return None

    def _get(self, path: str, params: Mapping[str, Any]) -> Any:
        query = urlencode({key: value for key, value in params.items() if value is not None})
        return binance_urlopen_json(f"{self.base_url}{path}?{query}")


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
            with contextlib.suppress(ImportError):
                import ccxt  # noqa: F401

        self.spot_exchange = spot_exchange or BinancePublicRestExchange(
            market_type="spot",
            base_url=binance_spot_rest_base(),
        )
        self.usdm_exchange = usdm_exchange or BinancePublicRestExchange(
            market_type="usdm",
            base_url=binance_usdm_rest_base(),
        )
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

    def fetch_recent_ohlcv(self, *, symbol: str, timeframe: str, limit: int = 300) -> list[OHLCVBar]:
        self.load_markets()
        safe_limit = max(1, min(limit, DEFAULT_BACKFILL_LIMIT))
        rows = self._exchange_for_symbol(symbol).fetch_ohlcv(symbol, timeframe, None, safe_limit)
        return normalize_ohlcv_rows(rows=rows, symbol=symbol, timeframe=timeframe)

    def fetch_live_order_book(self, *, symbol: str, limit: int = 20) -> MarketOrderBookResponse:
        self.load_markets()
        safe_limit = max(5, min(limit, 100))
        payload = self._exchange_for_symbol(symbol).fetch_order_book(symbol, safe_limit)
        bids = _normalize_book_side(payload.get("bids", []), limit=safe_limit)
        asks = _normalize_book_side(payload.get("asks", []), limit=safe_limit)
        return MarketOrderBookResponse(
            symbol=symbol,
            data_status="ok" if bids or asks else "empty",
            source="binance_public_rest",
            last_update_id=_int_or_none(payload.get("nonce")),
            bids=bids,
            asks=asks,
        )

    def fetch_live_trades(self, *, symbol: str, limit: int = 50) -> MarketTradesResponse:
        self.load_markets()
        safe_limit = max(1, min(limit, 100))
        rows = self._exchange_for_symbol(symbol).fetch_trades(symbol, None, safe_limit)
        trades: list[MarketTrade] = []
        for row in rows[-safe_limit:]:
            price = row.get("price")
            amount = row.get("amount")
            if price is None or amount is None:
                continue
            trades.append(
                MarketTrade(
                    trade_id=str(row.get("id")) if row.get("id") is not None else None,
                    price=Decimal(str(price)),
                    quantity=Decimal(str(amount)),
                    side=str(row.get("side") or "unknown"),
                    trade_time=_from_millis(row["timestamp"]) if row.get("timestamp") is not None else None,
                )
            )
        return MarketTradesResponse(
            symbol=symbol,
            data_status="ok" if trades else "empty",
            source="binance_public_rest",
            trades=trades,
        )

    def fetch_premium_index(self, *, symbol: str) -> MarketExtras | None:
        raw_symbol = platform_symbol_to_binance_raw(symbol)
        getter = getattr(self.usdm_exchange, "fapiPublicGetPremiumIndex", None)
        if getter is None:
            return None
        payload = getter({"symbol": raw_symbol})
        if not isinstance(payload, Mapping):
            return None
        timestamp = payload.get("time") or payload.get("nextFundingTime")
        rate = payload.get("lastFundingRate")
        open_interest = payload.get("openInterest")
        if timestamp is None and rate is None and open_interest is None:
            return None
        return MarketExtras(
            symbol=spot_to_usdm_perp_symbol(symbol),
            time=_from_millis(timestamp or int(datetime.now(UTC).timestamp() * 1000)),
            funding_rate=Decimal(str(rate)) if rate is not None else None,
            open_interest=Decimal(str(open_interest)) if open_interest is not None else None,
        )

    def close(self) -> None:
        for exchange in (self.spot_exchange, self.usdm_exchange):
            close = getattr(exchange, "close", None)
            if callable(close):
                close()


def _normalize_book_side(rows: Iterable[Sequence], *, limit: int) -> list[OrderBookLevel]:
    levels: list[OrderBookLevel] = []
    running_total = Decimal("0")
    for row in list(rows)[:limit]:
        if len(row) < 2:
            continue
        quantity = Decimal(str(row[1]))
        running_total += quantity
        levels.append(
            OrderBookLevel(
                price=Decimal(str(row[0])),
                quantity=quantity,
                total=running_total,
            )
        )
    return levels


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


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

    def __init__(
        self,
        *,
        data_repo: DataRepository,
        on_candle: Callable[[OHLCVBar], Awaitable[None]] | None = None,
        on_close: Callable[[], None] | None = None,
    ):
        self.data_repo = data_repo
        self.on_candle = on_candle
        self.on_close = on_close

    def build_kline_stream_url(self, *, symbol: str, timeframe: str) -> str:
        base = binance_usdm_ws_base() if symbol.endswith(":USDT") else binance_spot_ws_base()
        return f"{base}/{stream_symbol(symbol)}@kline_{timeframe}"

    def build_mark_price_stream_url(self, *, symbol: str) -> str:
        return f"{binance_usdm_ws_base()}/{stream_symbol(symbol)}@markPrice@1s"

    def persist_kline_payload(self, payload: Mapping[str, Any], *, symbol: str, timeframe: str) -> OHLCVBar | None:
        bar = normalize_ws_kline_event(payload, symbol=symbol, timeframe=timeframe)
        if bar is not None:
            self.data_repo.store_ohlcv_bars([bar])
        return bar

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
            bar = self.persist_kline_payload(payload, symbol=symbol, timeframe=timeframe)
            if bar is not None and self.on_candle is not None:
                await self.on_candle(bar)

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
        async with websockets.connect(
            url,
            ping_interval=20,
            ping_timeout=20,
            **websocket_connect_options(websockets.connect),
        ) as websocket:
            async for ws_message in websocket:
                message_text = ws_message.decode("utf-8") if isinstance(ws_message, bytes) else str(ws_message)
                yield json.loads(message_text)

    def close(self) -> None:
        if self.on_close is not None:
            self.on_close()


async def run_live_collector_forever(
    *,
    collector_factory: Callable[[], BinanceLiveMarketCollector],
    symbol: str = "BTC/USDT",
    perp_symbol: str = "BTC/USDT:USDT",
    timeframe: str = "1m",
    reconnect_error_handler: Callable[[Exception], Awaitable[None]] | None = None,
    reconnect_delay_seconds: float = 5.0,
    max_reconnect_delay_seconds: float = 60.0,
    backoff_factor: float = 1.5,
) -> None:
    """Run public WS collectors with exponential-backoff reconnect and state recovery.

    On disconnect the delay grows exponentially (capped at
    ``max_reconnect_delay_seconds``) so that a prolonged outage does not
    hammer the exchange. A successful connection resets the backoff. Each
    reconnect creates a fresh collector so internal buffers/state are rebuilt
    from scratch — this is the state-recovery mechanism for 7×24 operation.
    """

    from shared.logging import get_logger

    _reconnect_logger = get_logger(__name__)
    current_delay = reconnect_delay_seconds
    attempt = 0

    while True:
        collector = collector_factory()
        try:
            attempt += 1
            _reconnect_logger.info(
                "WS collector starting",
                extra={"symbol": symbol, "perp_symbol": perp_symbol, "attempt": attempt},
            )
            await asyncio.gather(
                collector.consume_kline_stream(symbol=symbol, timeframe=timeframe),
                collector.consume_kline_stream(symbol=perp_symbol, timeframe=timeframe),
                collector.consume_mark_price_stream(symbol=perp_symbol),
            )
            # Graceful close — reset backoff for the next connection.
            current_delay = reconnect_delay_seconds
        except Exception as exc:
            if reconnect_error_handler is not None:
                await reconnect_error_handler(exc)
            _reconnect_logger.warning(
                "WS collector disconnected, reconnecting",
                extra={
                    "symbol": symbol,
                    "attempt": attempt,
                    "delay_seconds": current_delay,
                    "error": str(exc),
                },
            )
            await asyncio.sleep(max(current_delay, 0.0))
            # Exponential backoff with cap — prevents exchange hammering.
            current_delay = min(current_delay * backoff_factor, max_reconnect_delay_seconds)
        finally:
            collector.close()
