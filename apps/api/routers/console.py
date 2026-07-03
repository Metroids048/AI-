"""Aggregated console read APIs."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from apps.api.config import settings
from services.data import DataRepository, MarketQueryService
from services.database import get_db_session
from services.strategy_library import ExecutionRepository, PaperRunRepository, ValidationRepository
from shared.models import ConsoleOverview

router = APIRouter(prefix="/console", tags=["console"])


@router.get("/overview", response_model=ConsoleOverview)
def get_console_overview(
    symbol: str = Query(default="BTC/USDT"),
    perp_symbol: str = Query(default="BTC/USDT:USDT"),
    timeframe: str = Query(default="1h"),
    db: Session = Depends(get_db_session),
) -> ConsoleOverview:
    data_repo = DataRepository(db)
    execution_repo = ExecutionRepository(db)
    validation_repo = ValidationRepository(db)
    risk_events = data_repo.list_risk_events(active_only=True)
    high_risk = any(str(event.severity) in {"high", "critical"} for event in risk_events)

    return ConsoleOverview(
        environment=settings.app_env,
        market=MarketQueryService(data_repo).get_snapshot(
            symbol=symbol,
            perp_symbol=perp_symbol,
            timeframe=timeframe,
        ),
        latest_backtests=[item.model_dump(mode="json") for item in validation_repo.list_backtest_runs()[-5:]],
        paper_runs=[item.model_dump(mode="json") for item in PaperRunRepository(db).list_paper_runs()[-5:]],
        orders=[item.model_dump(mode="json") for item in execution_repo.list_orders()[-10:]],
        positions=[item.model_dump(mode="json") for item in execution_repo.list_positions()[-10:]],
        risk_events=[item.model_dump(mode="json") for item in risk_events[:10]],
        global_risk_status="blocked" if high_risk else "normal",
    )
