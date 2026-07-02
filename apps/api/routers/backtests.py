"""Validation-layer API backed by persisted repositories and services."""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from apps.api.http import api_error, collection_response, not_found
from services.data import DataRepository
from services.database import get_db_session
from services.strategy_library import (
    OptimizationRepository,
    StrategyRepository,
    ValidationRepository,
)
from services.validation import CarryBacktestApplicationService
from shared.models import (
    BacktestRun,
    BacktestSubmissionRequest,
    CarryBacktestRequest,
    CollectionResponse,
    GateDecision,
    OptimizationRun,
    OptimizationSubmissionRequest,
    TaskSubmission,
)

router = APIRouter(tags=["validation"])


def _validation_repo(db: Session) -> ValidationRepository:
    return ValidationRepository(db)


def _optimization_repo(db: Session) -> OptimizationRepository:
    return OptimizationRepository(db)


def _carry_app(db: Session) -> CarryBacktestApplicationService:
    return CarryBacktestApplicationService(
        strategy_repo=StrategyRepository(db),
        validation_repo=ValidationRepository(db),
        data_repo=DataRepository(db),
    )


@router.get("/backtests", response_model=CollectionResponse[BacktestRun])
def list_backtest_runs(db: Session = Depends(get_db_session)) -> CollectionResponse[BacktestRun]:
    return collection_response(_validation_repo(db).list_backtest_runs())


@router.post("/backtests", response_model=TaskSubmission, status_code=status.HTTP_202_ACCEPTED)
def create_backtest_run(
    body: BacktestSubmissionRequest, db: Session = Depends(get_db_session)
) -> TaskSubmission:
    created = _validation_repo(db).create_backtest_run(
        BacktestRun(
            strategy_id=body.strategy_id,
            version_id=body.version_id,
            execution_engine=body.execution_engine,
            parameter_set=body.parameter_set,
            market_regime_coverage=body.market_regime_coverage,
            sample_split_plan=body.sample_split_plan,
            cost_model_ref=body.cost_model_ref,
            validation_methodology=body.validation_methodology,
            stress_test_scenarios=body.stress_test_scenarios,
            run_status="queued",
        )
    )
    return TaskSubmission(
        task_id=body.idempotency_key or created.backtest_run_id,
        resource_type="backtest_run",
        resource_id=created.backtest_run_id,
        detail={"queued": True},
    )


@router.post(
    "/backtests/carry",
    response_model=TaskSubmission,
    status_code=status.HTTP_202_ACCEPTED,
)
def submit_carry_backtest(
    body: CarryBacktestRequest, db: Session = Depends(get_db_session)
) -> TaskSubmission:
    try:
        run = _carry_app(db).submit(body)
    except ValueError as exc:
        message = str(exc)
        if "strategy not found" in message:
            raise not_found("strategy", body.strategy_id) from exc
        raise api_error(
            status_code=status.HTTP_400_BAD_REQUEST,
            error_code="carry_backtest_submission_failed",
            message=message,
        ) from exc
    return TaskSubmission(
        task_id=run.backtest_run_id,
        resource_type="backtest_run",
        resource_id=run.backtest_run_id,
        detail={"lane": "carry"},
    )


@router.get("/backtests/{backtest_run_id}", response_model=BacktestRun)
def get_backtest_run(
    backtest_run_id: str, db: Session = Depends(get_db_session)
) -> BacktestRun:
    run = _validation_repo(db).get_backtest_run(backtest_run_id)
    if run is None:
        raise not_found("backtest_run", backtest_run_id)
    return run


@router.get("/backtests/{backtest_run_id}/eligibility", response_model=GateDecision)
def get_backtest_eligibility(
    backtest_run_id: str, db: Session = Depends(get_db_session)
) -> GateDecision:
    run = _validation_repo(db).get_backtest_run(backtest_run_id)
    if run is None:
        raise not_found("backtest_run", backtest_run_id)
    if run.eligibility_result is None:
        raise api_error(
            status_code=status.HTTP_404_NOT_FOUND,
            error_code="gate_decision_not_found",
            message="gate decision not found",
            detail={"backtest_run_id": backtest_run_id},
        )
    return run.eligibility_result


@router.get("/optimizations", response_model=CollectionResponse[OptimizationRun])
def list_optimization_runs(
    db: Session = Depends(get_db_session),
) -> CollectionResponse[OptimizationRun]:
    return collection_response(_optimization_repo(db).list_runs())


@router.post("/optimizations", response_model=TaskSubmission, status_code=status.HTTP_202_ACCEPTED)
def submit_optimization(
    body: OptimizationSubmissionRequest, db: Session = Depends(get_db_session)
) -> TaskSubmission:
    created = _optimization_repo(db).create_run(
        OptimizationRun(
            strategy_id=body.strategy_id,
            version_id=body.version_id,
            search_space_ref=body.search_space_ref,
            optimization_method=body.optimization_method,
            run_status="queued",
        )
    )
    return TaskSubmission(
        task_id=body.idempotency_key or created.optimization_run_id,
        resource_type="optimization_run",
        resource_id=created.optimization_run_id,
    )
