"""Paper and live execution APIs for the current research loop slice."""

from __future__ import annotations

import os
import re
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from apps.api.config import settings
from apps.api.http import api_error, collection_response, not_found
from services.data import DataRepository
from services.data.binance import BinanceCcxtClient
from services.data.live_feed_bus import live_feed_bus
from services.data.market import MarketQueryService
from services.data.universe import (
    AUTO_SIMULATION_EXECUTION_SYMBOLS,
    FIXED_TOP20_SYMBOLS,
    exchange_to_platform_symbol,
    execution_scope_hash,
)
from services.database import get_db_session
from services.execution import (
    BinanceSpotTestnetGateway,
    CarryExecutionService,
    ExecutionGatekeeperService,
    LiveExecutionService,
    ManualTradingService,
    PaperRuntimeService,
    PaperSignalGenerator,
    configured_gateways,
)
from services.execution.bootstrap import bootstrap_link_verification_strategy
from services.execution.demo_audit import BinanceDemoAuditService
from services.execution.gateway import BinanceUsdtPerpetualGateway, probe_testnet_account
from services.execution.manual_context import ManualTradingContextService
from services.execution.risk_tiers import scale_asset_risk_tiers
from services.execution.runtime_state import load_external_scheduler_state
from services.execution.scheduler import runtime_scheduler_status
from services.execution.spot_gateway import spot_demo_credentials_configured
from services.execution.testnet_acceptance import TestnetAcceptanceService
from services.strategy_library import (
    AgentTaskRepository,
    ConfigConflictError,
    ConfigSnapshotRepository,
    DecisionEventRepository,
    ExecutionRepository,
    HypothesisRepository,
    NotificationRepository,
    PaperRunRepository,
    ReviewRepository,
    RiskProfileRepository,
    StrategyRepository,
    ValidationRepository,
)
from services.validation.admission import ValidationAdmissionService
from shared.models import (
    AdjustLeverageRequest,
    AgentTask,
    AutoTradingSettings,
    BinanceTestnetAccountStatus,
    CancelOrderRequest,
    CarryExecutionRequest,
    CarryExecutionStatus,
    ClosePositionRequest,
    CollectionResponse,
    ConfigSnapshot,
    ConfigSnapshotCreateRequest,
    DecisionEvent,
    ExchangeAccountSnapshot,
    ExchangeGatewayCapability,
    ExecutionOrderRequest,
    LeverageAdjustmentResult,
    LiveRun,
    LiveRunRequest,
    ManualOrderRequest,
    ManualTradingContext,
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
    RiskProfileUpdate,
    RunStatus,
    StrategyUpdate,
    TaskSubmission,
    TestnetAcceptanceRunRequest,
    TestnetAcceptanceRunResult,
    TestnetAcceptanceRunStatus,
    TradingRuntimeStatus,
)

router = APIRouter(prefix="/execution", tags=["execution"])

_REJECTION_CATEGORIES = ("信号不足", "OOS 证据", "风控限制", "网关失败", "交易所拒绝")


def _redact_gateway_message(value: object) -> str | None:
    if value is None:
        return None
    message = str(value).strip()
    if not message:
        return None
    message = re.sub(r"(?i)(api[_-]?key|secret|signature)=([^\s&]+)", r"\1=[redacted]", message)
    return message[:500]


def _rejection_category(codes: list[str]) -> str:
    normalized = {str(code).lower() for code in codes}
    if any("binance_auto_execute_failed" in code or "gateway" in code for code in normalized):
        return "网关失败"
    if any("validated_edge" in code or "oos" in code for code in normalized):
        return "OOS 证据"
    if any(
        token in code
        for code in normalized
        for token in ("exposure", "leverage", "portfolio", "daily_loss", "drawdown", "risk_")
    ):
        return "风控限制"
    if any(token in code for code in normalized for token in ("signal", "edge", "technical", "ensemble", "meta_label")):
        return "信号不足"
    return "交易所拒绝"


def summarize_order_rejections(orders: list[dict[str, object]]) -> dict[str, object]:
    """Return safe, operator-readable rejection evidence without exposing credentials."""
    counts = dict.fromkeys(_REJECTION_CATEGORIES, 0)
    recent: list[dict[str, object]] = []
    for order in orders:
        raw_codes = order.get("rejection_codes", [])
        codes = [str(code) for code in raw_codes if code] if isinstance(raw_codes, list) else []
        message = _redact_gateway_message(order.get("rejection_reason"))
        if not codes and message is None:
            continue
        category = _rejection_category(codes)
        counts[category] += 1
        context = order.get("entry_context")
        context = context if isinstance(context, dict) else {}
        recent.append(
            {
                "order_execution_id": order.get("order_execution_id"),
                "symbol": order.get("symbol"),
                "created_at": order.get("created_at"),
                "category": category,
                "codes": codes,
                "message": message,
                "gateway_order_id": order.get("gateway_order_id"),
                "request": {
                    key: context.get(key)
                    for key in ("order_type", "requested_notional", "requested_leverage", "runtime_config_version")
                    if context.get(key) is not None
                },
                "protection_order_refs": context.get("protection_order_refs", []),
            }
        )
    return {"counts": counts, "recent": recent[:50]}


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
        agent_repo=AgentTaskRepository(db),
        review_repo=ReviewRepository(db),
        notification_repo=NotificationRepository(db),
        gatekeeper=_gatekeeper(db),
        gateway=configured_gateways()[0],
    )


