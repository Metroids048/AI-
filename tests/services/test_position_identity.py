from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import sessionmaker

from services.execution.paper_cycle_orchestrator import PaperCycleOrchestrator
from services.execution.paper_exchange_execution import PaperExchangeExecutionService
from services.execution.paper_order_lifecycle import PaperOrderLifecycleService
from services.execution.scheduler_coordination import SchedulerCoordinator
from services.strategy_library import ExecutionRepository
from shared.models import (
    ExecutionOrderRequest,
    OrderExecution,
    PaperRun,
    PaperRuntimeCycleRequest,
    PositionManagementStatus,
    PositionRecord,
    PositionSnapshot,
    ProtectionRecord,
    ProtectionRecordStatus,
    StrategyContract,
    StrategyRules,
    TradeSide,
)
from tests.repositories.test_decision_snapshot_repository import _create_paper_run


class ReconcileGateway:
    api_backend = "paper"

    class _Capability:
        exchange = "binance"
        market_type = "usdt_perpetual"

    capability = _Capability()

    def __init__(self) -> None:
        self.submitted: list = []

    def reconcile(self, *, live_run_id: str) -> dict:
        return {
            "open_positions": [
                {
                    "symbol": "ETH/USDT:USDT",
                    "side": "short",
                    "contracts": 15.144,
                    "entry_price": 1944.0,
                    "mark_price": 1935.0,
                    "unrealized_pnl": 0.0,
                }
            ],
            "open_orders": [],
        }

    def submit_order(self, **kwargs) -> dict:  # noqa: ANN003
        self.submitted.append(kwargs)
        raise AssertionError("unmanaged or invalid protection must never submit a reduce-only order")


class FilledNormalizedGateway(ReconcileGateway):
    def submit_order(self, **kwargs) -> dict:  # noqa: ANN003
        self.submitted.append(kwargs)
        return {
            "gateway_order_id": "normalized-fill-1",
            "gateway_status": "filled",
            "quantity": 0.01,
            "filled_quantity": 0.01,
            "average_fill_price": 60_125.5,
            "fill_timestamp": "2026-07-23T02:00:00+00:00",
            "fill_source": "create_order",
            "protection_order_refs": [],
        }


def test_eth_external_short_does_not_inherit_invalid_historical_stop(db_session) -> None:
    repo = ExecutionRepository(db_session)
    record = repo.create_position_record(
        PositionRecord(
            exchange_account="binance:usdt_perpetual:paper",
            symbol="ETH/USDT",
            position_side=TradeSide.SHORT,
            opened_at=datetime(2026, 7, 23, tzinfo=UTC),
            quantity=15.144,
            order_origin="manual",
            strategy_id=None,
            run_id="paper-run-eth",
            management_status=PositionManagementStatus.UNMANAGED_EXTERNAL_POSITION,
        )
    )
    protection = repo.create_protection_record(
        ProtectionRecord(
            position_record_id=record.position_record_id or "",
            stop_price=1872.22425,
            take_profit_price=1700.0,
            protection_source="legacy_evidence",
        )
    )
    run = PaperRun(
        paper_run_id="paper-run-eth",
        strategy_id="strategy-eth",
        candidate_symbols=["ETH/USDT"],
        paper_status="running",
    )
    gateway = ReconcileGateway()
    service = PaperExchangeExecutionService(execution_repo=repo, gateway=gateway)

    result = service.reconcile_local_positions_with_exchange(
        paper_run=run,
        strategy=StrategyContract.model_construct(strategy_id="strategy-eth"),
        paper_run_id="paper-run-eth",
        active_positions={},
        exit_ladder_metrics={},
        protective_trailing={},
        reconcile_missing_counts={},
        cycle_time=datetime(2026, 7, 23, 1, tzinfo=UTC),
        close_position_fn=lambda **kwargs: (_ for _ in ()).throw(AssertionError("must not close")),
    )

    refreshed = repo.get_latest_protection_record(protection.position_record_id)
    assert refreshed is not None
    assert refreshed.status is ProtectionRecordStatus.INVALID_PROTECTION_GEOMETRY
    assert gateway.submitted == []
    assert any(action.action == "reconcile_unmanaged_external_position" for action in result["actions"])


