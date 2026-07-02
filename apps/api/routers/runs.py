"""Paper and live execution APIs for the current research loop slice."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from apps.api.http import api_error, collection_response, not_found
from services.data import DataRepository
from services.database import get_db_session
from services.execution import ExecutionGatekeeperService
from services.strategy_library import (
    ExecutionRepository,
    PaperRunRepository,
    RiskProfileRepository,
    ValidationRepository,
)
from shared.models import (
    CollectionResponse,
    ExecutionOrderRequest,
    LiveRun,
    LiveRunRequest,
    OrderExecution,
    PaperRun,
    PaperRunRequest,
    PositionSnapshot,
    TaskSubmission,
)

router = APIRouter(prefix="/execution", tags=["execution"])


def _execution_repo(db: Session) -> ExecutionRepository:
    return ExecutionRepository(db)


def _paper_repo(db: Session) -> PaperRunRepository:
    return PaperRunRepository(db)


def _gatekeeper(db: Session) -> ExecutionGatekeeperService:
    return ExecutionGatekeeperService(
        data_repo=DataRepository(db),
        validation_repo=ValidationRepository(db),
        risk_profile_repo=RiskProfileRepository(db),
        execution_repo=ExecutionRepository(db),
        paper_repo=PaperRunRepository(db),
    )


@router.get("/paper-runs", response_model=CollectionResponse[PaperRun])
def list_paper_runs(db: Session = Depends(get_db_session)) -> CollectionResponse[PaperRun]:
    return collection_response(_paper_repo(db).list_paper_runs())


@router.post("/paper-runs", response_model=TaskSubmission, status_code=status.HTTP_202_ACCEPTED)
def create_paper_run(body: PaperRunRequest, db: Session = Depends(get_db_session)) -> TaskSubmission:
    try:
        created = _gatekeeper(db).prepare_paper_run(body)
    except ValueError as exc:
        raise api_error(
            status_code=status.HTTP_400_BAD_REQUEST,
            error_code="paper_admission_rejected",
            message=str(exc),
        ) from exc
    return TaskSubmission(
        task_id=body.idempotency_key or created.paper_run_id,
        resource_type="paper_run",
        resource_id=created.paper_run_id,
    )


@router.get("/paper-runs/{paper_run_id}", response_model=PaperRun)
def get_paper_run(paper_run_id: str, db: Session = Depends(get_db_session)) -> PaperRun:
    run = _paper_repo(db).get_paper_run(paper_run_id)
    if run is None:
        raise not_found("paper_run", paper_run_id)
    return run


@router.get("/live-runs", response_model=CollectionResponse[LiveRun])
def list_live_runs(db: Session = Depends(get_db_session)) -> CollectionResponse[LiveRun]:
    return collection_response(_execution_repo(db).list_live_runs())


@router.post("/live-runs", response_model=LiveRun, status_code=status.HTTP_201_CREATED)
def create_live_run(body: LiveRunRequest, db: Session = Depends(get_db_session)) -> LiveRun:
    created = _execution_repo(db).create_live_run(
        LiveRun(
            live_run_id=str(uuid.uuid4()),
            strategy_id=body.strategy_id,
            version_id=body.version_id,
            exchange=body.exchange,
            capital_tier=body.capital_tier,
            risk_profile_ref=body.risk_profile_ref,
            live_status="queued",
        )
    )
    return created


@router.get("/orders", response_model=CollectionResponse[OrderExecution])
def list_orders(db: Session = Depends(get_db_session)) -> CollectionResponse[OrderExecution]:
    return collection_response(_execution_repo(db).list_orders())


@router.post("/orders", response_model=OrderExecution, status_code=status.HTTP_201_CREATED)
def create_order(
    body: ExecutionOrderRequest, db: Session = Depends(get_db_session)
) -> OrderExecution:
    return _gatekeeper(db).submit_order(body)


@router.get("/positions", response_model=CollectionResponse[PositionSnapshot])
def list_positions(db: Session = Depends(get_db_session)) -> CollectionResponse[PositionSnapshot]:
    return collection_response(_execution_repo(db).list_positions())


@router.post("/positions", response_model=PositionSnapshot, status_code=status.HTTP_201_CREATED)
def create_position_snapshot(
    body: PositionSnapshot, db: Session = Depends(get_db_session)
) -> PositionSnapshot:
    return _execution_repo(db).create_position_snapshot(body)
