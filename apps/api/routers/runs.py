"""Paper and live execution APIs for the current research loop slice."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from apps.api.http import api_error, collection_response, not_found
from services.data import DataRepository
from services.database import get_db_session
from services.execution import (
    ExecutionGatekeeperService,
    LiveExecutionService,
    PaperRuntimeService,
    PaperSignalGenerator,
    configured_gateways,
)
from services.strategy_library import (
    ExecutionRepository,
    HypothesisRepository,
    PaperRunRepository,
    ReviewRepository,
    RiskProfileRepository,
    StrategyRepository,
    ValidationRepository,
)
from services.validation.admission import ValidationAdmissionService
from shared.models import (
    CollectionResponse,
    ExchangeAccountSnapshot,
    ExchangeGatewayCapability,
    ExecutionOrderRequest,
    LiveRun,
    LiveRunRequest,
    OrderExecution,
    PaperRun,
    PaperRunRequest,
    PaperRunStatusUpdate,
    PaperRunStepRequest,
    PaperRuntimeCycleRequest,
    PaperRuntimeCycleResult,
    PaperRuntimeStatus,
    PositionSnapshot,
    ReconciliationRecord,
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
        hypothesis_repo=HypothesisRepository(db),
        risk_profile_repo=RiskProfileRepository(db),
        execution_repo=ExecutionRepository(db),
        paper_repo=PaperRunRepository(db),
        review_repo=ReviewRepository(db),
    )


def _live_service(db: Session) -> LiveExecutionService:
    gateway = configured_gateways()[0]
    return LiveExecutionService(
        data_repo=DataRepository(db),
        validation_repo=ValidationRepository(db),
        risk_profile_repo=RiskProfileRepository(db),
        execution_repo=ExecutionRepository(db),
        paper_repo=PaperRunRepository(db),
        review_repo=ReviewRepository(db),
        gateway=gateway,
    )


def _paper_runtime_service(db: Session) -> PaperRuntimeService:
    return PaperRuntimeService(
        data_repo=DataRepository(db),
        execution_repo=ExecutionRepository(db),
        paper_repo=PaperRunRepository(db),
        strategy_repo=StrategyRepository(db),
        gatekeeper=_gatekeeper(db),
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


@router.patch("/paper-runs/{paper_run_id}/status", response_model=PaperRun)
def update_paper_run_status(
    paper_run_id: str,
    body: PaperRunStatusUpdate,
    db: Session = Depends(get_db_session),
) -> PaperRun:
    updated = _paper_repo(db).update_paper_run_status(paper_run_id, body.paper_status)
    if updated is None:
        raise not_found("paper_run", paper_run_id)
    return updated


@router.post("/paper-runs/{paper_run_id}/step", response_model=OrderExecution, status_code=status.HTTP_201_CREATED)
def step_paper_run(
    paper_run_id: str,
    body: PaperRunStepRequest,
    db: Session = Depends(get_db_session),
) -> OrderExecution:
    paper_run = _paper_repo(db).get_paper_run(paper_run_id)
    if paper_run is None:
        raise not_found("paper_run", paper_run_id)
    strategy = StrategyRepository(db).get_strategy(paper_run.strategy_id)
    if strategy is None:
        raise not_found("strategy", paper_run.strategy_id)
    order_request = PaperSignalGenerator(data_repo=DataRepository(db)).generate_order(
        paper_run=paper_run,
        strategy=strategy,
        request=body,
        positions=_execution_repo(db).list_latest_positions_for_run(run_type="paper", run_id=paper_run_id),
    )
    return _gatekeeper(db).submit_order(order_request)


@router.post("/paper-runs/{paper_run_id}/auto-cycle", response_model=PaperRuntimeCycleResult)
def run_paper_runtime_cycle(
    paper_run_id: str,
    body: PaperRuntimeCycleRequest,
    db: Session = Depends(get_db_session),
) -> PaperRuntimeCycleResult:
    if _paper_repo(db).get_paper_run(paper_run_id) is None:
        raise not_found("paper_run", paper_run_id)
    try:
        return _paper_runtime_service(db).run_cycle(paper_run_id=paper_run_id, request=body)
    except ValueError as exc:
        raise api_error(
            status_code=status.HTTP_400_BAD_REQUEST,
            error_code="paper_runtime_cycle_failed",
            message=str(exc),
        ) from exc


@router.get("/paper-runs/{paper_run_id}/runtime-status", response_model=PaperRuntimeStatus)
def get_paper_runtime_status(paper_run_id: str, db: Session = Depends(get_db_session)) -> PaperRuntimeStatus:
    if _paper_repo(db).get_paper_run(paper_run_id) is None:
        raise not_found("paper_run", paper_run_id)
    return _paper_runtime_service(db).get_runtime_status(paper_run_id=paper_run_id)


@router.get("/live-runs", response_model=CollectionResponse[LiveRun])
def list_live_runs(db: Session = Depends(get_db_session)) -> CollectionResponse[LiveRun]:
    return collection_response(_execution_repo(db).list_live_runs())


@router.post("/live-runs", response_model=LiveRun, status_code=status.HTTP_201_CREATED)
def create_live_run(body: LiveRunRequest, db: Session = Depends(get_db_session)) -> LiveRun:
    strategy = StrategyRepository(db).get_strategy(body.strategy_id)
    if strategy is None:
        raise not_found("strategy", body.strategy_id)
    if not body.validation_backtest_run_id:
        raise api_error(
            status_code=status.HTTP_400_BAD_REQUEST,
            error_code="live_admission_rejected",
            message="live admission requires validation_backtest_run_id",
        )
    backtest = ValidationRepository(db).get_backtest_run(body.validation_backtest_run_id)
    if backtest is None:
        raise api_error(
            status_code=status.HTTP_400_BAD_REQUEST,
            error_code="live_admission_rejected",
            message="validation backtest run not found",
        )
    if backtest.eligibility_result is None or not backtest.eligibility_result.passed:
        raise api_error(
            status_code=status.HTTP_400_BAD_REQUEST,
            error_code="live_admission_rejected",
            message="validation gate rejected live admission",
        )
    hypothesis_id = None
    if backtest.metrics_summary is not None and backtest.metrics_summary.hypothesis_id is not None:
        hypothesis_id = backtest.metrics_summary.hypothesis_id
    elif "hypothesis_id" in backtest.validation_methodology:
        hypothesis_id = backtest.validation_methodology["hypothesis_id"]
    hypothesis = HypothesisRepository(db).get_hypothesis(hypothesis_id) if hypothesis_id else None
    promotion_gate = ValidationAdmissionService().assess_backtest_run(run=backtest, hypothesis=hypothesis)
    if not promotion_gate.passed:
        raise api_error(
            status_code=status.HTTP_400_BAD_REQUEST,
            error_code="live_admission_rejected",
            message=promotion_gate.reason or "promotion evidence incomplete",
            detail={"failed_thresholds": promotion_gate.failed_thresholds},
        )
    created = _execution_repo(db).create_live_run(
        LiveRun(
            live_run_id=str(uuid.uuid4()),
            strategy_id=body.strategy_id,
            version_id=body.version_id,
            exchange=body.exchange,
            capital_tier=body.capital_tier,
            validation_backtest_run_id=body.validation_backtest_run_id,
            risk_profile_ref=body.risk_profile_ref,
            live_status="queued",
        )
    )
    return created


@router.get("/gateway-capabilities", response_model=CollectionResponse[ExchangeGatewayCapability])
def list_gateway_capabilities() -> CollectionResponse[ExchangeGatewayCapability]:
    return collection_response([gateway.capability for gateway in configured_gateways()])


@router.get("/account-snapshots", response_model=CollectionResponse[ExchangeAccountSnapshot])
def list_account_snapshots(
    live_run_id: str | None = None,
    db: Session = Depends(get_db_session),
) -> CollectionResponse[ExchangeAccountSnapshot]:
    return collection_response(_execution_repo(db).list_account_snapshots(live_run_id=live_run_id))


@router.post(
    "/live-runs/{live_run_id}/sync-account",
    response_model=ExchangeAccountSnapshot,
    status_code=status.HTTP_201_CREATED,
)
def sync_live_account(live_run_id: str, db: Session = Depends(get_db_session)) -> ExchangeAccountSnapshot:
    if _execution_repo(db).get_live_run(live_run_id) is None:
        raise not_found("live_run", live_run_id)
    try:
        return _live_service(db).sync_account(live_run_id=live_run_id)
    except ValueError as exc:
        raise api_error(
            status_code=status.HTTP_400_BAD_REQUEST,
            error_code="account_sync_failed",
            message=str(exc),
        ) from exc


@router.post(
    "/live-runs/{live_run_id}/orders",
    response_model=OrderExecution,
    status_code=status.HTTP_201_CREATED,
)
def create_live_order(
    live_run_id: str,
    body: ExecutionOrderRequest,
    db: Session = Depends(get_db_session),
) -> OrderExecution:
    if _execution_repo(db).get_live_run(live_run_id) is None:
        raise not_found("live_run", live_run_id)
    try:
        return _live_service(db).submit_live_order(live_run_id=live_run_id, order_request=body)
    except ValueError as exc:
        raise api_error(
            status_code=status.HTTP_400_BAD_REQUEST,
            error_code="live_order_rejected",
            message=str(exc),
        ) from exc


@router.post(
    "/live-runs/{live_run_id}/orders/{order_execution_id}/cancel",
    response_model=OrderExecution,
)
def cancel_live_order(
    live_run_id: str,
    order_execution_id: str,
    db: Session = Depends(get_db_session),
) -> OrderExecution:
    try:
        return _live_service(db).cancel_live_order(
            live_run_id=live_run_id,
            order_execution_id=order_execution_id,
        )
    except ValueError as exc:
        raise api_error(
            status_code=status.HTTP_400_BAD_REQUEST,
            error_code="live_order_cancel_failed",
            message=str(exc),
        ) from exc


@router.get("/reconciliations", response_model=CollectionResponse[ReconciliationRecord])
def list_reconciliations(
    live_run_id: str | None = None,
    db: Session = Depends(get_db_session),
) -> CollectionResponse[ReconciliationRecord]:
    return collection_response(_execution_repo(db).list_reconciliation_records(live_run_id=live_run_id))


@router.post(
    "/live-runs/{live_run_id}/reconcile",
    response_model=ReconciliationRecord,
    status_code=status.HTTP_201_CREATED,
)
def reconcile_live_run(live_run_id: str, db: Session = Depends(get_db_session)) -> ReconciliationRecord:
    if _execution_repo(db).get_live_run(live_run_id) is None:
        raise not_found("live_run", live_run_id)
    try:
        return _live_service(db).reconcile_live_run(live_run_id=live_run_id)
    except ValueError as exc:
        raise api_error(
            status_code=status.HTTP_400_BAD_REQUEST,
            error_code="live_reconcile_failed",
            message=str(exc),
        ) from exc


@router.get("/orders", response_model=CollectionResponse[OrderExecution])
def list_orders(db: Session = Depends(get_db_session)) -> CollectionResponse[OrderExecution]:
    return collection_response(_execution_repo(db).list_orders())


@router.post("/orders", response_model=OrderExecution, status_code=status.HTTP_201_CREATED)
def create_order(body: ExecutionOrderRequest, db: Session = Depends(get_db_session)) -> OrderExecution:
    return _gatekeeper(db).submit_order(body)


@router.get("/positions", response_model=CollectionResponse[PositionSnapshot])
def list_positions(db: Session = Depends(get_db_session)) -> CollectionResponse[PositionSnapshot]:
    return collection_response(_execution_repo(db).list_positions())


@router.post("/positions", response_model=PositionSnapshot, status_code=status.HTTP_201_CREATED)
def create_position_snapshot(body: PositionSnapshot, db: Session = Depends(get_db_session)) -> PositionSnapshot:
    return _execution_repo(db).create_position_snapshot(body)
