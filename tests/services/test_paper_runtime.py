from datetime import UTC, datetime, timedelta
from decimal import Decimal

from services.data import DataRepository
from services.execution.decision_pipeline import DecisionPipelineResult
from services.execution.gatekeeper import ExecutionGatekeeperService
from services.execution.paper_runtime import PaperRuntimeService, _estimated_transaction_cost
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


def test_runtime_stoploss_wins_over_opposite_signal_hit_on_same_bar(db_session, monkeypatch) -> None:
    """Protective triggers must be honored before an opposite-direction signal
    close, even when both fire on the same bar. Forces the decision pipeline to
    return a SHORT signal (opposite of the open LONG) on a bar whose low also
    breaches the stoploss, then asserts the stoploss close wins.
    """
    runtime, paper_run = _runtime_with_position(
        db_session,
        side=TradeSide.LONG,
        stop_price=95.0,
        take_price=120.0,
    )
    PaperRunRepository(db_session).update_paper_run(
        paper_run.paper_run_id or "",
        execution_profile={**paper_run.execution_profile, "strategy_lane": "directional"},
    )
    _store_bar(db_session, low=94, high=101, close=96)
    latest = DataRepository(db_session).get_latest_ohlcv_bar(symbol="BTC/USDT", timeframe="1h")
    assert latest is not None

    def _forced_opposite_signal(*, strategy, symbol, timeframe, **_kwargs) -> DecisionPipelineResult:
        return DecisionPipelineResult(
            direction=TradeSide.SHORT,
            should_trade=True,
            reason="opposite_signal_forced_for_test",
            reference_price=Decimal("96"),
            bar_time=latest.timestamp,
            signals=[],
            ensemble=None,
            meta_label=None,
            veto_result=None,
            confidence_multiplier=1.0,
            atr=None,
            volatility_context={},
            trace={"pipeline_status": "forced_short_for_test"},
        )

    monkeypatch.setattr(
        runtime.signal_generator.decision_pipeline,
        "evaluate",
        _forced_opposite_signal,
    )

    result = runtime.run_cycle(
        paper_run_id=paper_run.paper_run_id or "",
        request=PaperRuntimeCycleRequest(symbols=["BTC/USDT"], timeframe="1h", enable_decision_veto=False),
    )

    assert result.closed_positions == 1
    assert result.actions[0].action == "stoploss_close_long"
    assert result.actions[0].reference_price == 95.0


def test_runtime_checks_open_position_stoploss_even_when_entry_bar_is_already_processed(db_session) -> None:
    runtime, paper_run = _runtime_with_position(
        db_session,
        side=TradeSide.LONG,
        stop_price=95.0,
        take_price=120.0,
    )
    _store_bar(db_session, low=94, high=101, close=96)
    latest = DataRepository(db_session).get_latest_ohlcv_bar(symbol="BTC/USDT", timeframe="1h")
    assert latest is not None
    PaperRunRepository(db_session).update_paper_run(
        paper_run.paper_run_id or "",
        paper_metrics_summary={
            "processed_cycle_keys": [
                f"{paper_run.paper_run_id}:BTC/USDT:1h:{latest.timestamp.isoformat()}"
            ]
        },
    )

    result = runtime.run_cycle(
        paper_run_id=paper_run.paper_run_id or "",
        request=PaperRuntimeCycleRequest(symbols=["BTC/USDT"], timeframe="1h", enable_decision_veto=False),
    )

    assert result.closed_positions == 1
    assert result.actions[0].action == "stoploss_close_long"


def test_runtime_uses_1m_protection_when_entry_timeframe_data_is_missing(db_session) -> None:
    runtime, paper_run = _runtime_with_position(
        db_session,
        side=TradeSide.LONG,
        stop_price=95.0,
        take_price=120.0,
    )
    _store_bar(db_session, low=94, high=101, close=96, timeframe="1m")

    result = runtime.run_cycle(
        paper_run_id=paper_run.paper_run_id or "",
        request=PaperRuntimeCycleRequest(symbols=["BTC/USDT"], timeframe="15m", enable_decision_veto=False),
    )

    assert result.closed_positions == 1
    assert result.actions[0].action == "stoploss_close_long"