def _manual_trading_service(db: Session) -> ManualTradingService:
    return ManualTradingService(
        execution_repo=ExecutionRepository(db),
        gatekeeper=_gatekeeper(db),
        gateway=configured_gateways()[0],
    )


def _manual_context_service(db: Session) -> ManualTradingContextService:
    return ManualTradingContextService(
        strategy_repo=StrategyRepository(db),
        validation_repo=ValidationRepository(db),
        paper_repo=PaperRunRepository(db),
    )


def _testnet_acceptance_service() -> TestnetAcceptanceService:
    return TestnetAcceptanceService(gateway=BinanceUsdtPerpetualGateway(use_testnet=True))


def _demo_audit_service(db: Session) -> BinanceDemoAuditService:
    return BinanceDemoAuditService(
        strategy_repo=StrategyRepository(db),
        validation_repo=ValidationRepository(db),
        paper_repo=PaperRunRepository(db),
        execution_repo=ExecutionRepository(db),
    )


def _carry_execution_service() -> CarryExecutionService:
    return CarryExecutionService(
        spot_gateway=BinanceSpotTestnetGateway(),
        perp_gateway=BinanceUsdtPerpetualGateway(use_testnet=True),
    )


def _carry_signal(db: Session, body: CarryExecutionRequest):  # noqa: ANN201
    client = BinanceCcxtClient() if settings.binance_live_market_enabled else None
    return MarketQueryService(DataRepository(db), binance_client=client).get_funding_arbitrage_signal(
        symbol=body.symbol,
        perp_symbol=body.perp_symbol,
        timeframe=body.timeframe,
    )


def _acceptance_status(task: AgentTask) -> TestnetAcceptanceRunStatus:
    result = TestnetAcceptanceRunResult.model_validate(task.output_payload) if task.output_payload else None
    return TestnetAcceptanceRunStatus(
        run_id=task.agent_task_id or "",
        run_status=task.task_status,
        result=result,
        error_summary=task.error_summary,
    )


def _carry_status(task: AgentTask) -> CarryExecutionStatus:
    if task.output_payload:
        return CarryExecutionStatus.model_validate(task.output_payload)
    return CarryExecutionStatus(
        run_id=task.agent_task_id or "",
        run_status=task.task_status,
        carry_state="planned",
        error_summary=task.error_summary,
    )


def _is_complete_execution_acceptance(result: TestnetAcceptanceRunResult) -> bool:
    expected = list(AUTO_SIMULATION_EXECUTION_SYMBOLS)
    return (
        result.run_status == "completed"
        and result.requested_symbols == expected
        and result.completed_symbols == expected
        and result.filled_order_count >= 2 * len(expected)
        and result.final_open_position_count == 0
        and result.final_open_order_count == 0
    )


def _arm_auto_testnet_runs_after_acceptance(db: Session, result: TestnetAcceptanceRunResult) -> None:
    if not _is_complete_execution_acceptance(result):
        return
    repo = PaperRunRepository(db)
    for run in repo.list_paper_runs():
        if run.execution_profile.get("auto_paper_runtime_key") != "signal_observation":
            continue
        repo.update_paper_run(
            run.paper_run_id or "",
            execution_profile={
                **run.execution_profile,
                "execution_mode": "binance_simulation_first",
                "mirror_to_gateway": True,
                "cost_gate_verified": True,
                "testnet_acceptance_verified_at": datetime.now(UTC).isoformat(),
                "acceptance_symbols": list(AUTO_SIMULATION_EXECUTION_SYMBOLS),
                "acceptance_scope_hash": execution_scope_hash(),
            },
        )


def _local_scheduler_process_running() -> bool:
    """Expose the separately launched desktop scheduler without any network probe."""

    try:
        pid_path = Path(__file__).resolve().parents[3] / "logs" / "scheduler.pid"
        scheduler_pid = int(pid_path.read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return False

    if sys.platform == "win32":
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, scheduler_pid)
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True

    try:
        os.kill(scheduler_pid, 0)
    except OSError:
        return False
    return True


@router.get("/paper-runs", response_model=CollectionResponse[PaperRun])
def list_paper_runs(db: Session = Depends(get_db_session)) -> CollectionResponse[PaperRun]:
    return collection_response(_paper_repo(db).list_paper_runs())


