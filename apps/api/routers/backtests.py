"""Backtest API backed by the validation repository."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from services.data import DataRepository
from services.database import get_db_session
from services.strategy_library import StrategyRepository, ValidationRepository
from services.validation import CarryBacktestApplicationService
from shared.models import BacktestRun, CarryBacktestRequest, GateDecision, OptimizationRun

router = APIRouter(tags=["validation"])

_OPTIMIZATIONS: dict[str, OptimizationRun] = {}


def _repo(db: Session) -> ValidationRepository:
    return ValidationRepository(db)


def _carry_app(db: Session) -> CarryBacktestApplicationService:
    return CarryBacktestApplicationService(
        strategy_repo=StrategyRepository(db),
        validation_repo=ValidationRepository(db),
        data_repo=DataRepository(db),
    )


@router.get("/backtests", response_model=list[BacktestRun])
def list_backtest_runs(db: Session = Depends(get_db_session)) -> list[BacktestRun]:
    return _repo(db).list_backtest_runs()


@router.post("/backtests", response_model=BacktestRun, status_code=status.HTTP_201_CREATED)
def create_backtest_run(
    body: BacktestRun, db: Session = Depends(get_db_session)
) -> BacktestRun:
    return _repo(db).create_backtest_run(body)


@router.post(
    "/backtests/carry",
    response_model=BacktestRun,
    status_code=status.HTTP_201_CREATED,
)
def submit_carry_backtest(
    body: CarryBacktestRequest, db: Session = Depends(get_db_session)
) -> BacktestRun:
    try:
        return _carry_app(db).submit(body)
    except ValueError as exc:
        detail = str(exc)
        status_code = (
            status.HTTP_404_NOT_FOUND if "strategy not found" in detail else status.HTTP_400_BAD_REQUEST
        )
        raise HTTPException(status_code=status_code, detail=detail) from exc


@router.get("/backtests/{backtest_run_id}", response_model=BacktestRun)
def get_backtest_run(
    backtest_run_id: str, db: Session = Depends(get_db_session)
) -> BacktestRun:
    run = _repo(db).get_backtest_run(backtest_run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="backtest run not found")
    return run


@router.get("/backtests/{backtest_run_id}/eligibility", response_model=GateDecision)
def get_backtest_eligibility(
    backtest_run_id: str, db: Session = Depends(get_db_session)
) -> GateDecision:
    run = _repo(db).get_backtest_run(backtest_run_id)
    if run is None or run.eligibility_result is None:
        raise HTTPException(status_code=404, detail="eligibility result not found")
    return run.eligibility_result


@router.get("/optimizations", response_model=list[OptimizationRun])
def list_optimization_runs() -> list[OptimizationRun]:
    return list(_OPTIMIZATIONS.values())