def test_identity_mismatch_quarantines_local_position_instead_of_reusing_managed_identity(db_session) -> None:
    repo = ExecutionRepository(db_session)
    entry = repo.create_order(
        OrderExecution(
            strategy_id="strategy-eth",
            symbol="ETH/USDT",
            direction=TradeSide.LONG,
            execution_status="filled",
            gateway_order_id="old-fill-1",
            order_origin="live_scheduler",
            stoploss_present=True,
            stoploss_plan={"price": 2000.0},
            takeprofit_plan={"price": 1800.0},
            paper_run_id="paper-run-eth",
            entry_context={"reference_price": 1944.0, "quantity": 15.144},
        )
    )
    record = repo.create_position_record(
        PositionRecord(
            exchange_account="binance:usdt_perpetual:paper",
            symbol="ETH/USDT",
            position_side=TradeSide.LONG,
            entry_order_id=entry.order_execution_id,
            entry_fill_id="old-fill-1",
            opened_at=datetime(2026, 7, 22, tzinfo=UTC),
            quantity=15.144,
            order_origin="live_scheduler",
            strategy_id="strategy-eth",
            run_id="paper-run-eth",
            management_status=PositionManagementStatus.MANAGED_STRATEGY,
        )
    )
    repo.update_order(entry.order_execution_id or "", position_record_id=record.position_record_id)
    protection = repo.create_protection_record(
        ProtectionRecord(
            position_record_id=record.position_record_id or "",
            stop_price=1900.0,
            take_profit_price=2100.0,
            protection_source="strategy_entry",
        )
    )
    position = repo.create_position_snapshot(
        PositionSnapshot(
            run_type="paper",
            run_id="paper-run-eth",
            symbol="ETH/USDT",
            side=TradeSide.LONG,
            quantity=15.144,
            entry_price=1944.0,
            mark_price=1935.0,
            unrealized_pnl=0.0,
            snapshot_time=datetime(2026, 7, 22, tzinfo=UTC),
            position_record_id=record.position_record_id,
        )
    )
    active_positions = {"ETH/USDT": position}
    gateway = ReconcileGateway()
    service = PaperExchangeExecutionService(execution_repo=repo, gateway=gateway)

    result = service.reconcile_local_positions_with_exchange(
        paper_run=PaperRun(
            paper_run_id="paper-run-eth",
            strategy_id="strategy-eth",
            candidate_symbols=["ETH/USDT"],
            paper_status="running",
        ),
        strategy=StrategyContract.model_construct(strategy_id="strategy-eth"),
        paper_run_id="paper-run-eth",
        active_positions=active_positions,
        exit_ladder_metrics={},
        protective_trailing={},
        reconcile_missing_counts={},
        cycle_time=datetime(2026, 7, 23, 1, tzinfo=UTC),
        close_position_fn=lambda **kwargs: (_ for _ in ()).throw(AssertionError("must not close")),
    )

    stale_record = repo.get_position_record(record.position_record_id or "")
    stale_protection = repo.get_latest_protection_record(protection.position_record_id)
    unmanaged = repo.find_unmanaged_position_record(
        exchange_account="binance:usdt_perpetual:paper",
        symbol="ETH/USDT",
        position_side=TradeSide.SHORT,
        run_id="paper-run-eth",
    )
    assert active_positions == {}
    assert stale_record is not None and stale_record.management_status is PositionManagementStatus.CLOSED
    assert stale_protection is not None and stale_protection.status is ProtectionRecordStatus.INACTIVE
    assert unmanaged is not None and unmanaged.position_record_id != record.position_record_id
    assert gateway.submitted == []
    assert any(action.action == "reconcile_identity_mismatch_quarantined" for action in result["actions"])


def test_persisted_managed_identity_survives_runtime_restart(db_session) -> None:
    repo = ExecutionRepository(db_session)
    entry = repo.create_order(
        OrderExecution(
            strategy_id="strategy-btc",
            symbol="BTC/USDT",
            direction=TradeSide.LONG,
            execution_status="filled",
            gateway_order_id="btc-fill-1",
            order_origin="live_scheduler",
            paper_run_id="paper-run-btc",
            entry_context={"reference_price": 60_000.0, "quantity": 0.01},
        )
    )
    record = repo.create_position_record(
        PositionRecord(
            exchange_account="binance:usdt_perpetual:paper",
            symbol="BTC/USDT",
            position_side=TradeSide.LONG,
            entry_order_id=entry.order_execution_id,
            entry_fill_id="btc-fill-1",
            opened_at=datetime(2026, 7, 23, tzinfo=UTC),
            quantity=0.01,
            order_origin="live_scheduler",
            strategy_id="strategy-btc",
            run_id="paper-run-btc",
            management_status=PositionManagementStatus.MANAGED_STRATEGY,
        )
    )
    repo.update_order(entry.order_execution_id or "", position_record_id=record.position_record_id)

    service = PaperExchangeExecutionService(execution_repo=repo, gateway=ReconcileGateway())

    recovered = service._managed_record_for_exchange(
        paper_run=PaperRun(
            paper_run_id="paper-run-btc",
            strategy_id="strategy-btc",
            candidate_symbols=["BTC/USDT"],
            paper_status="running",
        ),
        symbol="BTC/USDT",
        exchange_position={
            "symbol": "BTC/USDT:USDT",
            "side": "long",
            "contracts": 0.01,
            "entry_price": 60_000.0,
            "mark_price": 60_100.0,
            "position_update_time": datetime(2026, 7, 23, tzinfo=UTC).timestamp() * 1000,
        },
    )

    assert recovered is not None
    assert recovered.position_record_id == record.position_record_id