@router.get("/trading-status", response_model=TradingRuntimeStatus)
def get_trading_status(db: Session = Depends(get_db_session)) -> TradingRuntimeStatus:
    credentials_configured = bool(settings.binance_api_key and settings.binance_api_secret)
    gateway_available = credentials_configured and BinanceUsdtPerpetualGateway.capability.supports_order_submit
    external_state = load_external_scheduler_state()
    blockers: list[str] = []
    if not settings.binance_use_testnet or settings.live_trading_enabled:
        blockers.append("safety_boundary")
    if not credentials_configured:
        blockers.append("missing_credentials")
    if not gateway_available:
        blockers.append("gateway_unavailable")
    if not settings.binance_auto_execute:
        blockers.append("auto_execute_disabled")
    if not external_state.running:
        blockers.append(external_state.reason or "scheduler_offline")
    active_symbols = list(AUTO_SIMULATION_EXECUTION_SYMBOLS)
    expected_coverage = len(active_symbols)
    market_data_coverage = external_state.execution_coverage_count or external_state.top20_coverage_count
    if market_data_coverage != expected_coverage:
        blockers.append("market_data_coverage_incomplete")
    if not external_state.exchange_info_ready:
        blockers.append("exchange_info_not_ready")
    if not external_state.data_fresh:
        blockers.append("market_data_stale")
    blocking_risk = DataRepository(db).has_blocking_risk_event(scope=None, reference_time=datetime.now(UTC))
    if blocking_risk:
        blockers.append("blocking_risk_event")
    acceptance_task = AgentTaskRepository(db).find_verified_testnet_acceptance(active_symbols)
    acceptance_verified = acceptance_task is not None
    if not acceptance_verified:
        blockers.append("testnet_acceptance_not_verified")
    execution_ready = not blockers
    auto_execution_state = "ready" if execution_ready else "blocked_" + blockers[0]
    notes = ["secrets are never returned by this endpoint"]
    if not settings.binance_use_testnet:
        notes.append("binance_use_testnet is false; manual testnet trading is disabled by policy")
    scheduler_status = runtime_scheduler_status()
    external_scheduler_running = external_state.running and _local_scheduler_process_running()
    heartbeat = scheduler_status.last_results.get("market_data_heartbeat", {})
    heartbeat_symbols = heartbeat.get("checked_symbols", []) if isinstance(heartbeat, dict) else []
    strategy_gateway_orders = [
        order
        for order in _execution_repo(db).list_orders()
        if order.gateway_order_id
        and order.entry_context.get("execution_kind") == "strategy_trade"
        and order.entry_context.get("strategy_lane") == "signal_observation"
    ]
    latest_strategy_gateway_order = strategy_gateway_orders[-1] if strategy_gateway_orders else None
    return TradingRuntimeStatus(
        exchange="binance",
        mode="testnet" if settings.binance_use_testnet and credentials_configured else "paper",
        app_env=settings.app_env,
        binance_use_testnet=settings.binance_use_testnet,
        live_trading_enabled=settings.live_trading_enabled,
        credentials_configured=credentials_configured,
        gateway_available=gateway_available,
        auto_execute_enabled=settings.binance_auto_execute,
        auto_execution_state=auto_execution_state,
        execution_ready=execution_ready,
        execution_blockers=blockers,
        testnet_acceptance_verified=acceptance_verified,
        fixed_top20_count=20,
        simulation_catalog_count=len(FIXED_TOP20_SYMBOLS),
        active_execution_symbols=active_symbols,
        active_execution_count=expected_coverage,
        market_data_coverage_count=market_data_coverage,
        acceptance_symbols=active_symbols if acceptance_verified else [],
        acceptance_scope_hash=execution_scope_hash() if acceptance_verified else None,
        last_strategy_gateway_order_at=(
            latest_strategy_gateway_order.created_at if latest_strategy_gateway_order else None
        ),
        last_strategy_gateway_order_id=(
            latest_strategy_gateway_order.gateway_order_id if latest_strategy_gateway_order else None
        ),
        backend_build_id=settings.app_build_id,
        scheduler_mode="external_local" if external_scheduler_running else scheduler_status.mode,
        scheduler_running=external_scheduler_running or scheduler_status.running,
        last_auto_cycle_at=external_state.last_auto_cycle_at or scheduler_status.last_auto_cycle_at,
        next_cycle_eta_seconds=scheduler_status.next_cycle_eta_seconds,
        scheduler_error=scheduler_status.scheduler_error,
        task_run_counts=external_state.task_run_counts or dict(scheduler_status.run_counts),
        task_failure_counts=external_state.task_failure_counts or dict(scheduler_status.failure_counts),
        task_last_results=external_state.task_last_results or dict(scheduler_status.last_results),
        task_last_success_at=dict(scheduler_status.last_success_at),
        task_last_failure_at=dict(scheduler_status.last_failure_at),
        top20_coverage_count=external_state.top20_coverage_count if external_state.running else len(heartbeat_symbols),
        queue_backlog_status="not_probed",
        live_feed_status=live_feed_bus.status(),
        notes=notes,
    )


@router.post(
    "/testnet-acceptance-runs",
    response_model=TestnetAcceptanceRunStatus,
    status_code=status.HTTP_201_CREATED,
)
def create_testnet_acceptance_run(
    body: TestnetAcceptanceRunRequest,
    db: Session = Depends(get_db_session),
) -> TestnetAcceptanceRunStatus:
    if (
        not settings.binance_use_testnet
        or settings.live_trading_enabled
        or not settings.binance_api_key
        or not settings.binance_api_secret
    ):
        raise api_error(
            status_code=status.HTTP_400_BAD_REQUEST,
            error_code="testnet_acceptance_preflight_failed",
            message=(
                "Testnet acceptance requires testnet mode, live trading disabled, "
                "Binance Demo/Testnet credentials. A Binance proxy is optional."
            ),
        )
    repo = AgentTaskRepository(db)
    if body.idempotency_key:
        existing = next(
            (
                task
                for task in repo.list_tasks()
                if task.task_type == "testnet_acceptance" and task.input_ref == body.idempotency_key
            ),
            None,
        )
        if existing is not None:
            return _acceptance_status(existing)
    task = repo.create_task(
        AgentTask(
            agent_type="execution_agent",
            task_type="testnet_acceptance",
            input_ref=body.idempotency_key,
            input_payload=body.model_dump(mode="json"),
            task_status="running",
            executor_name="binance_testnet_acceptance",
        )
    )
    try:
        result = _testnet_acceptance_service().run(body)
    except Exception as exc:  # noqa: BLE001
        task = (
            repo.update_task(
                task.agent_task_id or "",
                task_status="failed",
                error_summary=str(exc),
            )
            or task
        )
        return _acceptance_status(task)
    _demo_audit_service(db).record_acceptance(
        acceptance_run_id=task.agent_task_id or "",
        result=result,
    )
    _arm_auto_testnet_runs_after_acceptance(db, result)
    task = (
        repo.update_task(
            task.agent_task_id or "",
            task_status=result.run_status,
            output_payload=result.model_dump(mode="json"),
            error_summary=result.error_summary,
            schema_validation_status="valid",
        )
        or task
    )
    return _acceptance_status(task)