def test_runtime_exits_stagnant_position_after_24_hours_below_half_r(db_session) -> None:
    runtime, paper_run = _runtime_with_position(
        db_session,
        side=TradeSide.LONG,
        stop_price=95.0,
        take_price=120.0,
        exit_rules={"time_exit_hours": 24, "time_exit_min_r": 0.5},
        position_age_hours=25,
    )
    _store_bar(db_session, low=100, high=102, close=101, timeframe="1m")

    result = runtime.run_cycle(
        paper_run_id=paper_run.paper_run_id or "",
        request=PaperRuntimeCycleRequest(symbols=["BTC/USDT"], timeframe="15m", enable_decision_veto=False),
    )

    assert result.closed_positions == 1
    assert result.actions[0].action == "time_exit_close_long"


def test_runtime_locks_and_closes_positions_at_hard_drawdown(db_session) -> None:
    runtime, paper_run = _runtime_with_position(
        db_session,
        side=TradeSide.LONG,
        stop_price=95.0,
        take_price=120.0,
    )
    PaperRunRepository(db_session).update_paper_run(
        paper_run.paper_run_id or "",
        paper_metrics_summary={"account_equity": 7_900, "equity_peak": 10_000},
    )
    _store_bar(db_session, low=98, high=102, close=99, timeframe="1m")

    result = runtime.run_cycle(
        paper_run_id=paper_run.paper_run_id or "",
        request=PaperRuntimeCycleRequest(symbols=["BTC/USDT"], timeframe="15m", enable_decision_veto=False),
    )

    assert result.paper_status == "locked"
    assert result.closed_positions == 1
    assert result.actions[0].action == "hard_drawdown_close_long"


def test_runtime_exit_ladder_level1_partial_and_moves_stop_to_breakeven(db_session) -> None:
    runtime, paper_run = _runtime_with_position(
        db_session,
        side=TradeSide.LONG,
        stop_price=95.0,
        take_price=120.0,
        takeprofit_rules={
            "exit_ladder": [
                {"r_multiple": 1.0, "close_fraction": 0.4},
                {"r_multiple": 1.5, "close_fraction": 0.3},
            ],
            "remainder_trail_after_r": 2.5,
        },
    )
    _store_bar(db_session, low=100, high=106, close=105, timeframe="1m")

    result = runtime.run_cycle(
        paper_run_id=paper_run.paper_run_id or "",
        request=PaperRuntimeCycleRequest(symbols=["BTC/USDT"], timeframe="15m", enable_decision_veto=False),
    )

    assert result.actions[0].action == "exit_ladder_partial_long"
    assert result.actions[0].reference_price == 105.0
    position = ExecutionRepository(db_session).list_latest_positions_for_run(
        run_type="paper", run_id=paper_run.paper_run_id or ""
    )[0]
    assert abs(position.quantity - 0.6) < 1e-9
    updated = PaperRunRepository(db_session).get_paper_run(paper_run.paper_run_id or "")
    assert updated is not None
    ladder = updated.paper_metrics_summary["exit_ladder"]["BTC/USDT"]
    assert ladder["current_stop_price"] == 100.0
    assert ladder["levels"][0]["executed"] is True
    assert updated.paper_metrics_summary["protective_trailing"]["BTC/USDT"]["stop_price"] == 100.0