def test_exchange_position_snapshot_preserves_both_hedge_sides() -> None:
    positions = PaperExchangeExecutionService._exchange_positions(
        {
            "open_positions": [
                {"symbol": "BTC/USDT:USDT", "side": "long", "contracts": 0.01},
                {"symbol": "BTC/USDT:USDT", "side": "short", "contracts": 0.02},
            ]
        }
    )

    assert len(positions) == 2
    assert {item["side"] for item in positions.values()} == {"long", "short"}
    assert PaperExchangeExecutionService._exchange_position_symbols(positions) == {"BTC/USDT"}


def test_ambiguous_exchange_hedge_sides_are_fail_closed(db_session) -> None:
    repo = ExecutionRepository(db_session)
    record = repo.create_position_record(
        PositionRecord(
            exchange_account="binance:usdt_perpetual:paper",
            symbol="BTC/USDT",
            position_side=TradeSide.LONG,
            opened_at=datetime(2026, 7, 23, tzinfo=UTC),
            quantity=0.01,
            order_origin="live_scheduler",
            strategy_id="strategy-btc",
            run_id="paper-run-btc",
            management_status=PositionManagementStatus.MANAGED_STRATEGY,
        )
    )
    protection = repo.create_protection_record(
        ProtectionRecord(
            position_record_id=record.position_record_id or "",
            stop_price=59_000,
            take_profit_price=62_000,
            protection_source="strategy_entry",
        )
    )
    position = repo.create_position_snapshot(
        PositionSnapshot(
            run_type="paper",
            run_id="paper-run-btc",
            symbol="BTC/USDT",
            side=TradeSide.LONG,
            quantity=0.01,
            entry_price=60_000,
            mark_price=60_100,
            snapshot_time=datetime(2026, 7, 23, tzinfo=UTC),
            position_record_id=record.position_record_id,
        )
    )

    class HedgeGateway(ReconcileGateway):
        def reconcile(self, *, live_run_id: str) -> dict:
            return {
                "open_positions": [
                    {"symbol": "BTC/USDT:USDT", "side": "long", "contracts": 0.01},
                    {"symbol": "BTC/USDT:USDT", "side": "short", "contracts": 0.02},
                ],
                "open_orders": [],
            }

    active_positions = {"BTC/USDT": position}
    service = PaperExchangeExecutionService(execution_repo=repo, gateway=HedgeGateway())
    result = service.reconcile_local_positions_with_exchange(
        paper_run=PaperRun(
            paper_run_id="paper-run-btc",
            strategy_id="strategy-btc",
            candidate_symbols=["BTC/USDT"],
            paper_status="running",
        ),
        strategy=StrategyContract.model_construct(strategy_id="strategy-btc"),
        paper_run_id="paper-run-btc",
        active_positions=active_positions,
        exit_ladder_metrics={},
        protective_trailing={},
        reconcile_missing_counts={},
        cycle_time=datetime(2026, 7, 23, 1, tzinfo=UTC),
        close_position_fn=lambda **kwargs: (_ for _ in ()).throw(AssertionError("must not close")),
    )

    refreshed_record = repo.get_position_record(record.position_record_id or "")
    refreshed_protection = repo.get_latest_protection_record(protection.position_record_id)
    assert active_positions == {}
    assert refreshed_record is not None
    assert refreshed_record.management_status is PositionManagementStatus.MANAGED_STRATEGY
    assert refreshed_protection is not None
    assert refreshed_protection.status is ProtectionRecordStatus.ACTIVE
    assert any(action.action == "reconcile_ambiguous_hedge_position" for action in result["actions"])


