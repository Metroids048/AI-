from datetime import UTC, datetime, timedelta
from decimal import Decimal

from services.data import DataRepository
from services.execution.gatekeeper import ExecutionGatekeeperService
from services.execution.paper_runtime import PaperRuntimeService
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
from shared.models import (
    BacktestRun,
    ExecutionOrderRequest,
    ExecutionRiskState,
    GateDecision,
    OrderExecution,
    PaperRun,
    PaperRuntimeCycleRequest,
    PositionSnapshot,
    StrategyCreate,
    TradeSide,
)


class FailingGateway:
    def __init__(self) -> None:
        self.submitted: list[ExecutionOrderRequest] = []

    def submit_order(self, *, live_run_id: str, order_request: ExecutionOrderRequest) -> dict:
        self.submitted.append(order_request)
        raise ValueError("testnet balance too low")


def test_runtime_stoploss_uses_intrabar_low_and_trigger_price(db_session) -> None:
    runtime, paper_run = _runtime_with_position(
        db_session,
        side=TradeSide.LONG,
        stop_price=95.0,
        take_price=120.0,
    )
    _store_bar(db_session, low=94, high=110, close=100)

    result = runtime.run_cycle(
        paper_run_id=paper_run.paper_run_id or "",
        request=PaperRuntimeCycleRequest(symbols=["BTC/USDT"], timeframe="1h", enable_decision_veto=False),
    )

    assert result.closed_positions == 1
    assert result.open_position_symbols == []
    assert result.actions[0].action == "stoploss_close_long"
    assert result.actions[0].reference_price == 95.0
    latest_position = ExecutionRepository(db_session).list_latest_positions_for_run(
        run_type="paper",
        run_id=paper_run.paper_run_id or "",
        include_closed=True,
    )[0]
    assert latest_position.quantity == 0
    assert latest_position.mark_price == 95.0
    failures = ReviewRepository(db_session).list_failures(failure_type="stoploss_triggered")
    assert len(failures) == 1
    assert failures[0].origin_run_id == paper_run.paper_run_id


def test_runtime_stoploss_wins_when_stoploss_and_takeprofit_hit_same_bar(db_session) -> None:
    runtime, paper_run = _runtime_with_position(
        db_session,
        side=TradeSide.LONG,
        stop_price=95.0,
        take_price=105.0,
    )
    _store_bar(db_session, low=94, high=106, close=104)

    result = runtime.run_cycle(
        paper_run_id=paper_run.paper_run_id or "",
        request=PaperRuntimeCycleRequest(symbols=["BTC/USDT"], timeframe="1h", enable_decision_veto=False),
    )

    assert result.closed_positions == 1
    assert result.actions[0].action == "stoploss_close_long"
    assert result.actions[0].reference_price == 95.0


def test_runtime_trailing_stop_ratchets_to_entry_after_configured_r_multiple(db_session) -> None:
    runtime, paper_run = _runtime_with_position(
        db_session,
        side=TradeSide.LONG,
        stop_price=95.0,
        take_price=130.0,
        takeprofit_rules={"risk_reward": 3.0, "trail_after_r": 1.0},
    )
    _store_bar(db_session, low=101, high=106, close=105)

    first = runtime.run_cycle(
        paper_run_id=paper_run.paper_run_id or "",
        request=PaperRuntimeCycleRequest(
            symbols=["BTC/USDT"],
            timeframe="1h",
            close_on_opposite_signal=False,
            enable_decision_veto=False,
        ),
    )

    assert first.closed_positions == 0
    updated_run = PaperRunRepository(db_session).get_paper_run(paper_run.paper_run_id or "")
    trail_state = updated_run.paper_metrics_summary["protective_trailing"]["BTC/USDT"]
    assert trail_state["stop_price"] == 100.0

    _store_bar(db_session, low=97, high=101, close=98, offset_hours=1)
    second = runtime.run_cycle(
        paper_run_id=paper_run.paper_run_id or "",
        request=PaperRuntimeCycleRequest(
            symbols=["BTC/USDT"],
            timeframe="1h",
            close_on_opposite_signal=False,
            enable_decision_veto=False,
        ),
    )

    assert second.closed_positions == 1
    assert second.actions[0].action == "stoploss_close_long"
    assert second.actions[0].reference_price == 100.0


def test_binance_first_gateway_failure_blocks_local_close(db_session, monkeypatch) -> None:
    from shared.config import settings

    monkeypatch.setattr(settings, "binance_auto_execute", True)
    gateway = FailingGateway()
    runtime, paper_run = _runtime_with_position(
        db_session,
        side=TradeSide.SHORT,
        stop_price=110.0,
        take_price=90.0,
        mirror_to_gateway=True,
        gateway=gateway,
    )
    _store_bar(db_session, low=89, high=105, close=95)

    result = runtime.run_cycle(
        paper_run_id=paper_run.paper_run_id or "",
        request=PaperRuntimeCycleRequest(symbols=["BTC/USDT"], timeframe="1h", enable_decision_veto=False),
    )

    assert result.closed_positions == 0
    assert result.rejected_orders == 1
    assert result.open_position_symbols == ["BTC/USDT"]
    assert len(gateway.submitted) == 1
    assert gateway.submitted[0].entry_context["close_only_mode"] is True
    failures = ReviewRepository(db_session).list_failures(failure_type="gateway_mirror_failed")
    assert len(failures) == 1
    assert "testnet balance too low" in failures[0].failure_summary
    latest_position = ExecutionRepository(db_session).list_latest_positions_for_run(
        run_type="paper",
        run_id=paper_run.paper_run_id or "",
    )[0]
    assert latest_position.quantity == -1.0
    rejected_order = ExecutionRepository(db_session).list_orders()[-1]
    assert rejected_order.execution_status == "rejected"
    assert "binance_auto_execute_failed" in rejected_order.rejection_codes


