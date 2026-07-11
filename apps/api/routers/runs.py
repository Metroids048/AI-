"""Paper and live execution APIs for the current research loop slice."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from apps.api.config import settings
from apps.api.http import api_error, collection_response, not_found
from services.data import DataRepository
from services.data.live_feed_bus import live_feed_bus
from services.data.universe import FIXED_TOP20_SYMBOLS, exchange_to_platform_symbol
from services.database import get_db_session
from services.execution import (
    ExecutionGatekeeperService,
    LiveExecutionService,
    ManualTradingService,
    PaperRuntimeService,
    PaperSignalGenerator,
    configured_gateways,
)
from services.execution.gateway import probe_testnet_account
from services.execution.manual_context import ManualTradingContextService
from services.execution.scheduler import runtime_scheduler_status
from services.strategy_library import (
    AgentTaskRepository,
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
    AutoTradingSettings,
    BinanceTestnetAccountStatus,
    CancelOrderRequest,
    ClosePositionRequest,
    CollectionResponse,
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
    StrategyUpdate,
    TaskSubmission,
    TradingRuntimeStatus,
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


@router.get("/paper-runs", response_model=CollectionResponse[PaperRun])
def list_paper_runs(db: Session = Depends(get_db_session)) -> CollectionResponse[PaperRun]:
    return collection_response(_paper_repo(db).list_paper_runs())


@router.get("/trading-status", response_model=TradingRuntimeStatus)
def get_trading_status() -> TradingRuntimeStatus:
    gateway = configured_gateways()[0]
    credentials_configured = bool(settings.binance_api_key and settings.binance_api_secret)
    gateway_available = credentials_configured and gateway.capability.supports_order_submit
    if not settings.binance_use_testnet or settings.live_trading_enabled:
        auto_execution_state = "blocked_safety_boundary"
    elif not credentials_configured:
        auto_execution_state = "blocked_missing_credentials"
    elif not gateway_available:
        auto_execution_state = "blocked_gateway_unavailable"
    elif not settings.binance_auto_execute:
        auto_execution_state = "monitoring_only"
    else:
        auto_execution_state = "armed"
    notes = ["secrets are never returned by this endpoint"]
    if not settings.binance_use_testnet:
        notes.append("binance_use_testnet is false; manual testnet trading is disabled by policy")
    scheduler_status = runtime_scheduler_status()
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
        fixed_top20_count=20,
        backend_build_id=settings.app_build_id,
        scheduler_mode=scheduler_status.mode,
        scheduler_running=scheduler_status.running,
        last_auto_cycle_at=scheduler_status.last_auto_cycle_at,
        next_cycle_eta_seconds=scheduler_status.next_cycle_eta_seconds,
        scheduler_error=scheduler_status.scheduler_error,
        live_feed_status=live_feed_bus.status(),
        notes=notes,
    )


@router.get("/binance-testnet-account", response_model=BinanceTestnetAccountStatus)
def get_binance_testnet_account() -> BinanceTestnetAccountStatus:
    """Live Testnet account probe — use this when Binance web UI is geo-blocked."""
    return probe_testnet_account()


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
    return updated


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
    return updated


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
    local_gateway_ids = {
        str(order["gateway_order_id"])
        for order in local_orders
        if order.get("gateway_order_id")
    }
    unmatched_local_orders = [
        order
        for order in local_orders
        if order.get("gateway_order_id") and str(order.get("gateway_order_id")) not in gateway_by_id
    ]
    unmatched_gateway_orders = [
        order
        for gateway_id, order in gateway_by_id.items()
        if gateway_id not in local_gateway_ids
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


@router.post("/orders", response_model=OrderExecution, status_code=status.HTTP_201_CREATED)
def create_order(body: ExecutionOrderRequest, db: Session = Depends(get_db_session)) -> OrderExecution:
    return _gatekeeper(db).submit_order(body)


@router.get("/positions", response_model=CollectionResponse[PositionSnapshot])
def list_positions(db: Session = Depends(get_db_session)) -> CollectionResponse[PositionSnapshot]:
    return collection_response(_execution_repo(db).list_positions())


@router.post("/positions", response_model=PositionSnapshot, status_code=status.HTTP_201_CREATED)
def create_position_snapshot(body: PositionSnapshot, db: Session = Depends(get_db_session)) -> PositionSnapshot:
    return _execution_repo(db).create_position_snapshot(body)
