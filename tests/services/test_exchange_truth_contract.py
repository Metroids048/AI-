from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from services.execution.execution_truth import (
    ExchangeFillReceipt,
    ExchangeOrderRecord,
    ExchangeOrderState,
    ExecutionMode,
    ReconciliationStatus,
    close_quantity,
    resolve_execution_mode,
)
from services.execution.paper_exchange_execution import PaperExchangeExecutionService
from services.execution.paper_order_lifecycle import PaperOrderLifecycleService
from services.strategy_library import ExecutionRepository
from shared.models import (
    ExecutionOrderRequest,
    OrderExecution,
    PaperRun,
    PositionManagementStatus,
    PositionSnapshot,
    TradeSide,
)


def _testnet_order(repo: ExecutionRepository, *, confirmed: bool) -> OrderExecution:
    return repo.create_order(
        OrderExecution(
            strategy_id="strategy-btc",
            symbol="BTC/USDT",
            direction=TradeSide.LONG,
            execution_status="FILLED" if confirmed else "INTENT_CREATED",
            gateway_name="binance_usdt_perpetual",
            gateway_order_id="exchange-order-1" if confirmed else None,
            paper_run_id="paper-run-btc",
            entry_context={
                "execution_mode": ExecutionMode.BINANCE_TESTNET.value,
                "reference_price": 60_000.0,
                "quantity": 0.01,
                "exchange_fill_confirmed": confirmed,
                "exchange_filled_quantity": 0.01 if confirmed else None,
                "exchange_average_fill_price": 60_100.0 if confirmed else None,
                "entry_fill_receipt_id": "receipt-1" if confirmed else None,
                "position_group_id": "position-group-1" if confirmed else None,
                "exchange_account": "binance:usdt_perpetual:testnet",
                "protection_order_refs": (
                    [
                        {"kind": "stoploss", "gateway_order_id": "stop-1"},
                        {"kind": "takeprofit", "gateway_order_id": "take-1"},
                    ]
                    if confirmed
                    else []
                ),
            },
            stoploss_plan={"price": 59_000.0},
            takeprofit_plan={"price": 62_000.0},
        )
    )


def test_execution_mode_runtime_rejects_legacy_ambiguous_values() -> None:
    assert resolve_execution_mode("local_paper") is ExecutionMode.LOCAL_PAPER
    assert resolve_execution_mode("binance_testnet") is ExecutionMode.BINANCE_TESTNET
    with pytest.raises(ValueError, match="legacy execution mode"):
        resolve_execution_mode("paper_only")
    with pytest.raises(ValueError, match="legacy execution mode"):
        resolve_execution_mode("binance_simulation_first")


def test_fill_receipt_requires_exchange_trade_identity() -> None:
    with pytest.raises(ValueError):
        ExchangeFillReceipt(
            receipt_id="receipt-1",
            exchange_account="binance:usdt_perpetual:testnet",
            exchange_order_id="exchange-order-1",
            client_order_id="client-order-1",
            trade_ids=[],
            symbol="BTC/USDT",
            side="buy",
            reduce_only=False,
            filled_quantity=Decimal("0.01"),
            average_fill_price=Decimal("60100"),
            commissions=[],
            event_time=datetime(2026, 7, 27, tzinfo=UTC),
        )


def test_testnet_position_projection_requires_authoritative_receipt(db_session) -> None:
    repo = ExecutionRepository(db_session)
    order = _testnet_order(repo, confirmed=False)

    with pytest.raises(ValueError, match="authoritative exchange fill receipt"):
        PaperOrderLifecycleService(execution_repo=repo).open_position(
            paper_run_id="paper-run-btc",
            order=order,
            cycle_time=datetime(2026, 7, 27, tzinfo=UTC),
            execution_mode=ExecutionMode.BINANCE_TESTNET.value,
        )

    assert repo.list_positions_for_run(run_type="paper", run_id="paper-run-btc") == []