def test_stale_scheduler_token_cannot_reach_gateway_submit(db_session) -> None:
    factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
    first = SchedulerCoordinator(session_factory=factory, instance_id="scheduler-a")
    second = SchedulerCoordinator(session_factory=factory, instance_id="scheduler-b")
    now = datetime.now(UTC)
    assert first.acquire_or_renew_lease(now=now - timedelta(seconds=100), ttl_seconds=30)
    first_token = first.fencing_token()
    assert first_token == 1
    assert second.acquire_or_renew_lease(now=now, ttl_seconds=90)

    run = _create_paper_run(db_session)
    repo = ExecutionRepository(db_session)
    order = repo.create_order(
        OrderExecution(
            strategy_id=run.strategy_id,
            symbol="BTC/USDT",
            direction=TradeSide.LONG,
            execution_status="accepted",
            stoploss_present=True,
            stoploss_plan={"price": 59_000},
            paper_run_id=run.paper_run_id,
        )
    )
    gateway = ReconcileGateway()
    request = ExecutionOrderRequest(
        strategy_id=run.strategy_id,
        symbol="BTC/USDT",
        direction=TradeSide.LONG,
        entry_context={"quantity": 0.01, "reference_price": 60_000},
        stoploss_plan={"price": 59_000},
        paper_run_id=run.paper_run_id,
        cycle_source="runtime_scheduler",
        scheduler_instance_id="scheduler-a",
        fencing_token=first_token,
    )

    result = PaperExchangeExecutionService(execution_repo=repo, gateway=gateway).ensure_binance_execution(
        paper_run=run,
        order=order,
        order_request=request,
        position=None,
    )

    assert result.execution_status == "rejected"
    assert "lease_lost/fenced" in (result.rejection_reason or "")
    assert gateway.submitted == []


def test_gateway_normalized_fill_quantity_becomes_position_identity_quantity(db_session) -> None:
    run = _create_paper_run(db_session)
    repo = ExecutionRepository(db_session)
    order = repo.create_order(
        OrderExecution(
            strategy_id=run.strategy_id,
            symbol="BTC/USDT",
            direction=TradeSide.LONG,
            execution_status="accepted",
            stoploss_present=True,
            stoploss_plan={"price": 59_000},
            takeprofit_plan={"price": 62_000},
            paper_run_id=run.paper_run_id,
            entry_context={
                "requested_notional": 654.0,
                "reference_price": 60_000.0,
                "quantity": 0.0109,
                "exchange_account": "binance:usdt_perpetual:paper",
            },
        )
    )
    request = ExecutionOrderRequest(
        strategy_id=run.strategy_id,
        symbol="BTC/USDT",
        direction=TradeSide.LONG,
        entry_context=order.entry_context,
        stoploss_plan=order.stoploss_plan,
        takeprofit_plan=order.takeprofit_plan,
        paper_run_id=run.paper_run_id,
    )
    filled = PaperExchangeExecutionService(
        execution_repo=repo,
        gateway=FilledNormalizedGateway(),
    ).ensure_binance_execution(
        paper_run=run,
        order=order,
        order_request=request,
        position=None,
    )
    position = PaperOrderLifecycleService(execution_repo=repo).open_position(
        paper_run_id=run.paper_run_id or "",
        order=filled,
        cycle_time=datetime(2026, 7, 23, 2, tzinfo=UTC),
    )

    assert filled.entry_context["quantity"] == 0.01
    assert filled.entry_context["exchange_average_fill_price"] == 60_125.5
    assert filled.entry_context["exchange_fill_confirmed"] is True
    assert position.quantity == 0.01
    assert position.entry_price == 60_125.5
    record = repo.get_position_record(position.position_record_id or "")
    assert record is not None
    assert record.quantity == 0.01


def test_confirmed_exchange_fill_is_not_resized_to_local_min_notional(db_session) -> None:
    repo = ExecutionRepository(db_session)
    order = repo.create_order(
        OrderExecution(
            strategy_id="strategy-btc",
            symbol="BTC/USDT",
            direction=TradeSide.LONG,
            execution_status="filled",
            gateway_name="binance_usdt_perpetual",
            gateway_order_id="small-fill-1",
            paper_run_id="paper-run-btc",
            entry_context={
                "reference_price": 60_000.0,
                "quantity": 0.01,
                "min_notional_usdt": 50.0,
                "exchange_fill_confirmed": True,
                "exchange_filled_quantity": 0.0001,
                "exchange_average_fill_price": 60_000.0,
                "exchange_account": "binance:usdt_perpetual:paper",
            },
            stoploss_plan={"price": 59_000.0},
            takeprofit_plan={"price": 62_000.0},
        )
    )

    position = PaperOrderLifecycleService(execution_repo=repo).open_position(
        paper_run_id="paper-run-btc",
        order=order,
        cycle_time=datetime(2026, 7, 23, 2, 30, tzinfo=UTC),
    )

    assert position.quantity == 0.0001
    record = repo.get_position_record(position.position_record_id or "")
    assert record is not None
    assert record.quantity == 0.0001