def test_runtime_exit_ladder_level2_then_remainder_trails(db_session) -> None:
    runtime, paper_run = _runtime_with_position(
        db_session,
        side=TradeSide.LONG,
        stop_price=95.0,
        take_price=120.0,
        takeprofit_rules={
            "exit_ladder": [
                {"r_multiple": 1.0, "close_fraction": 0.4},
                {"r_multiple": 1.5, "close_fraction": 0.3},
            ],
            "remainder_trail_after_r": 2.5,
        },
    )
    # Seed level1 already done.
    PaperRunRepository(db_session).update_paper_run(
        paper_run.paper_run_id or "",
        paper_metrics_summary={
            "exit_ladder": {
                "BTC/USDT": {
                    "symbol": "BTC/USDT",
                    "side": "long",
                    "entry_price": 100.0,
                    "original_quantity": 1.0,
                    "remaining_quantity": 0.6,
                    "initial_stop_price": 95.0,
                    "current_stop_price": 100.0,
                    "remainder_trail_after_r": 2.5,
                    "locked_level1_price": 105.0,
                    "levels": [
                        {"r_multiple": 1.0, "close_fraction": 0.4, "executed": True, "trigger_price": 105.0},
                        {"r_multiple": 1.5, "close_fraction": 0.3, "executed": False, "trigger_price": None},
                    ],
                }
            },
            "protective_trailing": {
                "BTC/USDT": {"stop_price": 100.0, "original_stop_price": 95.0, "entry_price": 100.0}
            },
        },
    )
    ExecutionRepository(db_session).create_position_snapshot(
        PositionSnapshot(
            run_type="paper",
            run_id=paper_run.paper_run_id or "",
            symbol="BTC/USDT",
            side=TradeSide.LONG,
            quantity=0.6,
            entry_price=100.0,
            mark_price=105.0,
            unrealized_pnl=3.0,
            snapshot_time=datetime.now(UTC) - timedelta(minutes=5),
        )
    )
    _store_bar(db_session, low=104, high=108, close=107.5, timeframe="1m")

    first = runtime.run_cycle(
        paper_run_id=paper_run.paper_run_id or "",
        request=PaperRuntimeCycleRequest(symbols=["BTC/USDT"], timeframe="15m", enable_decision_veto=False),
    )
    assert first.actions[0].action == "exit_ladder_partial_long"
    position = ExecutionRepository(db_session).list_latest_positions_for_run(
        run_type="paper", run_id=paper_run.paper_run_id or ""
    )[0]
    assert abs(position.quantity - 0.3) < 1e-9
    updated = PaperRunRepository(db_session).get_paper_run(paper_run.paper_run_id or "")
    assert updated is not None
    ladder = updated.paper_metrics_summary["exit_ladder"]["BTC/USDT"]
    assert ladder["current_stop_price"] == 105.0
    assert ladder["levels"][1]["executed"] is True

    # Favorable move beyond 2.5R from entry (112.5) should ratchet stop to BE floor already locked at 105.
    _store_bar(db_session, low=110, high=113, close=112.5, timeframe="1m", offset_hours=1)
    second = runtime.run_cycle(
        paper_run_id=paper_run.paper_run_id or "",
        request=PaperRuntimeCycleRequest(symbols=["BTC/USDT"], timeframe="15m", enable_decision_veto=False),
    )
    assert second.closed_positions == 0
    trailing = PaperRunRepository(db_session).get_paper_run(paper_run.paper_run_id or "")
    assert trailing is not None
    assert trailing.paper_metrics_summary["protective_trailing"]["BTC/USDT"]["stop_price"] >= 105.0


def test_runtime_partially_takes_profit_at_two_r_and_keeps_trailing_remainder(db_session) -> None:
    runtime, paper_run = _runtime_with_position(
        db_session,
        side=TradeSide.LONG,
        stop_price=95.0,
        take_price=110.0,
        takeprofit_rules={"risk_reward": 2.0, "partial_close_fraction": 0.5},
    )
    _store_bar(db_session, low=106, high=111, close=110, timeframe="1m")

    result = runtime.run_cycle(
        paper_run_id=paper_run.paper_run_id or "",
        request=PaperRuntimeCycleRequest(symbols=["BTC/USDT"], timeframe="15m", enable_decision_veto=False),
    )

    assert result.closed_positions == 0
    assert result.actions[0].action == "partial_takeprofit_long"
    position = ExecutionRepository(db_session).list_latest_positions_for_run(
        run_type="paper", run_id=paper_run.paper_run_id or ""
    )[0]
    assert position.quantity == 0.5