@router.get("/testnet-acceptance-runs/{run_id}", response_model=TestnetAcceptanceRunStatus)
def get_testnet_acceptance_run(
    run_id: str,
    db: Session = Depends(get_db_session),
) -> TestnetAcceptanceRunStatus:
    task = AgentTaskRepository(db).get_task(run_id)
    if task is None or task.task_type != "testnet_acceptance":
        raise not_found("testnet_acceptance_run", run_id)
    return _acceptance_status(task)


@router.post("/testnet-acceptance-runs/{run_id}/cancel", response_model=TestnetAcceptanceRunStatus)
def cancel_testnet_acceptance_run(
    run_id: str,
    db: Session = Depends(get_db_session),
) -> TestnetAcceptanceRunStatus:
    repo = AgentTaskRepository(db)
    task = repo.get_task(run_id)
    if task is None or task.task_type != "testnet_acceptance":
        raise not_found("testnet_acceptance_run", run_id)
    if task.task_status not in {"queued", "running"}:
        raise api_error(
            status_code=status.HTTP_409_CONFLICT,
            error_code="testnet_acceptance_not_cancellable",
            message=f"Acceptance run cannot be cancelled from {task.task_status}.",
        )
    updated = repo.update_task(run_id, task_status="cancelled") or task
    return _acceptance_status(updated)


@router.post(
    "/carry-executions",
    response_model=CarryExecutionStatus,
    status_code=status.HTTP_201_CREATED,
)
def create_carry_execution(
    body: CarryExecutionRequest,
    db: Session = Depends(get_db_session),
) -> CarryExecutionStatus:
    if (
        not settings.binance_use_testnet
        or settings.live_trading_enabled
        or not settings.binance_api_key
        or not settings.binance_api_secret
        or not spot_demo_credentials_configured()
    ):
        raise api_error(
            status_code=status.HTTP_400_BAD_REQUEST,
            error_code="carry_execution_preflight_failed",
            message=(
                "Dual-leg Carry requires the existing Binance Demo credentials (or an explicit "
                "Spot override), testnet mode, and live trading disabled. A proxy is optional."
            ),
        )
    repo = AgentTaskRepository(db)
    if body.idempotency_key:
        existing = next(
            (
                task
                for task in repo.list_tasks()
                if task.task_type == "carry_execution" and task.input_ref == body.idempotency_key
            ),
            None,
        )
        if existing is not None:
            return _carry_status(existing)
    task = repo.create_task(
        AgentTask(
            agent_type="execution_agent",
            task_type="carry_execution",
            input_ref=body.idempotency_key,
            input_payload=body.model_dump(mode="json"),
            task_status="running",
            executor_name="binance_dual_leg_carry",
        )
    )
    try:
        signal = _carry_signal(db, body)
        result = _carry_execution_service().run(body, signal=signal)
    except Exception as exc:  # noqa: BLE001
        task = (
            repo.update_task(
                task.agent_task_id or "",
                task_status="failed",
                error_summary=str(exc),
            )
            or task
        )
        return _carry_status(task)
    result = result.model_copy(update={"run_id": task.agent_task_id or result.run_id})
    task = (
        repo.update_task(
            task.agent_task_id or "",
            task_status=result.run_status,
            output_payload=result.model_dump(mode="json"),
            error_summary=result.error_summary,
            schema_validation_status="valid",
        )
        or task
    )
    return _carry_status(task)


@router.get("/carry-executions/{run_id}", response_model=CarryExecutionStatus)
def get_carry_execution(
    run_id: str,
    db: Session = Depends(get_db_session),
) -> CarryExecutionStatus:
    task = AgentTaskRepository(db).get_task(run_id)
    if task is None or task.task_type != "carry_execution":
        raise not_found("carry_execution", run_id)
    return _carry_status(task)


@router.get("/binance-testnet-account", response_model=BinanceTestnetAccountStatus)
def get_binance_testnet_account(db: Session = Depends(get_db_session)) -> BinanceTestnetAccountStatus:
    """Live Testnet account probe — use this when Binance web UI is geo-blocked."""
    account = probe_testnet_account()
    audit_service = _demo_audit_service(db)
    audit_service.record_account_snapshot(account)
    audit_service.record_recent_account_orders(account)
    audit_service.record_exchange_positions(account)
    return account


@router.get("/manual-trading-context", response_model=ManualTradingContext)
def get_manual_trading_context(
    mode: str = "paper",
    db: Session = Depends(get_db_session),
) -> ManualTradingContext:
    try:
        return _manual_context_service(db).get_or_create(mode=mode)
    except ValueError as exc:
        raise api_error(
            status_code=status.HTTP_400_BAD_REQUEST,
            error_code="manual_context_rejected",
            message=str(exc),
        ) from exc


@router.post("/manual-trading-context", response_model=ManualTradingContext, status_code=status.HTTP_201_CREATED)
def create_manual_trading_context(
    mode: str = "paper",
    db: Session = Depends(get_db_session),
) -> ManualTradingContext:
    return get_manual_trading_context(mode=mode, db=db)