def test_confirmed_testnet_position_persists_receipt_and_position_group(db_session) -> None:
    repo = ExecutionRepository(db_session)
    order = _testnet_order(repo, confirmed=True)
    exchange_order = repo.create_exchange_order(
        ExchangeOrderRecord(
            local_order_execution_id=order.order_execution_id or "",
            exchange_account="binance:usdt_perpetual:testnet",
            execution_mode=ExecutionMode.BINANCE_TESTNET,
            client_order_id="client-order-1",
            exchange_order_id="exchange-order-1",
            symbol="BTC/USDT",
            side="buy",
            state=ExchangeOrderState.FILLED,
            requested_quantity=Decimal("0.01"),
        )
    )
    persisted_receipt = repo.create_exchange_fill_receipt(
        exchange_order_record_id=exchange_order.exchange_order_record_id or "",
        receipt=ExchangeFillReceipt(
            receipt_id="receipt-1",
            exchange_account="binance:usdt_perpetual:testnet",
            exchange_order_id="exchange-order-1",
            client_order_id="client-order-1",
            trade_ids=["trade-1"],
            symbol="BTC/USDT",
            side="buy",
            reduce_only=False,
            filled_quantity=Decimal("0.01"),
            average_fill_price=Decimal("60100"),
            commissions=[],
            event_time=datetime(2026, 7, 27, tzinfo=UTC),
        ),
    )
    replayed_receipt = repo.create_exchange_fill_receipt(
        exchange_order_record_id=exchange_order.exchange_order_record_id or "",
        receipt=persisted_receipt.model_copy(update={"receipt_id": "receipt-replayed"}),
    )
    assert replayed_receipt.receipt_id == "receipt-1"
    assert len(repo.list_exchange_fill_receipts(symbol="BTC/USDT")) == 1

    snapshot = PaperOrderLifecycleService(execution_repo=repo).open_position(
        paper_run_id="paper-run-btc",
        order=order,
        cycle_time=datetime(2026, 7, 27, tzinfo=UTC),
        execution_mode=ExecutionMode.BINANCE_TESTNET.value,
    )

    record = repo.get_position_record(snapshot.position_record_id or "")
    assert record is not None
    assert record.management_status is PositionManagementStatus.MANAGED_STRATEGY
    assert record.entry_fill_receipt_id == "receipt-1"
    assert record.position_group_id == "position-group-1"
    assert record.execution_mode is ExecutionMode.BINANCE_TESTNET
    with pytest.raises(ValueError, match="open managed position already exists"):
        repo.create_position_record(record.model_copy(update={"position_record_id": None}))


def test_gateway_absence_is_exchange_unavailable_not_local_acceptance(db_session) -> None:
    repo = ExecutionRepository(db_session)
    order = _testnet_order(repo, confirmed=False)
    request = ExecutionOrderRequest(
        strategy_id=order.strategy_id,
        symbol=order.symbol,
        direction=order.direction,
        entry_context=order.entry_context,
        stoploss_plan=order.stoploss_plan,
        takeprofit_plan=order.takeprofit_plan,
        paper_run_id=order.paper_run_id,
    )

    result = PaperExchangeExecutionService(execution_repo=repo, gateway=None).ensure_binance_execution(
        paper_run=PaperRun(
            paper_run_id="paper-run-btc",
            strategy_id=order.strategy_id,
            candidate_symbols=["BTC/USDT", "ETH/USDT"],
        ),
        order=order,
        order_request=request,
        position=None,
    )

    assert result.execution_status == "EXCHANGE_UNKNOWN"
    assert result.gateway_status == "EXCHANGE_UNAVAILABLE"
    assert result.entry_context["exchange_fill_confirmed"] is False


def test_reconciliation_failure_blocks_all_new_entries(db_session) -> None:
    class TimeoutGateway:
        def reconcile(self, *, live_run_id: str) -> dict:
            raise TimeoutError("exchange timeout")

    result = PaperExchangeExecutionService(
        execution_repo=ExecutionRepository(db_session),
        gateway=TimeoutGateway(),
    ).reconcile_local_positions_with_exchange(
        paper_run=PaperRun(
            paper_run_id="paper-run-btc",
            strategy_id="strategy-btc",
            candidate_symbols=["BTC/USDT", "ETH/USDT"],
        ),
        strategy=None,
        paper_run_id="paper-run-btc",
        active_positions={},
        exit_ladder_metrics={},
        protective_trailing={},
        reconcile_missing_counts={},
        cycle_time=datetime(2026, 7, 27, tzinfo=UTC),
        close_position_fn=lambda **kwargs: None,
    )

    assert result["status"] == ReconciliationStatus.UNAVAILABLE.value
    assert set(result["entry_blocked_symbols"]) == {"BTC/USDT", "ETH/USDT"}
    assert "exchange timeout" in result["error"]