def test_runtime_realized_pnl_includes_configured_transaction_costs(db_session) -> None:
    runtime, paper_run = _runtime_with_position(
        db_session,
        side=TradeSide.LONG,
        stop_price=95.0,
        take_price=120.0,
        fee_bps=100.0,
        slippage_bps=0.0,
    )
    _store_bar(db_session, low=94, high=110, close=100)

    result = runtime.run_cycle(
        paper_run_id=paper_run.paper_run_id or "",
        request=PaperRuntimeCycleRequest(symbols=["BTC/USDT"], timeframe="1h", enable_decision_veto=False),
    )

    assert result.closed_positions == 1
    updated = PaperRunRepository(db_session).get_paper_run(paper_run.paper_run_id or "")
    assert updated is not None
    assert updated.paper_metrics_summary["gross_realized_pnl_total"] == -5.0
    assert updated.paper_metrics_summary["estimated_fee_total"] == 1.95
    assert updated.paper_metrics_summary["net_realized_pnl_total"] == -6.95
    assert updated.paper_metrics_summary["account_equity"] == 9993.05


def test_transaction_cost_uses_core_and_standard_pressure_tiers(db_session) -> None:
    strategy = StrategyRepository(db_session).create_strategy(
        StrategyCreate(
            strategy_key="tiered-costs",
            source="test",
            core_thesis="Costs must be conservative by asset liquidity tier.",
            rules={
                "entry_rules": {
                    "core_fee_bps": 10,
                    "standard_fee_bps": 18,
                    "core_slippage_bps": 0,
                    "standard_slippage_bps": 0,
                },
                "stoploss_rules": {"fixed_bps": 250},
                "takeprofit_rules": {"risk_reward": 2},
                "position_rules": {},
            },
        )
    )

    core = _estimated_transaction_cost(price=100, quantity=1, strategy=strategy, symbol="BTC/USDT")
    standard = _estimated_transaction_cost(price=100, quantity=1, strategy=strategy, symbol="XRP/USDT")

    assert core.fee_bps == 10
    assert standard.fee_bps == 18
    assert core.total_cost == 0.10
    assert standard.total_cost == 0.18


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


def test_runtime_reconciles_local_close_when_exchange_flat_even_if_entry_cycle_already_processed(
    db_session,
) -> None:
    class FlatGateway:
        capability = type("Cap", (), {"gateway_name": "flat_gateway"})()

        def submit_order(self, *, live_run_id: str, order_request: ExecutionOrderRequest) -> dict:
            raise AssertionError("reconcile path must not submit new orders")

        def reconcile(self, *, live_run_id: str) -> dict:
            return {
                "live_run_id": live_run_id,
                "reconciliation_status": "ok",
                "open_order_count": 0,
                "position_mismatches": [],
                "open_positions": [],
            }

    runtime, paper_run = _runtime_with_position(
        db_session,
        side=TradeSide.LONG,
        stop_price=95.0,
        take_price=120.0,
        mirror_to_gateway=True,
        gateway=FlatGateway(),
    )
    _store_bar(db_session, low=99, high=101, close=100, timeframe="1m")
    latest = DataRepository(db_session).get_latest_ohlcv_bar(symbol="BTC/USDT", timeframe="1m")
    assert latest is not None
    # Entry cycle already processed — reconcile must still close local vs exchange flat.
    PaperRunRepository(db_session).update_paper_run(
        paper_run.paper_run_id or "",
        paper_metrics_summary={
            "processed_cycle_keys": [
                f"{paper_run.paper_run_id}:BTC/USDT:15m:{latest.timestamp.isoformat()}"
            ]
        },
    )

    result = runtime.run_cycle(
        paper_run_id=paper_run.paper_run_id or "",
        request=PaperRuntimeCycleRequest(symbols=["BTC/USDT"], timeframe="15m", enable_decision_veto=False),
    )

    assert result.closed_positions == 1
    assert result.actions[0].action == "reconcile_flat_close_long"
    assert result.open_position_symbols == []


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


class ReduceOnlyFlatGateway:
    def __init__(self) -> None:
        self.submitted: list[ExecutionOrderRequest] = []

    def reconcile(self, *, live_run_id: str) -> dict:
        return {"open_positions": []}

    def submit_order(self, *, live_run_id: str, order_request: ExecutionOrderRequest) -> dict:
        self.submitted.append(order_request)
        raise ValueError('binanceusdm {"code":-2022,"msg":"ReduceOnly Order is rejected."}')