@router.post("/manual-orders", response_model=OrderExecution, status_code=status.HTTP_201_CREATED)
def create_manual_order(body: ManualOrderRequest, db: Session = Depends(get_db_session)) -> OrderExecution:
    try:
        order = _manual_trading_service(db).submit_manual_order(body)
    except ValueError as exc:
        raise api_error(
            status_code=status.HTTP_400_BAD_REQUEST,
            error_code="manual_order_rejected",
            message=str(exc),
        ) from exc
    if order.execution_status == "rejected":
        raise api_error(
            status_code=status.HTTP_400_BAD_REQUEST,
            error_code="manual_order_rejected",
            message=order.rejection_reason or "manual order rejected",
            detail={"rejection_codes": order.rejection_codes},
        )
    return order


@router.post("/close-position", response_model=OrderExecution, status_code=status.HTTP_201_CREATED)
def close_manual_position(body: ClosePositionRequest, db: Session = Depends(get_db_session)) -> OrderExecution:
    try:
        order = _manual_trading_service(db).close_position(body)
    except ValueError as exc:
        raise api_error(
            status_code=status.HTTP_400_BAD_REQUEST,
            error_code="close_position_rejected",
            message=str(exc),
        ) from exc
    if order.execution_status == "rejected":
        raise api_error(
            status_code=status.HTTP_400_BAD_REQUEST,
            error_code="close_position_rejected",
            message=order.rejection_reason or "close position rejected",
            detail={"rejection_codes": order.rejection_codes},
        )
    return order


@router.post("/adjust-leverage", response_model=LeverageAdjustmentResult)
def adjust_leverage(body: AdjustLeverageRequest, db: Session = Depends(get_db_session)) -> LeverageAdjustmentResult:
    try:
        return _manual_trading_service(db).adjust_leverage(body)
    except ValueError as exc:
        raise api_error(
            status_code=status.HTTP_400_BAD_REQUEST,
            error_code="adjust_leverage_rejected",
            message=str(exc),
        ) from exc


@router.post("/cancel-order", response_model=OrderExecution)
def cancel_manual_order(body: CancelOrderRequest, db: Session = Depends(get_db_session)) -> OrderExecution:
    try:
        return _manual_trading_service(db).cancel_order(body)
    except ValueError as exc:
        raise api_error(
            status_code=status.HTTP_400_BAD_REQUEST,
            error_code="cancel_order_rejected",
            message=str(exc),
        ) from exc


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


@router.post("/link-verification/bootstrap", response_model=TaskSubmission, status_code=status.HTTP_202_ACCEPTED)
def bootstrap_link_verification(db: Session = Depends(get_db_session)) -> TaskSubmission:
    """Create/refresh the link-verification-only PaperRun on demand. This lane
    never evaluates real signals and never counts toward strategy performance
    (see services/execution/bootstrap.py::bootstrap_link_verification_strategy);
    it exists purely to exercise the order -> stoploss -> takeprofit -> close
    pipeline. Call /paper-runs/{id}/auto-cycle or /step afterward to run it."""
    paper_run_id = bootstrap_link_verification_strategy()
    if paper_run_id is None:
        raise api_error(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            error_code="link_verification_bootstrap_failed",
            message="link verification paper run bootstrap returned no paper_run_id",
        )
    return TaskSubmission(
        task_id=paper_run_id,
        resource_type="paper_run",
        resource_id=paper_run_id,
    )


@router.post("/paper-runs/auto-cycle-all", response_model=dict)
def run_all_paper_runtime_cycles(
    body: PaperRuntimeCycleRequest,
    db: Session = Depends(get_db_session),
) -> dict:
    results = []
    for run in _paper_repo(db).list_paper_runs():
        if run.paper_status != "running" or run.paper_run_id is None:
            continue
        try:
            result = _paper_runtime_service(db).run_cycle(paper_run_id=run.paper_run_id, request=body)
            results.append(result.model_dump(mode="json"))
        except ValueError as exc:
            results.append(
                {
                    "paper_run_id": run.paper_run_id,
                    "paper_status": run.paper_status,
                    "error": str(exc),
                }
            )
    return {"paper_runs": len(results), "results": results}


@router.get("/paper-runs/{paper_run_id}", response_model=PaperRun)
def get_paper_run(paper_run_id: str, db: Session = Depends(get_db_session)) -> PaperRun:
    run = _paper_repo(db).get_paper_run(paper_run_id)
    if run is None:
        raise not_found("paper_run", paper_run_id)
    return run


@router.get(
    "/paper-runs/{paper_run_id}/config-snapshots",
    response_model=CollectionResponse[ConfigSnapshot],
)
def list_paper_config_snapshots(
    paper_run_id: str,
    db: Session = Depends(get_db_session),
) -> CollectionResponse[ConfigSnapshot]:
    if _paper_repo(db).get_paper_run(paper_run_id) is None:
        raise not_found("paper_run", paper_run_id)
    return collection_response(ConfigSnapshotRepository(db).list_snapshots(paper_run_id))


