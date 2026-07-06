"""Read-only market data APIs for the Paper trading console."""

from __future__ import annotations

import asyncio
from hmac import compare_digest
from typing import Any

from fastapi import APIRouter, Depends, Query, WebSocket, status
from fastapi.websockets import WebSocketDisconnect
from sqlalchemy.orm import Session

from apps.api.config import settings
from services.data import DataRepository, MarketQueryService
from services.data.binance import BinanceCcxtClient, fetch_usdm_24h_tickers
from services.data.capabilities import list_exchange_capabilities
from services.database import get_db_session, get_session_factory
from shared.models import (
    CollectionResponse,
    ExchangeCapability,
    FundingArbitrageSignal,
    MarketOrderBookResponse,
    MarketSnapshot,
    MarketTradesResponse,
    MarketUniverseItem,
    OhlcvSeriesResponse,
)

router = APIRouter(prefix="/market", tags=["market"])


def _market_service(db: Session) -> MarketQueryService:
    return MarketQueryService(DataRepository(db))


def _live_market_service(db: Session) -> MarketQueryService:
    client = BinanceCcxtClient() if settings.binance_live_market_enabled else None
    return MarketQueryService(DataRepository(db), binance_client=client)


@router.get("/snapshot", response_model=MarketSnapshot)
def get_market_snapshot(
    symbol: str = Query(default="BTC/USDT"),
    perp_symbol: str = Query(default="BTC/USDT:USDT"),
    timeframe: str = Query(default="1h"),
    db: Session = Depends(get_db_session),
) -> MarketSnapshot:
    service = _live_market_service(db) if settings.binance_live_market_enabled else _market_service(db)
    method = service.get_live_snapshot if settings.binance_live_market_enabled else service.get_snapshot
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
    service = _live_market_service(db) if settings.binance_live_market_enabled else _market_service(db)
    method = service.get_live_ohlcv_series if settings.binance_live_market_enabled else service.get_ohlcv_series
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
    return _live_market_service(db).get_order_book(symbol=symbol, limit=limit)


@router.get("/trades", response_model=MarketTradesResponse)
def get_recent_trades(
    symbol: str = Query(default="BTC/USDT:USDT"),
    limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db_session),
) -> MarketTradesResponse:
    return _live_market_service(db).get_recent_trades(symbol=symbol, limit=limit)


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
    last_signature: tuple[Any, ...] | None = None
    sequence = 0
    try:
        while True:
            payload = _latest_ohlcv_payload(symbol=symbol, timeframe=timeframe, limit=limit)
            signature = _ohlcv_signature(payload)
            if signature != last_signature or sequence == 0:
                await websocket.send_json(
                    {
                        "event": "ohlcv_snapshot",
                        "source": payload.get("source", "persisted_market_data"),
                        "sequence": sequence,
                        "payload": payload,
                    }
                )
                last_signature = signature
                sequence += 1
            await asyncio.sleep(max(1, settings.market_kline_stream_poll_seconds))
    except WebSocketDisconnect:
        return


@router.get("/universe", response_model=CollectionResponse[MarketUniverseItem])
def get_market_universe(
    limit: int = Query(default=20, ge=1, le=50),
    db: Session = Depends(get_db_session),
) -> CollectionResponse[MarketUniverseItem]:
    tickers = _fetch_binance_usdm_tickers() if settings.binance_live_universe_enabled else None
    items = _market_service(db).get_market_universe(limit=limit, tickers=tickers)
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
    service = _live_market_service(db) if settings.binance_live_market_enabled else _market_service(db)
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
        import ccxt

        client = ccxt.binanceusdm({"enableRateLimit": True, "timeout": 3000})
        tickers = client.fapiPublicGetTicker24hr()
        return tickers if isinstance(tickers, list) else None
    except Exception:
        try:
            return fetch_usdm_24h_tickers()
        except Exception:
            return None


def _websocket_token_is_valid(token: str) -> bool:
    return bool(token) and compare_digest(token, settings.admin_api_token)


def _latest_ohlcv_payload(*, symbol: str, timeframe: str, limit: int) -> dict:
    with get_session_factory()() as db:
        service = _live_market_service(db) if settings.binance_live_market_enabled else _market_service(db)
        method = service.get_live_ohlcv_series if settings.binance_live_market_enabled else service.get_ohlcv_series
        return method(symbol=symbol, timeframe=timeframe, limit=limit).model_dump(mode="json")


def _ohlcv_signature(payload: dict) -> tuple[Any, ...]:
    candles = payload.get("candles") or []
    last = candles[-1] if candles else {}
    return (
        payload.get("data_status"),
        len(candles),
        last.get("time") or last.get("timestamp"),
        last.get("open"),
        last.get("high"),
        last.get("low"),
        last.get("close"),
        last.get("volume"),
    )


@router.get("/news", response_model=dict)
def list_news_items(
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db_session),
) -> dict:
    items = DataRepository(db).list_news_items(limit=limit)
    return {"items": items, "total": len(items)}


@router.get("/macro-events", response_model=dict)
def list_macro_events(
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db_session),
) -> dict:
    items = DataRepository(db).list_macro_events(limit=limit)
    return {"items": items, "total": len(items)}