def test_automatic_exit_request_carries_scheduler_fence() -> None:
    request = PaperCycleOrchestrator._protection_order_request(
        paper_run=PaperRun(strategy_id="strategy-1", paper_run_id="run-1"),
        strategy=StrategyContract.model_construct(strategy_id="strategy-1"),
        position=PositionSnapshot(
            run_type="paper",
            run_id="run-1",
            symbol="BTC/USDT",
            side=TradeSide.LONG,
            quantity=0.01,
            entry_price=60_000,
            mark_price=60_100,
            snapshot_time=datetime(2026, 7, 23, 3, tzinfo=UTC),
        ),
        runtime_request=PaperRuntimeCycleRequest(
            cycle_source="runtime_scheduler",
            scheduler_instance_id="scheduler-a",
            fencing_token=7,
        ),
    )

    assert request.cycle_source == "runtime_scheduler"
    assert request.scheduler_instance_id == "scheduler-a"
    assert request.fencing_token == 7


def test_full_close_marks_position_and_protection_terminal(db_session) -> None:
    repo = ExecutionRepository(db_session)
    order = repo.create_order(
        OrderExecution(
            strategy_id="strategy-1",
            symbol="BTC/USDT",
            direction=TradeSide.LONG,
            execution_status="filled",
            paper_run_id="run-1",
            entry_context={"reference_price": 100, "quantity": 1},
            stoploss_plan={"price": 95},
            takeprofit_plan={"price": 110},
        )
    )
    lifecycle = PaperOrderLifecycleService(execution_repo=repo)
    position = lifecycle.open_position(
        paper_run_id="run-1",
        order=order,
        cycle_time=datetime(2026, 7, 23, 3, tzinfo=UTC),
    )
    lifecycle.close_position(
        paper_run_id="run-1",
        position=position,
        mark_price=101,
        cycle_time=datetime(2026, 7, 23, 4, tzinfo=UTC),
        strategy=StrategyContract.model_construct(
            strategy_id="strategy-1",
            rules=StrategyRules(),
        ),
    )

    record = repo.get_position_record(position.position_record_id or "")
    protection = repo.get_latest_protection_record(position.position_record_id or "")
    assert record is not None and record.management_status is PositionManagementStatus.CLOSED
    assert protection is not None and protection.status is ProtectionRecordStatus.INACTIVE


def test_binance_filled_status_without_authoritative_fill_details_stays_unprojected(db_session) -> None:
    class MissingFillDetailsGateway(ReconcileGateway):
        class _Capability:
            gateway_name = "binance_usdt_perpetual"
            exchange = "binance"
            market_type = "usdt_perpetual"

        capability = _Capability()

        def submit_order(self, **kwargs) -> dict:  # noqa: ANN003
            self.submitted.append(kwargs)
            return {
                "gateway_order_id": "filled-without-details",
                "gateway_status": "filled",
                "quantity": 0.01,
                "protection_order_refs": [],
            }

    run = _create_paper_run(db_session)
    repo = ExecutionRepository(db_session)
    order = repo.create_order(
        OrderExecution(
            strategy_id=run.strategy_id,
            symbol="BTC/USDT",
            direction=TradeSide.LONG,
            execution_status="accepted",
            stoploss_present=True,
            stoploss_plan={"price": 59_000},
            paper_run_id=run.paper_run_id,
            entry_context={"reference_price": 60_000.0, "quantity": 0.01},
        )
    )

    result = PaperExchangeExecutionService(
        execution_repo=repo,
        gateway=MissingFillDetailsGateway(),
    ).ensure_binance_execution(
        paper_run=run,
        order=order,
        order_request=ExecutionOrderRequest(
            strategy_id=run.strategy_id,
            symbol="BTC/USDT",
            direction=TradeSide.LONG,
            entry_context=order.entry_context,
            stoploss_plan=order.stoploss_plan,
            paper_run_id=run.paper_run_id,
        ),
        position=None,
    )

    assert result.execution_status == "submitted"
    assert result.entry_context["exchange_fill_confirmed"] is False