@router.post(
    "/paper-runs/{paper_run_id}/config-snapshots",
    response_model=ConfigSnapshot,
    status_code=status.HTTP_201_CREATED,
)
def create_paper_config_snapshot(
    paper_run_id: str,
    body: ConfigSnapshotCreateRequest,
    db: Session = Depends(get_db_session),
) -> ConfigSnapshot:
    run = _paper_repo(db).get_paper_run(paper_run_id)
    if run is None:
        raise not_found("paper_run", paper_run_id)
    try:
        snapshot = ConfigSnapshot.create(
            paper_run_id=paper_run_id,
            config=body.config,
            created_by=body.created_by,
            effective_cycle_id=body.effective_cycle_id,
            previous_snapshot_id=run.active_config_snapshot_id,
        )
        return ConfigSnapshotRepository(db).create_snapshot(
            snapshot,
            base_config_hash=body.base_config_hash,
        )
    except ConfigConflictError as exc:
        raise api_error(
            status_code=status.HTTP_409_CONFLICT,
            error_code="config_snapshot_conflict",
            message=str(exc),
            detail={"active_config_hash": run.active_config_hash},
        ) from exc
    except ValueError as exc:
        raise api_error(
            status_code=status.HTTP_400_BAD_REQUEST,
            error_code="invalid_config_snapshot",
            message=str(exc),
        ) from exc


@router.get(
    "/paper-runs/{paper_run_id}/decision-events",
    response_model=CollectionResponse[DecisionEvent],
)
def list_paper_decision_events(
    paper_run_id: str,
    cycle_id: str | None = None,
    decision_id: str | None = None,
    db: Session = Depends(get_db_session),
) -> CollectionResponse[DecisionEvent]:
    if _paper_repo(db).get_paper_run(paper_run_id) is None:
        raise not_found("paper_run", paper_run_id)
    events = DecisionEventRepository(db).list_events(
        paper_run_id=paper_run_id,
        cycle_id=cycle_id,
        decision_id=decision_id,
    )
    return collection_response(events)


@router.patch("/paper-runs/{paper_run_id}/status", response_model=PaperRun)
def update_paper_run_status(
    paper_run_id: str,
    body: PaperRunStatusUpdate,
    db: Session = Depends(get_db_session),
) -> PaperRun:
    paper_repo = _paper_repo(db)
    run = paper_repo.get_paper_run(paper_run_id)
    if run is None:
        raise not_found("paper_run", paper_run_id)
    updated = paper_repo.update_paper_run_status(paper_run_id, body.paper_status)
    if updated is None:
        raise not_found("paper_run", paper_run_id)
    try:
        lifecycle_status = RunStatus(body.paper_status)
    except ValueError:
        lifecycle_status = None
    if lifecycle_status is not None:
        StrategyRepository(db).update_lifecycle_status(run.strategy_id, paper_status=lifecycle_status.value)
    return updated


@router.patch("/paper-runs/{paper_run_id}/execution-profile", response_model=PaperRun)
def update_paper_run_execution_profile(
    paper_run_id: str,
    body: dict[str, object],
    db: Session = Depends(get_db_session),
) -> PaperRun:
    run = _paper_repo(db).get_paper_run(paper_run_id)
    if run is None:
        raise not_found("paper_run", paper_run_id)
    updated = _paper_repo(db).update_paper_run(
        paper_run_id,
        execution_profile={**run.execution_profile, **body},
    )
    if updated is None:
        raise not_found("paper_run", paper_run_id)
    strategy_after = StrategyRepository(db).get_strategy(updated.strategy_id)
    snapshot = ConfigSnapshot.create(
        paper_run_id=paper_run_id,
        config={
            "execution_profile": updated.execution_profile,
            "strategy_rules": strategy_after.rules.model_dump(mode="json") if strategy_after else {},
            "risk_profile_id": updated.execution_profile.get("risk_profile_id"),
        },
        created_by="execution-profile-compat",
        effective_cycle_id="NEXT_CYCLE",
        previous_snapshot_id=updated.active_config_snapshot_id,
    )
    ConfigSnapshotRepository(db).create_snapshot(
        snapshot,
        base_config_hash=updated.active_config_hash,
    )
    return _paper_repo(db).get_paper_run(paper_run_id) or updated