def test_reconciliation_kill_switch_survives_restart_until_unknown_orders_resolve(db_session) -> None:
    class HealthyGateway:
        def reconcile(self, *, live_run_id: str) -> dict:
            del live_run_id
            return {"open_positions": [], "open_orders": [], "reconciliation_status": "healthy"}

    repo = ExecutionRepository(db_session)
    local_order = _testnet_order(repo, confirmed=False)
    unknown = repo.create_exchange_order(
        ExchangeOrderRecord(
            local_order_execution_id=local_order.order_execution_id or "",
            exchange_account="binance:usdt_perpetual:testnet",
            execution_mode=ExecutionMode.BINANCE_TESTNET,
            client_order_id="unknown-client-order",
            symbol="BTC/USDT",
            side="buy",
            state=ExchangeOrderState.EXCHANGE_UNKNOWN,
            requested_quantity=Decimal("0.01"),
        )
    )
    service = PaperExchangeExecutionService(execution_repo=repo, gateway=HealthyGateway())
    paper_run = PaperRun(
        paper_run_id="paper-run-btc",
        strategy_id="strategy-btc",
        candidate_symbols=["BTC/USDT", "ETH/USDT"],
        paper_metrics_summary={
            "reconciliation_consecutive_failures": 3,
            "entry_kill_switch_active": True,
        },
    )

    unresolved = service.reconcile_local_positions_with_exchange(
        paper_run=paper_run,
        strategy=None,
        paper_run_id="paper-run-btc",
        active_positions={},
        exit_ladder_metrics={},
        protective_trailing={},
        reconcile_missing_counts={},
        cycle_time=datetime(2026, 7, 27, tzinfo=UTC),
        close_position_fn=lambda **kwargs: None,
    )

    assert unresolved["status"] == ReconciliationStatus.DEGRADED.value
    assert unresolved["entry_kill_switch_active"] is True
    assert set(unresolved["entry_blocked_symbols"]) == {"BTC/USDT", "ETH/USDT"}

    repo.update_exchange_order(
        unknown.exchange_order_record_id or "",
        state=ExchangeOrderState.EXCHANGE_REJECTED,
    )
    resolved = service.reconcile_local_positions_with_exchange(
        paper_run=paper_run,
        strategy=None,
        paper_run_id="paper-run-btc",
        active_positions={},
        exit_ladder_metrics={},
        protective_trailing={},
        reconcile_missing_counts={},
        cycle_time=datetime(2026, 7, 27, 0, 1, tzinfo=UTC),
        close_position_fn=lambda **kwargs: None,
    )

    assert resolved["status"] == ReconciliationStatus.HEALTHY.value
    assert resolved["consecutive_failures"] == 0
    assert resolved["entry_kill_switch_active"] is False


def test_close_quantity_never_rounds_up_to_min_notional() -> None:
    result = close_quantity(
        requested_quantity=Decimal("0.00004"),
        authoritative_quantity=Decimal("0.00002"),
        step_size=Decimal("0.00001"),
        reference_price=Decimal("60000"),
        min_notional=Decimal("5"),
    )

    assert result.quantity == Decimal("0.00002")
    assert result.dust_remains is True


def test_reduce_risk_fill_with_authoritative_residual_is_dust_not_closed(db_session) -> None:
    class ResidualGateway:
        capability = type("Cap", (), {"gateway_name": "binance_usdt_perpetual"})()

        def __init__(self) -> None:
            self.reconcile_calls = 0

        def reconcile(self, *, live_run_id: str) -> dict:
            del live_run_id
            self.reconcile_calls += 1
            contracts = 0.01 if self.reconcile_calls == 1 else 0.00001
            return {
                "open_positions": [
                    {
                        "symbol": "BTC/USDT",
                        "contracts": contracts,
                        "side": "long",
                        "mark_price": 60_000.0,
                    }
                ],
                "open_orders": [],
            }

        def submit_order(self, *, live_run_id: str, order_request: ExecutionOrderRequest) -> dict:
            del live_run_id, order_request
            return {
                "gateway_status": "filled",
                "gateway_order_id": "exchange-close-1",
                "client_order_id": "aq-close-1",
                "filled_quantity": 0.00999,
                "average_fill_price": 60_000.0,
                "trade_ids": ["trade-close-1"],
                "commissions": [],
                "fill_timestamp": datetime(2026, 7, 27, tzinfo=UTC),
                "fill_source": "account_trades",
            }

    repo = ExecutionRepository(db_session)
    order = repo.create_order(
        OrderExecution(
            strategy_id="strategy-btc",
            symbol="BTC/USDT",
            direction=TradeSide.LONG,
            execution_status="accepted",
            paper_run_id="paper-run-btc",
            close_only_mode=True,
            entry_context={
                "execution_mode": ExecutionMode.BINANCE_TESTNET.value,
                "close_only_mode": True,
                "quantity": 0.01,
                "reference_price": 60_000.0,
                "step_size": "0.00001",
            },
        )
    )
    request = ExecutionOrderRequest(
        strategy_id=order.strategy_id,
        symbol=order.symbol,
        direction=order.direction,
        entry_context=order.entry_context,
        paper_run_id=order.paper_run_id,
    )
    position = PositionSnapshot(
        run_type="paper",
        run_id="paper-run-btc",
        symbol="BTC/USDT",
        side=TradeSide.LONG,
        quantity=0.01,
        entry_price=59_000.0,
        mark_price=60_000.0,
        snapshot_time=datetime(2026, 7, 27, tzinfo=UTC),
    )

    result = PaperExchangeExecutionService(
        execution_repo=repo,
        gateway=ResidualGateway(),
    ).ensure_binance_execution(
        paper_run=PaperRun(
            paper_run_id="paper-run-btc",
            strategy_id="strategy-btc",
            candidate_symbols=["BTC/USDT"],
            execution_profile={"execution_mode": ExecutionMode.BINANCE_TESTNET.value},
        ),
        order=order,
        order_request=request,
        position=position,
    )

    assert result.execution_status == ExchangeOrderState.DUST_REMAINS.value
    assert result.entry_context["post_close_authoritative_quantity"] == pytest.approx(0.00001)
    assert result.entry_context["dust_remains"] is True
    exchange_order = repo.list_exchange_orders(limit=1)[0]
    assert exchange_order.state is ExchangeOrderState.DUST_REMAINS
