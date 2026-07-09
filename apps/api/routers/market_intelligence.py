"""Market Intelligence debug/read APIs."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from services.data import DataRepository, MarketIntelligenceService
from services.database import get_db_session
from shared.models import (
    CollectionResponse,
    MarketEvent,
    MarketIntelligenceFeatureSnapshot,
    MarketIntelligenceSignal,
)

router = APIRouter(prefix="/market-intelligence", tags=["market-intelligence"])


def _service(db: Session) -> MarketIntelligenceService:
    return MarketIntelligenceService(data_repo=DataRepository(db))


@router.get("/events", response_model=CollectionResponse[MarketEvent])
def list_market_intelligence_events(
    symbol: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db_session),
) -> CollectionResponse[MarketEvent]:
    items = _service(db).list_events(symbol=symbol, limit=limit)
    return CollectionResponse(items=items, total=len(items))


@router.get("/features", response_model=MarketIntelligenceFeatureSnapshot)
def get_market_intelligence_features(
    symbol: str = Query(default="BTC/USDT"),
    db: Session = Depends(get_db_session),
) -> MarketIntelligenceFeatureSnapshot:
    return _service(db).build_feature_snapshot(symbol=symbol)


@router.get("/signals", response_model=MarketIntelligenceSignal)
def get_market_intelligence_signal(
    symbol: str = Query(default="BTC/USDT"),
    db: Session = Depends(get_db_session),
) -> MarketIntelligenceSignal:
    return _service(db).build_signal(symbol=symbol)


@router.post("/refresh", response_model=dict)
def refresh_market_intelligence(
    symbol: str = Query(default="BTC/USDT"),
    db: Session = Depends(get_db_session),
) -> dict:
    return _service(db).refresh(symbol=symbol)