def test_reduce_only_already_flat_closes_local_ghost(db_session, monkeypatch) -> None:
    from shared.config import settings

    monkeypatch.setattr(settings, "binance_auto_execute", True)
    gateway = ReduceOnlyFlatGateway()
    runtime, paper_run = _runtime_with_position(
        db_session,
        side=TradeSide.LONG,
        stop_price=90.0,
        take_price=120.0,
        mirror_to_gateway=True,
        gateway=gateway,
    )
    # Reconcile empties first if exchange flat — seed bar then force protective path by
    # making reconcile report the position still "present" would skip. Here reconcile is
    # empty so ghost is cleared at reconcile stage before protective close.
    result = runtime.run_cycle(
        paper_run_id=paper_run.paper_run_id or "",
        request=PaperRuntimeCycleRequest(symbols=["BTC/USDT"], timeframe="1h", enable_decision_veto=False),
    )
    assert result.closed_positions == 1
    assert result.open_position_symbols == []
    assert any(action.action.startswith("reconcile_flat_close_") for action in result.actions)


def test_reduce_only_flat_on_protective_close_clears_local(db_session, monkeypatch) -> None:
    from shared.config import settings

    monkeypatch.setattr(settings, "binance_auto_execute", True)

    class StickyExchangeGateway(ReduceOnlyFlatGateway):
        def reconcile(self, *, live_run_id: str) -> dict:
            # Pretend exchange still has the position so reconcile does not clear it;
            # protective close then hits ReduceOnly -2022 (race / stale snapshot).
            return {
                "open_positions": [
                    {"symbol": "BTC/USDT:USDT", "contracts": 1.0, "side": "long"},
                ]
            }

    gateway = StickyExchangeGateway()
    runtime, paper_run = _runtime_with_position(
        db_session,
        side=TradeSide.LONG,
        stop_price=95.0,
        take_price=110.0,
        mirror_to_gateway=True,
        gateway=gateway,
    )
    _store_bar(db_session, low=94, high=100, close=96)

    result = runtime.run_cycle(
        paper_run_id=paper_run.paper_run_id or "",
        request=PaperRuntimeCycleRequest(symbols=["BTC/USDT"], timeframe="1h", enable_decision_veto=False),
    )

    assert result.closed_positions == 1
    assert result.open_position_symbols == []
    assert result.rejected_orders == 0
    closed_order = ExecutionRepository(db_session).list_orders()[-1]
    assert closed_order.execution_status == "filled"
    assert closed_order.entry_context.get("exchange_already_flat") is True
    assert any("exchange_already_flat" in str(item.get("status", "")) for item in closed_order.lifecycle_history)


def _runtime_with_position(
    db_session,
    *,
    side: TradeSide,
    stop_price: float,
    take_price: float,
    takeprofit_rules: dict | None = None,
    exit_rules: dict | None = None,
    fee_bps: float = 8.0,
    slippage_bps: float = 6.0,
    mirror_to_gateway: bool = False,
    gateway=None,
    position_age_hours: int = 0,
) -> tuple[PaperRuntimeService, PaperRun]:
    strategy = StrategyRepository(db_session).create_strategy(
        StrategyCreate(
            strategy_key=f"protective_{side}",
            source="open_source:freqtrade",
            core_thesis="Protective orders must close paper positions before signal handling.",
            rules={
                "entry_rules": {"funding_threshold_bps": 1, "fee_bps": fee_bps, "slippage_bps": slippage_bps},
                "exit_rules": exit_rules or {},
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
                "cost_gate_verified": mirror_to_gateway,
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
            snapshot_time=datetime.now(UTC) - timedelta(minutes=5) - timedelta(hours=position_age_hours),
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
    timeframe: str = "1h",
) -> None:
    now = datetime.now(UTC).replace(microsecond=0) + timedelta(hours=offset_hours)
    DataRepository(db_session).store_ohlcv_bars(
        [
            {
                "symbol": "BTC/USDT",
                "exchange": "binance",
                "timeframe": timeframe,
                "time": now,
                "open": Decimal("100"),
                "high": Decimal(str(high)),
                "low": Decimal(str(low)),
                "close": Decimal(str(close)),
                "volume": Decimal("10"),
            }
        ]
    )