@router.patch("/paper-runs/{paper_run_id}/auto-settings", response_model=PaperRun)
def update_paper_run_auto_settings(
    paper_run_id: str,
    body: AutoTradingSettings,
    db: Session = Depends(get_db_session),
) -> PaperRun:
    run = _paper_repo(db).get_paper_run(paper_run_id)
    if run is None:
        raise not_found("paper_run", paper_run_id)

    risk_profile_id = str(run.execution_profile.get("risk_profile_id") or "")
    if risk_profile_id:
        RiskProfileRepository(db).update_profile(
            risk_profile_id,
            RiskProfileUpdate(
                single_trade_risk_limit=body.risk_per_trade,
                max_symbol_exposure=body.max_symbol_exposure,
                max_total_exposure=body.max_total_exposure,
                max_open_positions=body.max_open_positions,
                max_leverage=body.max_leverage,
                daily_loss_limit=body.daily_loss_limit,
                weekly_loss_limit=body.weekly_loss_limit,
                hard_stop_drawdown_limit=body.hard_stop_drawdown_limit,
                market_scope="Binance USDT-M fixed Top20",
                config_source="frontend auto-settings",
            ),
        )

    strategy_repo = StrategyRepository(db)
    strategy = strategy_repo.get_strategy(run.strategy_id)
    if strategy is not None:
        position_rules = {
            **strategy.rules.position_rules,
            "risk_per_trade": body.risk_per_trade,
            "max_leverage": body.max_leverage,
            "max_position_fraction": body.max_symbol_exposure,
        }
        if body.order_notional_usdt is not None:
            position_rules["order_notional_usdt"] = body.order_notional_usdt
        else:
            position_rules.pop("order_notional_usdt", None)
        updated_rules = strategy.rules.model_copy(
            update={
                "entry_rules": {
                    **strategy.rules.entry_rules,
                    "strategy_lanes": body.strategy_lanes,
                    "market_intelligence_enabled": body.market_intelligence_enabled,
                },
                "stoploss_rules": {**strategy.rules.stoploss_rules, **body.stoploss},
                "takeprofit_rules": {**strategy.rules.takeprofit_rules, **body.takeprofit},
                "position_rules": position_rules,
            }
        )
        strategy_repo.update_strategy(run.strategy_id, StrategyUpdate(rules=updated_rules))

    settings_payload = body.model_dump(mode="json")
    # The client always echoes back whatever asset_risk_tiers it last loaded (the
    # AutoTradingSettings default, or a stale copy of a previous save) because the
    # UI has no per-tier editor. Rescale the *existing* tier table (preserving any
    # symbol assignments from the weekly ATR% volatility sweep) against the
    # operator-controlled max_leverage / max_symbol_exposure sliders, so the
    # sliders actually drive the values PaperSignalGenerator reads at order time.
    settings_payload["asset_risk_tiers"] = scale_asset_risk_tiers(
        run.execution_profile.get("asset_risk_tiers"),
        max_leverage=body.max_leverage,
        max_symbol_exposure=body.max_symbol_exposure,
    )
    history = list(run.paper_metrics_summary.get("auto_settings_history", []))[-49:]
    history.append({"updated_at": datetime.now(UTC).isoformat(), "settings": settings_payload})
    updated_profile = {
        **run.execution_profile,
        **settings_payload,
        "mirror_to_gateway": body.execution_mode == "binance_simulation_first",
        "auto_settings_updated_at": datetime.now(UTC).isoformat(),
    }
    updated_metrics = {**run.paper_metrics_summary, "auto_settings_history": history}
    updated = _paper_repo(db).update_paper_run(
        paper_run_id,
        execution_profile=updated_profile,
        paper_metrics_summary=updated_metrics,
    )
    if updated is None:
        raise not_found("paper_run", paper_run_id)
    strategy_after = StrategyRepository(db).get_strategy(updated.strategy_id)
    snapshot = ConfigSnapshot.create(
        paper_run_id=paper_run_id,
        config={
            "execution_profile": updated.execution_profile,
            "strategy_rules": strategy_after.rules.model_dump(mode="json") if strategy_after else {},
            "risk_profile_id": risk_profile_id or None,
        },
        created_by="auto-settings-compat",
        effective_cycle_id="NEXT_CYCLE",
        previous_snapshot_id=updated.active_config_snapshot_id,
    )
    ConfigSnapshotRepository(db).create_snapshot(
        snapshot,
        base_config_hash=updated.active_config_hash,
    )
    return _paper_repo(db).get_paper_run(paper_run_id) or updated


