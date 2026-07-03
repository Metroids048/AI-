"""Read-only market data APIs for the Paper trading console."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from services.data import DataRepository, MarketQueryService
from services.data.capabilities import list_exchange_capabilities
from services.database import get_db_session
from shared.models import CollectionResponse, ExchangeCapability, MarketSnapshot, OhlcvSeriesResponse

router = APIRouter(prefix="/market", tags=["market"])


def _market_service(db: Session) -> MarketQueryService:
    return MarketQueryService(DataRepository(db))


@router.get("/snapshot", response_model=MarketSnapshot)
def get_market_snapshot(
    symbol: str = Query(default="BTC/USDT"),
    perp_symbol: str = Query(default="BTC/USDT:USDT"),
    timeframe: str = Query(default="1h"),
    db: Session = Depends(get_db_session),
) -> MarketSnapshot:
    return _market_service(db).get_snapshot(
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
    return _market_service(db).get_ohlcv_series(
        symbol=symbol,
        timeframe=timeframe,
        limit=limit,
    )


@router.get("/capabilities", response_model=CollectionResponse[ExchangeCapability])
def get_market_capabilities() -> CollectionResponse[ExchangeCapability]:
    capabilities = list_exchange_capabilities()
    return CollectionResponse(items=capabilities, total=len(capabilities))
