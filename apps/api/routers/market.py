"""Read-only market data APIs for the Paper trading console."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Mapping
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from datetime import UTC, datetime
from decimal import Decimal
from hmac import compare_digest
from threading import Lock
from time import monotonic
from typing import Any

from fastapi import APIRouter, Depends, Query, WebSocket, status
from fastapi.websockets import WebSocketDisconnect
from sqlalchemy.orm import Session

from apps.api.config import settings
from services.data import DataRepository, MarketQueryService, live_feed_bus
from services.data.binance import (
    BinanceCcxtClient,
    binance_usdm_ws_base,
    fetch_usdm_24h_tickers,
    fetch_usdm_exchange_info_symbols,
    normalize_ws_kline_event_with_status,
    stream_symbol,
)
from services.data.capabilities import list_exchange_capabilities
from services.data.macro_calendar import MacroCalendarService
from services.data.news import NewsIngestionService
from services.database import get_db_session, get_session_factory
from services.strategy_library import AgentTaskRepository, ReviewRepository, StrategyRepository
from shared.models import (
    CollectionResponse,
    ExchangeCapability,
    FundingArbitrageSignal,
    MarketOrderBookResponse,
    MarketSnapshot,
    MarketTrade,
    MarketTradesResponse,
    MarketUniverseItem,
    OhlcvSeriesResponse,
    OrderBookLevel,
)

router = APIRouter(prefix="/market", tags=["market"])

_EXCHANGE_INFO_TTL_SECONDS = 300.0
_EXCHANGE_INFO_WAIT_SECONDS = 0.25
_exchange_info_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="binance-exchange-info")
_exchange_info_lock = Lock()
_exchange_info_future: Future[list[dict[str, Any]]] | None = None
_exchange_info_cache: list[dict[str, Any]] | None = None
_exchange_info_cached_at = 0.0


def _market_service(db: Session) -> MarketQueryService:
    return MarketQueryService(DataRepository(db))


def _live_market_service(db: Session) -> MarketQueryService:
    client = BinanceCcxtClient() if settings.binance_live_market_enabled else None
    return MarketQueryService(DataRepository(db), binance_client=client)


def _live_market_reads_enabled() -> bool:
    """Keep the desktop API responsive while its scheduler refreshes persisted data."""

    return settings.binance_live_market_enabled and os.getenv("PAPER_CONSOLE_API_ONLY", "false").lower() != "true"


@router.get("/snapshot", response_model=MarketSnapshot)
def get_market_snapshot(
    symbol: str = Query(default="BTC/USDT"),
    perp_symbol: str = Query(default="BTC/USDT:USDT"),
    timeframe: str = Query(default="1h"),
    db: Session = Depends(get_db_session),
) -> MarketSnapshot:
    live_reads = _live_market_reads_enabled()
    service = _live_market_service(db) if live_reads else _market_service(db)
    method = service.get_live_snapshot if live_reads else service.get_snapshot
    return method(
        symbol=symbol,
        perp_symbol=perp_symbol,
        timeframe=timeframe,
    )


@router.get("/ohlcv", response_model=OhlcvSeriesResponse)
def get_ohlcv_series(
    symbol: str = Query(default="BTC/USDT"),
    timeframe: str = Query(default="1h"),
    limit: int = Query(default=300, ge=1, le=1000),
    db: Session = Depends(get_db_session),
) -> OhlcvSeriesResponse:
    live_reads = _live_market_reads_enabled()
    service = _live_market_service(db) if live_reads else _market_service(db)
    method = service.get_live_ohlcv_series if live_reads else service.get_ohlcv_series
    return method(
        symbol=symbol,
        timeframe=timeframe,
        limit=limit,
    )


@router.get("/order-book", response_model=MarketOrderBookResponse)
def get_order_book(
    symbol: str = Query(default="BTC/USDT:USDT"),
    limit: int = Query(default=20, ge=5, le=100),
    db: Session = Depends(get_db_session),
) -> MarketOrderBookResponse:
    service = _live_market_service(db) if _live_market_reads_enabled() else _market_service(db)
    return service.get_order_book(symbol=symbol, limit=limit)


@router.get("/trades", response_model=MarketTradesResponse)
def get_recent_trades(
    symbol: str = Query(default="BTC/USDT:USDT"),
    limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db_session),
) -> MarketTradesResponse:
    service = _live_market_service(db) if _live_market_reads_enabled() else _market_service(db)
    return service.get_recent_trades(symbol=symbol, limit=limit)


@router.websocket("/ohlcv/stream")
async def stream_ohlcv_series(
    websocket: WebSocket,
    symbol: str = Query(default="BTC/USDT"),
    timeframe: str = Query(default="1h"),
    limit: int = Query(default=300, ge=1, le=1000),
    token: str = Query(default=""),
) -> None:
    """Push persisted Kline updates to the console as the Binance collector writes them."""

    if not _websocket_token_is_valid(token):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    await websocket.accept()
    sequence = 0
    subscription = await live_feed_bus.subscribe(symbol=symbol, timeframe=timeframe)
    try:
        payload = _latest_ohlcv_payload(symbol=symbol, timeframe=timeframe, limit=limit)
        await websocket.send_json(
            {
                "event": "ohlcv_snapshot",
                "source": payload.get("source", "persisted_market_data"),
                "feed_status": live_feed_bus.status(symbol=symbol, timeframe=timeframe),
                "sequence": sequence,
                "payload": payload,
            }
        )
        sequence += 1
        while True:
            try:
                message = await asyncio.wait_for(subscription.queue.get(), timeout=30.0)
                await websocket.send_json({**message, "sequence": sequence})
                sequence += 1
            except TimeoutError:
                await websocket.send_json(
                    {
                        "event": "feed_status",
                        "feed_status": live_feed_bus.status(symbol=symbol, timeframe=timeframe),
                        "sequence": sequence,
                    }
                )
                sequence += 1
    except WebSocketDisconnect:
        return
    finally:
        await live_feed_bus.unsubscribe(subscription)


@router.websocket("/exchange-stream")
async def stream_exchange_terminal(
    websocket: WebSocket,
    symbol: str = Query(default="BTC/USDT"),
    perp_symbol: str = Query(default="BTC/USDT:USDT"),
    timeframe: str = Query(default="1m"),
    limit: int = Query(default=300, ge=1, le=1000),
    token: str = Query(default=""),
) -> None:
    """Trading-page market stream: snapshot, live kline, order book, trades, and feed status."""

    if not _websocket_token_is_valid(token):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    await websocket.accept()
    sequence = 0
    try:
        await websocket.send_json(
            {
                "event": "exchange_snapshot",
                "sequence": sequence,
                "payload": _latest_exchange_payload(
                    symbol=symbol,
                    perp_symbol=perp_symbol,
                    timeframe=timeframe,
                    limit=limit,
                ),
            }
        )
        sequence += 1
        if not settings.binance_live_ws_enabled:
            await _stream_exchange_rest_poll(
                websocket=websocket,
                symbol=symbol,
                perp_symbol=perp_symbol,
                timeframe=timeframe,
                limit=limit,
                sequence=sequence,
                reason="binance_live_ws_disabled",
            )
            return
        try:
            sequence = await _stream_exchange_binance_ws(
                websocket=websocket,
                symbol=symbol,
                perp_symbol=perp_symbol,
                timeframe=timeframe,
                sequence=sequence,
            )
        except Exception as exc:
            await _stream_exchange_rest_poll(
                websocket=websocket,
                symbol=symbol,
                perp_symbol=perp_symbol,
                timeframe=timeframe,
                limit=limit,
                sequence=sequence,
                reason=f"binance_ws_error:{exc.__class__.__name__}",
            )
    except (WebSocketDisconnect, RuntimeError):
        return


@router.get("/universe", response_model=CollectionResponse[MarketUniverseItem])
def get_market_universe(
    limit: int = Query(default=20, ge=1, le=50),
    mode: str = Query(default="dynamic", pattern="^(dynamic|fixed_top20)$"),
    db: Session = Depends(get_db_session),
) -> CollectionResponse[MarketUniverseItem]:
    tickers = _fetch_binance_usdm_tickers() if settings.binance_live_universe_enabled and mode == "dynamic" else None
    exchange_info = _fetch_binance_exchange_info_symbols() if mode == "fixed_top20" else None
    items = _market_service(db).get_market_universe(
        limit=limit,
        tickers=tickers,
        mode=mode,
        exchange_info_symbols=exchange_info,
    )
    return CollectionResponse(items=items, total=len(items))


@router.get("/funding-arbitrage-signal", response_model=FundingArbitrageSignal)
def get_funding_arbitrage_signal(
    symbol: str = Query(default="BTC/USDT"),
    perp_symbol: str = Query(default="BTC/USDT:USDT"),
    timeframe: str = Query(default="1h"),
    fee_bps: float = Query(default=8.0, ge=0),
    slippage_bps: float = Query(default=6.0, ge=0),
    db: Session = Depends(get_db_session),
) -> FundingArbitrageSignal:
    service = _live_market_service(db) if _live_market_reads_enabled() else _market_service(db)
    return service.get_funding_arbitrage_signal(
        symbol=symbol,
        perp_symbol=perp_symbol,
        timeframe=timeframe,
        fee_bps=fee_bps,
        slippage_bps=slippage_bps,
    )


@router.get("/capabilities", response_model=CollectionResponse[ExchangeCapability])
def get_market_capabilities() -> CollectionResponse[ExchangeCapability]:
    capabilities = list_exchange_capabilities()
    return CollectionResponse(items=capabilities, total=len(capabilities))


def _fetch_binance_usdm_tickers() -> list[dict] | None:
    try:
        return fetch_usdm_24h_tickers()
    except Exception:
        try:
            import ccxt

            client = ccxt.binanceusdm({"enableRateLimit": True, "timeout": 3000})
            tickers = client.fapiPublicGetTicker24hr()
            return tickers if isinstance(tickers, list) else None
        except Exception:
            return None


def _fetch_binance_exchange_info_symbols() -> list[dict] | None:
    global _exchange_info_cache, _exchange_info_cached_at, _exchange_info_future

    now = monotonic()
    with _exchange_info_lock:
        if _exchange_info_cache is not None and now - _exchange_info_cached_at < _EXCHANGE_INFO_TTL_SECONDS:
            return _exchange_info_cache
        if _exchange_info_future is None:
            _exchange_info_future = _exchange_info_executor.submit(fetch_usdm_exchange_info_symbols)
        future = _exchange_info_future
    try:
        result = future.result(timeout=_EXCHANGE_INFO_WAIT_SECONDS)
    except FutureTimeoutError:
        return _exchange_info_cache
    except Exception:
        with _exchange_info_lock:
            if _exchange_info_future is future:
                _exchange_info_future = None
        return _exchange_info_cache
    with _exchange_info_lock:
        _exchange_info_cache = result
        _exchange_info_cached_at = monotonic()
        if _exchange_info_future is future:
            _exchange_info_future = None
    return result


def reset_exchange_info_cache() -> None:
    """Reset the short-lived exchangeInfo cache for tests and explicit refreshes."""

    global _exchange_info_cache, _exchange_info_cached_at, _exchange_info_future
    with _exchange_info_lock:
        if _exchange_info_future is not None:
            _exchange_info_future.cancel()
        _exchange_info_future = None
        _exchange_info_cache = None
        _exchange_info_cached_at = 0.0


def _websocket_token_is_valid(token: str) -> bool:
    return bool(token) and compare_digest(token, settings.admin_api_token)


def _latest_ohlcv_payload(*, symbol: str, timeframe: str, limit: int) -> dict:
    with get_session_factory()() as db:
        live_reads = _live_market_reads_enabled()
        service = _live_market_service(db) if live_reads else _market_service(db)
        method = service.get_live_ohlcv_series if live_reads else service.get_ohlcv_series
        return method(symbol=symbol, timeframe=timeframe, limit=limit).model_dump(mode="json")


def _latest_exchange_payload(*, symbol: str, perp_symbol: str, timeframe: str, limit: int) -> dict:
    with get_session_factory()() as db:
        live_reads = _live_market_reads_enabled()
        service = _live_market_service(db) if live_reads else _market_service(db)
        snapshot_method = service.get_live_snapshot if live_reads else service.get_snapshot
        candles_method = service.get_live_ohlcv_series if live_reads else service.get_ohlcv_series
        return {
            "symbol": symbol,
            "perp_symbol": perp_symbol,
            "timeframe": timeframe,
            "snapshot": snapshot_method(symbol=symbol, perp_symbol=perp_symbol, timeframe=timeframe).model_dump(
                mode="json"
            ),
            "ohlcv": candles_method(symbol=symbol, timeframe=timeframe, limit=limit).model_dump(mode="json"),
            "order_book": service.get_order_book(symbol=perp_symbol, limit=20).model_dump(mode="json"),
            "trades": service.get_recent_trades(symbol=perp_symbol, limit=50).model_dump(mode="json"),
            "feed_status": {
                "status": "snapshot",
                "source": "binance_public_ws" if settings.binance_live_ws_enabled else "rest_polling",
                "reason": None if settings.binance_live_ws_enabled else "binance_live_ws_disabled",
            },
        }


async def _stream_exchange_binance_ws(
    *,
    websocket: WebSocket,
    symbol: str,
    perp_symbol: str,
    timeframe: str,
    sequence: int,
) -> int:
    try:
        import websockets
    except ImportError as exc:  # pragma: no cover - runtime dependency guard
        raise RuntimeError("websockets package is required for Binance exchange stream") from exc

    await websocket.send_json(
        {
            "event": "feed_status",
            "sequence": sequence,
            "payload": {"status": "live", "source": "binance_public_ws", "reason": None},
        }
    )
    sequence += 1
    async with websockets.connect(_binance_exchange_stream_url(perp_symbol=perp_symbol, timeframe=timeframe)) as ws:
        async for raw_message in ws:
            payload = json.loads(raw_message.decode("utf-8") if isinstance(raw_message, bytes) else str(raw_message))
            event = _exchange_stream_event_from_binance_payload(
                payload,
                symbol=symbol,
                perp_symbol=perp_symbol,
                timeframe=timeframe,
            )
            if event is None:
                continue
            if event["event"] == "kline" and event["payload"].get("closed"):
                with get_session_factory()() as db:
                    DataRepository(db).store_ohlcv_bars([event["bar"]])
            event = {key: value for key, value in event.items() if key != "bar"}
            await websocket.send_json({**event, "sequence": sequence})
            sequence += 1
    return sequence


async def _stream_exchange_rest_poll(
    *,
    websocket: WebSocket,
    symbol: str,
    perp_symbol: str,
    timeframe: str,
    limit: int,
    sequence: int,
    reason: str,
) -> None:
    await websocket.send_json(
        {
            "event": "feed_status",
            "sequence": sequence,
            "payload": {"status": "rest_polling", "source": "binance_public_rest", "reason": reason},
        }
    )
    sequence += 1
    while True:
        await asyncio.sleep(2.0)
        payload = _latest_exchange_payload(symbol=symbol, perp_symbol=perp_symbol, timeframe=timeframe, limit=limit)
        payload["feed_status"] = {"status": "rest_polling", "source": "binance_public_rest", "reason": reason}
        await websocket.send_json({"event": "exchange_snapshot", "sequence": sequence, "payload": payload})
        sequence += 1


def _binance_exchange_stream_url(*, perp_symbol: str, timeframe: str) -> str:
    raw = stream_symbol(perp_symbol)
    streams = "/".join([f"{raw}@kline_{timeframe}", f"{raw}@depth20@100ms", f"{raw}@trade"])
    return f"{binance_usdm_ws_base().replace('/ws', '/stream')}?streams={streams}"


def _exchange_stream_event_from_binance_payload(
    payload: Mapping[str, Any],
    *,
    symbol: str,
    perp_symbol: str,
    timeframe: str,
) -> dict | None:
    event = payload.get("data") if "data" in payload else payload
    if not isinstance(event, Mapping):
        return None
    if event.get("e") == "kline" or isinstance(event.get("k"), Mapping):
        normalized = normalize_ws_kline_event_with_status(event, symbol=symbol, timeframe=timeframe)
        if normalized is None:
            return None
        bar, closed = normalized
        return {
            "event": "kline",
            "bar": bar,
            "payload": {**bar.model_dump(mode="json"), "closed": closed, "source": "binance_public_ws"},
        }
    if "lastUpdateId" in event or "b" in event or "bids" in event:
        return {
            "event": "order_book",
            "payload": _order_book_from_ws_event(event, symbol=perp_symbol).model_dump(mode="json"),
        }
    if event.get("e") == "trade" or ("p" in event and "q" in event):
        trade = _trade_from_ws_event(event)
        if trade is None:
            return None
        return {"event": "trade", "payload": trade.model_dump(mode="json")}
    return None


def _order_book_from_ws_event(event: Mapping[str, Any], *, symbol: str) -> MarketOrderBookResponse:
    bids = _book_levels_from_ws(event.get("bids") or event.get("b") or [], limit=20)
    asks = _book_levels_from_ws(event.get("asks") or event.get("a") or [], limit=20)
    return MarketOrderBookResponse(
        symbol=symbol,
        data_status="ok" if bids or asks else "empty",
        source="binance_public_ws",
        last_update_id=_int_or_none(event.get("lastUpdateId") or event.get("u")),
        bids=bids,
        asks=asks,
    )


def _book_levels_from_ws(rows: Any, *, limit: int) -> list[OrderBookLevel]:
    levels: list[OrderBookLevel] = []
    running_total = Decimal("0")
    for row in list(rows or [])[:limit]:
        if not isinstance(row, (list, tuple)) or len(row) < 2:
            continue
        price = Decimal(str(row[0]))
        quantity = Decimal(str(row[1]))
        running_total += quantity
        levels.append(OrderBookLevel(price=price, quantity=quantity, total=running_total))
    return levels


def _trade_from_ws_event(event: Mapping[str, Any]) -> MarketTrade | None:
    price = event.get("p")
    quantity = event.get("q")
    if price is None or quantity is None:
        return None
    timestamp = event.get("T") or event.get("E")
    trade_time = datetime.fromtimestamp(int(timestamp) / 1000, tz=UTC) if timestamp is not None else None
    return MarketTrade(
        trade_id=str(event.get("t")) if event.get("t") is not None else None,
        price=Decimal(str(price)),
        quantity=Decimal(str(quantity)),
        side="sell" if bool(event.get("m")) else "buy",
        trade_time=trade_time,
    )


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


@router.get("/news", response_model=dict)
def list_news_items(
    limit: int = Query(default=50, ge=1, le=200),
    refresh: bool = Query(default=False),
    db: Session = Depends(get_db_session),
) -> dict:
    repo = DataRepository(db)
    refresh_summary = None
    refresh_error = None
    if refresh:
        try:
            refresh_summary = NewsIngestionService(
                data_repo=repo,
                agent_repo=AgentTaskRepository(db),
                strategy_repo=StrategyRepository(db),
                review_repo=ReviewRepository(db),
            ).poll_configured_feeds()
        except Exception as exc:  # pragma: no cover - depends on third-party network
            refresh_error = str(exc)
    items = repo.list_news_items(limit=limit)
    return {"items": items, "total": len(items), "refresh": refresh_summary, "refresh_error": refresh_error}


@router.get("/macro-events", response_model=dict)
def list_macro_events(
    limit: int = Query(default=50, ge=1, le=200),
    refresh: bool = Query(default=False),
    db: Session = Depends(get_db_session),
) -> dict:
    repo = DataRepository(db)
    refresh_summary = None
    refresh_error = None
    if refresh:
        try:
            refresh_summary = MacroCalendarService(data_repo=repo).poll_configured_sources()
        except Exception as exc:  # pragma: no cover - depends on third-party network
            refresh_error = str(exc)
    items = repo.list_macro_events(limit=limit)
    return {"items": items, "total": len(items), "refresh": refresh_summary, "refresh_error": refresh_error}