@router.get("/paper-runs/{paper_run_id}/order-sync", response_model=dict)
def get_paper_run_order_sync(paper_run_id: str, db: Session = Depends(get_db_session)) -> dict:
    run = _paper_repo(db).get_paper_run(paper_run_id)
    if run is None:
        raise not_found("paper_run", paper_run_id)
    local_orders = [
        order.model_dump(mode="json")
        for order in _execution_repo(db).list_orders()
        if order.paper_run_id == paper_run_id
    ]
    positions = [
        position.model_dump(mode="json")
        for position in _execution_repo(db).list_latest_positions_for_run(run_type="paper", run_id=paper_run_id)
    ]
    account = probe_testnet_account(order_limit=20, order_symbols=list(FIXED_TOP20_SYMBOLS))
    gateway_by_id = {str(order.order_id): order.model_dump(mode="json") for order in account.recent_orders}
    local_gateway_ids = {str(order["gateway_order_id"]) for order in local_orders if order.get("gateway_order_id")}
    unmatched_local_orders = [
        order
        for order in local_orders
        if order.get("gateway_order_id") and str(order.get("gateway_order_id")) not in gateway_by_id
    ]
    unmatched_gateway_orders = [
        order for gateway_id, order in gateway_by_id.items() if gateway_id not in local_gateway_ids
    ]
    symbol_summary = []
    for symbol in FIXED_TOP20_SYMBOLS:
        symbol_local = [order for order in local_orders if order.get("symbol") == symbol]
        symbol_gateway = [
            order
            for order in gateway_by_id.values()
            if exchange_to_platform_symbol(str(order.get("symbol", ""))) == symbol
        ]
        matched_ids = {
            str(order.get("gateway_order_id"))
            for order in symbol_local
            if order.get("gateway_order_id") and str(order.get("gateway_order_id")) in gateway_by_id
        }
        symbol_summary.append(
            {
                "symbol": symbol,
                "local_order_count": len(symbol_local),
                "gateway_order_count": len(symbol_gateway),
                "matched_order_count": len(matched_ids),
                "unmatched_local_order_count": sum(
                    1 for order in unmatched_local_orders if order.get("symbol") == symbol
                ),
                "unmatched_gateway_order_count": sum(
                    1
                    for order in unmatched_gateway_orders
                    if exchange_to_platform_symbol(str(order.get("symbol", ""))) == symbol
                ),
            }
        )
    return {
        "paper_run_id": paper_run_id,
        "execution_mode": run.execution_profile.get("execution_mode"),
        "local_orders": local_orders,
        "gateway_recent_orders": list(gateway_by_id.values()),
        "matched_local_order_count": len(local_gateway_ids & gateway_by_id.keys()),
        "symbol_summary": symbol_summary,
        "positions": positions,
        "protection_order_refs": [
            {
                "order_execution_id": order.get("order_execution_id"),
                "symbol": order.get("symbol"),
                "refs": order.get("entry_context", {}).get("protection_order_refs", []),
            }
            for order in local_orders
            if order.get("entry_context", {}).get("protection_order_refs")
        ],
        "unmatched_local_orders": unmatched_local_orders,
        "unmatched_gateway_orders": unmatched_gateway_orders,
        "account": account.model_dump(mode="json"),
    }


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
    order_request = PaperSignalGenerator(
        data_repo=DataRepository(db),
        execution_repo=ExecutionRepository(db),
        agent_repo=AgentTaskRepository(db),
        strategy_repo=StrategyRepository(db),
        review_repo=ReviewRepository(db),
        notification_repo=NotificationRepository(db),
    ).generate_order(
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


@router.get("/paper-runs/{paper_run_id}/decision-trace", response_model=dict)
def get_paper_decision_trace(paper_run_id: str, db: Session = Depends(get_db_session)) -> dict:
    run = _paper_repo(db).get_paper_run(paper_run_id)
    if run is None:
        raise not_found("paper_run", paper_run_id)
    metrics = run.paper_metrics_summary
    profile = run.execution_profile or {}
    orders = [
        order.model_dump(mode="json")
        for order in _execution_repo(db).list_orders()
        if order.paper_run_id == paper_run_id
    ]
    return {
        "paper_run_id": paper_run_id,
        "strategy_lane": profile.get("strategy_lane"),
        "auto_paper_runtime_key": profile.get("auto_paper_runtime_key"),
        "candidate_symbols": run.candidate_symbols,
        "selection_basis": run.selection_basis,
        "execution_profile": profile,
        "auto_settings": {
            key: profile.get(key)
            for key in (
                "execution_mode",
                "max_leverage",
                "risk_per_trade",
                "order_notional_usdt",
                "max_open_positions",
                "max_symbols",
                "max_symbol_exposure",
                "max_total_exposure",
                "daily_loss_limit",
                "weekly_loss_limit",
                "hard_stop_drawdown_limit",
                "strategy_lanes",
                "stoploss",
                "takeprofit",
                "llm_veto_enabled",
                "market_intelligence_enabled",
            )
        },
        "last_cycle_at": metrics.get("last_cycle_at"),
        "last_scanned_symbols": metrics.get("last_scanned_symbols", []),
        "last_action_counts": metrics.get("last_action_counts", {}),
        "last_runtime_timeframe": metrics.get("last_runtime_timeframe"),
        "last_cycle_actions": metrics.get("last_cycle_actions", []),
        "last_cycle_decisions": metrics.get("last_cycle_decisions", []),
        "processed_cycle_keys": metrics.get("processed_cycle_keys", []),
        "rejection_summary": summarize_order_rejections(orders),
    }


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


@router.get("/orders/{order_execution_id}/timeline", response_model=dict)
def get_order_timeline(order_execution_id: str, db: Session = Depends(get_db_session)) -> dict:
    order = _execution_repo(db).get_order(order_execution_id)
    if order is None:
        raise not_found("order", order_execution_id)
    return {
        "order_execution_id": order.order_execution_id,
        "intent_id": order.intent_id,
        "cycle_id": order.cycle_id,
        "decision_id": order.decision_id,
        "config_snapshot_id": order.config_snapshot_id,
        "config_hash": order.config_hash,
        "normalized_order": order.normalized_order,
        "execution_status": order.execution_status,
        "gateway_order_id": order.gateway_order_id,
        "reconciliation_status": order.reconciliation_status,
        "timeline": order.lifecycle_history,
    }


@router.post("/recovery-check", response_model=dict)
def run_execution_recovery_check(db: Session = Depends(get_db_session)) -> dict:
    blockers: list[dict[str, object]] = []
    for order in _execution_repo(db).list_orders():
        codes: list[str] = []
        state = order.execution_status.upper()
        if state in {"UNKNOWN", "RECOVERY_REQUIRED"}:
            codes.append("RECOVERY_REQUIRED")
        if state in {"FILLED", "PARTIALLY_FILLED", "PROTECTION_PENDING"} and not order.stoploss_present:
            codes.append("UNPROTECTED_POSITION")
        if order.reconciliation_status in {"failed", "mismatch", "unknown"}:
            codes.append("RECOVERY_REQUIRED")
        if codes:
            blockers.append(
                {
                    "order_execution_id": order.order_execution_id,
                    "symbol": order.symbol,
                    "execution_status": order.execution_status,
                    "block_codes": sorted(set(codes)),
                }
            )
    return {
        "can_open_new_positions": not blockers,
        "checked_order_count": len(_execution_repo(db).list_orders()),
        "blockers": blockers,
    }


@router.post("/orders", response_model=OrderExecution, status_code=status.HTTP_201_CREATED)
def create_order(body: ExecutionOrderRequest, db: Session = Depends(get_db_session)) -> OrderExecution:
    return _gatekeeper(db).submit_order(body)


@router.get("/positions", response_model=CollectionResponse[PositionSnapshot])
def list_positions(db: Session = Depends(get_db_session)) -> CollectionResponse[PositionSnapshot]:
    return collection_response(_execution_repo(db).list_positions())


@router.post("/positions", response_model=PositionSnapshot, status_code=status.HTTP_201_CREATED)
def create_position_snapshot(body: PositionSnapshot, db: Session = Depends(get_db_session)) -> PositionSnapshot:
    return _execution_repo(db).create_position_snapshot(body)