def _runtime_with_position(
    db_session,
    *,
    side: TradeSide,
    stop_price: float,
    take_price: float,
    takeprofit_rules: dict | None = None,
    mirror_to_gateway: bool = False,
    gateway=None,
) -> tuple[PaperRuntimeService, PaperRun]:
    strategy = StrategyRepository(db_session).create_strategy(
        StrategyCreate(
            strategy_key=f"protective_{side}",
            source="open_source:freqtrade",
            core_thesis="Protective orders must close paper positions before signal handling.",
            rules={
                "entry_rules": {"funding_threshold_bps": 1},
                "exit_rules": {},
                "stoploss_rules": {"fixed_bps": 500},
                "takeprofit_rules": takeprofit_rules or {"risk_reward": 2.0},
                "position_rules": {"notional_usdt": 100, "max_leverage": 1},
            },
        )
    )
    backtest = ValidationRepository(db_session).create_backtest_run(
        BacktestRun(
            strategy_id=strategy.strategy_id,
            execution_engine="paper-runtime-test",
            eligibility_result=GateDecision(
                strategy_id=strategy.strategy_id,
                passed=True,
                decision_status="accepted",
                reason="test accepted",
            ),
        )
    )
    paper_run = PaperRunRepository(db_session).create_paper_run(
        PaperRun(
            strategy_id=strategy.strategy_id,
            gate_decision_ref=backtest.backtest_run_id,
            candidate_symbols=["BTC/USDT"],
            execution_profile={
                "account_equity": 10_000,
                "equity_peak": 10_000,
                "mirror_to_gateway": mirror_to_gateway,
            },
            paper_status="running",
        )
    )
    execution_repo = ExecutionRepository(db_session)
    execution_repo.create_order(
        OrderExecution(
            strategy_id=strategy.strategy_id,
            symbol="BTC/USDT",
            direction=side,
            execution_status="filled",
            stoploss_present=True,
            close_only_mode=False,
            entry_context={
                "reference_price": "100",
                "requested_notional": 100,
                "quantity": 1,
                "timeframe": "1h",
            },
            stoploss_plan={"price": stop_price},
            takeprofit_plan={"price": take_price},
            validation_backtest_run_id=backtest.backtest_run_id,
            paper_run_id=paper_run.paper_run_id,
            evaluated_risk_state=ExecutionRiskState(account_equity=10_000, equity_peak=10_000),
        )
    )
    execution_repo.create_position_snapshot(
        PositionSnapshot(
            run_type="paper",
            run_id=paper_run.paper_run_id or "",
            symbol="BTC/USDT",
            side=side,
            quantity=1.0 if side == TradeSide.LONG else -1.0,
            entry_price=100.0,
            mark_price=100.0,
            unrealized_pnl=0.0,
            snapshot_time=datetime.now(UTC) - timedelta(minutes=5),
        )
    )
    runtime = PaperRuntimeService(
        data_repo=DataRepository(db_session),
        execution_repo=execution_repo,
        paper_repo=PaperRunRepository(db_session),
        strategy_repo=StrategyRepository(db_session),
        agent_repo=AgentTaskRepository(db_session),
        review_repo=ReviewRepository(db_session),
        notification_repo=NotificationRepository(db_session),
        gatekeeper=ExecutionGatekeeperService(
            data_repo=DataRepository(db_session),
            validation_repo=ValidationRepository(db_session),
            hypothesis_repo=HypothesisRepository(db_session),
            risk_profile_repo=RiskProfileRepository(db_session),
            execution_repo=ExecutionRepository(db_session),
            paper_repo=PaperRunRepository(db_session),
            review_repo=ReviewRepository(db_session),
        ),
        gateway=gateway,
    )
    return runtime, paper_run


def _store_bar(
    db_session,
    *,
    low: float,
    high: float,
    close: float,
    offset_hours: int = 0,
) -> None:
    now = datetime.now(UTC).replace(microsecond=0) + timedelta(hours=offset_hours)
    DataRepository(db_session).store_ohlcv_bars(
        [
            {
                "symbol": "BTC/USDT",
                "exchange": "binance",
                "timeframe": "1h",
                "time": now,
                "open": Decimal("100"),
                "high": Decimal(str(high)),
                "low": Decimal(str(low)),
                "close": Decimal(str(close)),
                "volume": Decimal("10"),
            }
        ]
    )
