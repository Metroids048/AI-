from datetime import UTC, datetime, timedelta
from decimal import Decimal

from services.data import DataRepository
from services.data.universe import execution_scope_hash
from services.execution.decision_pipeline import DecisionPipelineResult
from services.execution.gatekeeper import ExecutionGatekeeperService
from services.execution.paper_cycle_orchestrator import (
    PaperCycleOrchestrator,
    _estimated_transaction_cost,
    _fixed_universe_skip_reason,
)
from services.execution.paper_runtime import PaperRuntimeService
from services.strategy_library import (
    AgentTaskRepository,
    ConfigSnapshotRepository,
    DecisionFunnelRepository,
    DecisionSnapshotRepository,
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
    ConfigSnapshot,
    ExchangeAccountSnapshot,
    ExecutionOrderRequest,
    ExecutionRiskState,
    GateDecision,
    MarketRulesSnapshot,
    OrderExecution,
    PaperRun,
    PaperRuntimeCycleRequest,
    PositionManagementStatus,
    PositionRecord,
    PositionSnapshot,
    PretradeMarketSnapshot,
    ProtectionRecord,
    StrategyCreate,
    TradeSide,
)


class FailingGateway:
    def __init__(self) -> None:
        self.submitted: list[ExecutionOrderRequest] = []

    def submit_order(self, *, live_run_id: str, order_request: ExecutionOrderRequest) -> dict:
        self.submitted.append(order_request)
        raise ValueError("testnet balance too low")

    def reconcile(self, *, live_run_id: str) -> dict:
        del live_run_id
        return {
            "open_positions": [
                {
                    "symbol": "BTC/USDT:USDT",
                    "contracts": 1.0,
                    "side": "short",
                    "entry_price": 100.0,
                    "mark_price": 95.0,
                }
            ],
            "open_orders": [],
        }


def test_runtime_activates_pending_config_at_cycle_boundary(db_session) -> None:
    runtime, paper_run = _runtime_with_position(
        db_session,
        side=TradeSide.LONG,
        stop_price=95.0,
        take_price=120.0,
    )
    repo = ConfigSnapshotRepository(db_session)
    initial = repo.create_snapshot(
        ConfigSnapshot.create(
            paper_run_id=paper_run.paper_run_id or "",
            config={"execution_profile": paper_run.execution_profile},
            created_by="bootstrap",
            effective_cycle_id="seed",
        ),
        base_config_hash=None,
    )
    pending = repo.create_snapshot(
        ConfigSnapshot.create(
            paper_run_id=paper_run.paper_run_id or "",
            config={"execution_profile": {**paper_run.execution_profile, "llm_veto_enabled": False}},
            created_by="operator",
            effective_cycle_id="NEXT_CYCLE",
            previous_snapshot_id=initial.config_snapshot_id,
        ),
        base_config_hash=initial.config_hash,
    )
    _store_bar(db_session, low=99, high=101, close=100)

    runtime.run_cycle(
        paper_run_id=paper_run.paper_run_id or "",
        request=PaperRuntimeCycleRequest(symbols=["BTC/USDT"], timeframe="1h", enable_decision_veto=False),
    )

    assert repo.get_active(paper_run.paper_run_id or "").config_hash == pending.config_hash
    assert repo.get_pending(paper_run.paper_run_id or "") is None


def test_runtime_exposes_order_lifecycle_that_persists_fills(db_session) -> None:
    runtime, paper_run = _runtime_with_position(
        db_session,
        side=TradeSide.LONG,
        stop_price=95.0,
        take_price=120.0,
    )
    order = ExecutionRepository(db_session).list_orders()[0]
    filled = runtime.order_lifecycle.fill_order(
        order=order,
        cycle_time=datetime.now(UTC),
    )

    assert filled.execution_status == "filled"
    assert filled.paper_run_id == paper_run.paper_run_id


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


def test_runtime_protective_close_gate_rejection_keeps_trigger_price_and_position_open(db_session, monkeypatch) -> None:
    runtime, paper_run = _runtime_with_position(
        db_session,
        side=TradeSide.LONG,
        stop_price=95.0,
        take_price=120.0,
    )
    _store_bar(db_session, low=94, high=101, close=96)

    def _reject_close(request: ExecutionOrderRequest) -> OrderExecution:
        return OrderExecution(
            order_execution_id="forced-close-rejection",
            strategy_id=request.strategy_id,
            version_id=request.version_id,
            symbol=request.symbol,
            direction=request.direction,
            execution_status="rejected",
            close_only_mode=True,
            rejection_reason="forced_gate_rejection",
            rejection_codes=["forced_gate_rejection"],
            entry_context=request.entry_context,
            paper_run_id=request.paper_run_id,
        )

    monkeypatch.setattr(runtime.gatekeeper, "submit_order", _reject_close)

    result = runtime.run_cycle(
        paper_run_id=paper_run.paper_run_id or "",
        request=PaperRuntimeCycleRequest(symbols=["BTC/USDT"], timeframe="1h", enable_decision_veto=False),
    )

    assert result.closed_positions == 0
    assert result.open_position_symbols == ["BTC/USDT"]
    assert result.rejected_orders == 1
    assert result.actions[0].action == "rejected"
    assert result.actions[0].reference_price == 95.0
    assert result.actions[0].reason == "forced_gate_rejection"


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


def test_runtime_opposite_signal_close_gate_rejection_keeps_reference_price_and_position_open(
    db_session, monkeypatch
) -> None:
    runtime, paper_run = _runtime_with_position(
        db_session,
        side=TradeSide.LONG,
        stop_price=90.0,
        take_price=120.0,
    )
    PaperRunRepository(db_session).update_paper_run(
        paper_run.paper_run_id or "",
        execution_profile={**paper_run.execution_profile, "strategy_lane": "directional"},
    )
    _store_bar(db_session, low=95, high=101, close=96)
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

    def _reject_close(request: ExecutionOrderRequest) -> OrderExecution:
        return OrderExecution(
            order_execution_id="forced-opposite-close-rejection",
            strategy_id=request.strategy_id,
            version_id=request.version_id,
            symbol=request.symbol,
            direction=request.direction,
            execution_status="rejected",
            close_only_mode=True,
            rejection_reason="forced_gate_rejection",
            rejection_codes=["forced_gate_rejection"],
            entry_context=request.entry_context,
            paper_run_id=request.paper_run_id,
        )

    monkeypatch.setattr(runtime.signal_generator.decision_pipeline, "evaluate", _forced_opposite_signal)
    monkeypatch.setattr(runtime.gatekeeper, "submit_order", _reject_close)

    result = runtime.run_cycle(
        paper_run_id=paper_run.paper_run_id or "",
        request=PaperRuntimeCycleRequest(symbols=["BTC/USDT"], timeframe="1h", enable_decision_veto=False),
    )

    assert result.closed_positions == 0
    assert result.open_position_symbols == ["BTC/USDT"]
    assert result.rejected_orders == 1
    assert result.actions[0].action == "rejected"
    assert result.actions[0].reference_price == 96.0


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
            "processed_cycle_keys": [f"{paper_run.paper_run_id}:BTC/USDT:1h:{latest.timestamp.isoformat()}"]
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


def test_runtime_does_not_lock_after_rebasing_initial_exchange_equity(db_session) -> None:
    runtime, paper_run = _runtime_without_position(db_session)
    ExecutionRepository(db_session).create_account_snapshot(
        ExchangeAccountSnapshot(
            live_run_id="console_probe",
            exchange="binance",
            wallet_balance=5_250.75,
            available_balance=5_100.0,
            margin_balance=5_250.75,
            snapshot_time=datetime.now(UTC),
        )
    )
    _store_bar(db_session, low=99, high=101, close=100)
    request = PaperRuntimeCycleRequest(
        symbols=["BTC/USDT"],
        timeframe="1h",
        enable_decision_veto=False,
    )

    first = runtime.run_cycle(paper_run_id=paper_run.paper_run_id or "", request=request)
    second = runtime.run_cycle(paper_run_id=paper_run.paper_run_id or "", request=request)
    persisted = PaperRunRepository(db_session).get_paper_run(paper_run.paper_run_id or "")

    assert first.paper_status == "running"
    assert second.paper_status == "running"
    assert persisted is not None
    assert persisted.paper_metrics_summary["account_equity"] == 5_250.75
    assert persisted.paper_metrics_summary["equity_peak"] == 5_250.75


def test_runtime_retries_same_bar_after_data_freshness_recovers(db_session, monkeypatch) -> None:
    runtime, paper_run = _runtime_without_position(db_session)
    _store_bar(db_session, low=99, high=101, close=100, offset_hours=-3)

    def _forced_signal(**_kwargs) -> DecisionPipelineResult:
        return DecisionPipelineResult(
            direction=TradeSide.LONG,
            should_trade=True,
            reason="forced_retryable_signal",
            reference_price=Decimal("100"),
            bar_time=datetime.now(UTC) - timedelta(hours=3),
            signals=[],
            ensemble=None,
            meta_label=None,
            veto_result=None,
            confidence_multiplier=1.0,
            atr=1.0,
            volatility_context={},
            trace={
                "pipeline_status": "bet_taken",
                "strategy_lane": "directional",
                "meta_label_win_rate": 0.8,
                "meta_label_average_win": 0.02,
                "meta_label_average_loss": 0.01,
                "round_trip_fee_rate": 0.0002,
                "round_trip_slippage_rate": 0.0,
            },
        )

    monkeypatch.setattr(runtime.signal_generator.decision_pipeline, "evaluate", _forced_signal)
    monkeypatch.setattr("services.execution.gatekeeper.settings.execution_freshness_delay_seconds", 2 * 60 * 60)
    request = PaperRuntimeCycleRequest(
        symbols=["BTC/USDT"],
        timeframe="1h",
        enable_decision_veto=False,
    )

    first = runtime.run_cycle(paper_run_id=paper_run.paper_run_id or "", request=request)
    monkeypatch.setattr("services.execution.gatekeeper.settings.execution_freshness_delay_seconds", 4 * 60 * 60)
    second = runtime.run_cycle(paper_run_id=paper_run.paper_run_id or "", request=request)

    assert first.actions[0].action == "rejected"
    assert "data_not_fresh" in (first.actions[0].reason or "")
    assert second.opened_positions == 1
    assert second.actions[0].action == "open_long"


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
    execution_repo = ExecutionRepository(db_session)
    existing_position = execution_repo.list_latest_positions_for_run(
        run_type="paper",
        run_id=paper_run.paper_run_id or "",
    )[0]
    execution_repo.create_position_snapshot(
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
            position_record_id=existing_position.position_record_id,
        )
    )
    _store_bar(db_session, low=104, high=108, close=107.5, timeframe="1m", offset_hours=-1)

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
    _store_bar(db_session, low=110, high=113, close=112.5, timeframe="1m")
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
    _store_bar(db_session, low=101, high=106, close=105, offset_hours=-1)

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

    _store_bar(db_session, low=97, high=101, close=98)
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
            "processed_cycle_keys": [f"{paper_run.paper_run_id}:BTC/USDT:15m:{latest.timestamp.isoformat()}"]
        },
    )

    first = runtime.run_cycle(
        paper_run_id=paper_run.paper_run_id or "",
        request=PaperRuntimeCycleRequest(symbols=["BTC/USDT"], timeframe="15m", enable_decision_veto=False),
    )
    result = runtime.run_cycle(
        paper_run_id=paper_run.paper_run_id or "",
        request=PaperRuntimeCycleRequest(symbols=["BTC/USDT"], timeframe="15m", enable_decision_veto=False),
    )

    assert first.closed_positions == 0
    assert first.actions[0].action == "reconcile_exchange_position_missing_pending"
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
    assert len(gateway.submitted) >= 1
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
    assert rejected_order.execution_status == "EXCHANGE_REJECTED"
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
    first = runtime.run_cycle(
        paper_run_id=paper_run.paper_run_id or "",
        request=PaperRuntimeCycleRequest(symbols=["BTC/USDT"], timeframe="1h", enable_decision_veto=False),
    )
    result = runtime.run_cycle(
        paper_run_id=paper_run.paper_run_id or "",
        request=PaperRuntimeCycleRequest(symbols=["BTC/USDT"], timeframe="1h", enable_decision_veto=False),
    )
    assert first.closed_positions == 0
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
                    {
                        "symbol": "BTC/USDT:USDT",
                        "contracts": 1.0,
                        "side": "long",
                        "entry_price": 100.0,
                        "mark_price": 100.0,
                    },
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

    assert result.closed_positions == 0
    assert result.open_position_symbols == ["BTC/USDT"]
    assert result.rejected_orders >= 1
    closed_order = ExecutionRepository(db_session).list_orders()[-1]
    assert closed_order.execution_status == "EXCHANGE_REJECTED"
    assert closed_order.entry_context.get("exchange_already_flat") is not True


def test_runtime_persists_decision_snapshot_for_skip_no_trade_decision(db_session, monkeypatch) -> None:
    """skip_no_trade_decision actions never create an OrderExecution row, so the
    only durable record of rejection reasons like technical_signals_insufficient
    is the DecisionSnapshot appended here."""
    runtime, paper_run = _runtime_without_position(db_session)
    _store_bar(db_session, low=99, high=101, close=100)

    def _forced_skip(*, strategy, symbol, timeframe, **_kwargs) -> DecisionPipelineResult:
        return DecisionPipelineResult(
            direction=None,
            should_trade=False,
            reason="technical_signals_insufficient",
            reference_price=Decimal("100"),
            bar_time=datetime.now(UTC),
            signals=[],
            ensemble=None,
            meta_label=None,
            veto_result=None,
            confidence_multiplier=0.0,
            atr=None,
            volatility_context={},
            trace={"pipeline_status": "technical_signals_insufficient"},
        )

    monkeypatch.setattr(
        runtime.signal_generator.decision_pipeline,
        "evaluate",
        _forced_skip,
    )

    result = runtime.run_cycle(
        paper_run_id=paper_run.paper_run_id or "",
        request=PaperRuntimeCycleRequest(symbols=["BTC/USDT"], timeframe="1h", enable_decision_veto=False),
    )

    assert result.actions[0].action == "skip_no_trade_decision"
    snapshots = DecisionSnapshotRepository(db_session).list_snapshots(paper_run_id=paper_run.paper_run_id)
    assert len(snapshots) == 1
    assert snapshots[0].pipeline_status == "technical_signals_insufficient"
    assert snapshots[0].action == "skip_no_trade_decision"
    terminals = DecisionFunnelRepository(db_session).list_terminals(
        paper_run_id=paper_run.paper_run_id,
        symbol="BTC/USDT",
    )
    assert len(terminals) == 1
    assert terminals[0].terminal_stage.value == "entry_signal"
    assert terminals[0].status.value == "SKIPPED"
    assert terminals[0].reason_code == "TECHNICAL_SIGNALS_INSUFFICIENT"


def test_paper_only_fixed_universe_does_not_block_on_initial_unknown_exchange_status() -> None:
    profile = {
        "universe_mode": "fixed_top20",
        "execution_mode": "local_paper",
        "mirror_to_gateway": False,
        "universe_assets": [
            {
                "platform_symbol": "BTC/USDT",
                "tradable_status": "unknown",
                "reason": "exchangeInfo unavailable during bootstrap",
            }
        ],
    }
    paper_run = PaperRun(strategy_id="paper-only", execution_profile=profile)

    assert _fixed_universe_skip_reason(paper_run, "BTC/USDT") is None

    mirrored = paper_run.model_copy(update={"execution_profile": {**profile, "mirror_to_gateway": True}})

    assert _fixed_universe_skip_reason(mirrored, "BTC/USDT") == "exchangeInfo unavailable during bootstrap"


def _runtime_without_position(
    db_session,
    *,
    gateway=None,
    mirror_to_gateway: bool = False,
) -> tuple[PaperRuntimeService, PaperRun]:
    strategy = StrategyRepository(db_session).create_strategy(
        StrategyCreate(
            strategy_key="decision_snapshot_persistence",
            source="open_source:freqtrade",
            core_thesis="Verify decision snapshots persist for no-trade skips.",
            rules={
                "entry_rules": {"fee_bps": 8.0, "slippage_bps": 6.0},
                "exit_rules": {},
                "stoploss_rules": {"fixed_bps": 500},
                "takeprofit_rules": {"risk_reward": 2.0},
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
                "strategy_lane": "directional",
                "execution_mode": "binance_testnet" if mirror_to_gateway else "local_paper",
                "mirror_to_gateway": mirror_to_gateway,
                "cost_gate_verified": mirror_to_gateway,
            },
            paper_status="running",
        )
    )
    execution_repo = ExecutionRepository(db_session)
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


def test_binance_submitted_entry_does_not_create_local_filled_position(db_session, monkeypatch) -> None:
    from shared.config import settings

    class SubmittedGateway:
        capability = type("Cap", (), {"gateway_name": "submitted_gateway"})()

        def reconcile(self, *, live_run_id: str) -> dict:
            del live_run_id
            return {"open_positions": [], "open_orders": []}

        def load_market_rules_snapshot(self, *, symbol, leverage, loaded_at):  # noqa: ANN001
            return MarketRulesSnapshot(
                rules_snapshot_id="rules:submitted-gateway",
                symbol=symbol,
                market_status="TRADING",
                position_mode="ONE_WAY",
                margin_mode="CROSS",
                leverage=leverage,
                tick_size=Decimal("0.1"),
                step_size=Decimal("0.001"),
                min_quantity=Decimal("0.001"),
                min_notional=Decimal("5"),
                loaded_at=loaded_at,
                exchange="binance",
                market_type="swap",
                exchange_symbol="BTC/USDT:USDT",
                price_precision=1,
                amount_precision=3,
                contract_size=Decimal("1"),
                market_active=True,
            )

        def pretrade_market_snapshot(
            self,
            *,
            order_request: ExecutionOrderRequest,
        ) -> PretradeMarketSnapshot:
            del order_request
            now = datetime.now(UTC)
            return PretradeMarketSnapshot(
                server_time=now,
                bid=Decimal("99.9"),
                ask=Decimal("100.1"),
                mark_price=Decimal("100"),
                decision_bar_close_time=now - timedelta(seconds=10),
                decision_age_seconds=10,
                atr=Decimal("1"),
                tick_size=Decimal("0.1"),
                step_size=Decimal("0.001"),
            )

        def submit_order(self, *, live_run_id: str, order_request: ExecutionOrderRequest) -> dict:
            return {
                "gateway_order_id": "pending-entry-1",
                "gateway_status": "submitted",
                "protection_order_refs": [],
            }

    monkeypatch.setattr(settings, "binance_auto_execute", True)
    monkeypatch.setattr(settings, "binance_use_testnet", True)
    monkeypatch.setattr(settings, "live_trading_enabled", False)
    runtime, paper_run = _runtime_without_position(
        db_session,
        gateway=SubmittedGateway(),
        mirror_to_gateway=True,
    )
    _store_bar(db_session, low=99, high=101, close=100)
    latest = DataRepository(db_session).get_latest_ohlcv_bar(symbol="BTC/USDT", timeframe="1h")
    assert latest is not None

    def _forced_trade(*, strategy, symbol, timeframe, **_kwargs) -> DecisionPipelineResult:
        return DecisionPipelineResult(
            direction=TradeSide.LONG,
            should_trade=True,
            reason="forced_trade",
            reference_price=Decimal("100"),
            bar_time=latest.timestamp,
            signals=[],
            ensemble=None,
            meta_label=None,
            veto_result=None,
            confidence_multiplier=1.0,
            atr=None,
            volatility_context={},
            trace={
                "pipeline_status": "bet_taken",
                "strategy_lane": "directional",
                "meta_label_win_rate": 1.0,
                "meta_label_average_win": 0.10,
                "meta_label_average_loss": 0.0,
                "round_trip_fee_rate": 0.001,
                "round_trip_slippage_rate": 0.0002,
            },
        )

    monkeypatch.setattr(runtime.signal_generator.decision_pipeline, "evaluate", _forced_trade)

    result = runtime.run_cycle(
        paper_run_id=paper_run.paper_run_id or "",
        request=PaperRuntimeCycleRequest(symbols=["BTC/USDT"], timeframe="1h", enable_decision_veto=False),
    )

    assert result.opened_positions == 0
    assert result.rejected_orders == 0
    assert result.actions[0].action == "pending_gateway_fill"
    assert result.open_position_symbols == []
    order = ExecutionRepository(db_session).list_orders()[-1]
    assert order.execution_status == "submitted"
    assert order.gateway_order_id == "pending-entry-1"


def test_runtime_reconcile_requires_confirmed_exchange_flat_before_closing_local(db_session) -> None:
    class EventuallyConsistentGateway:
        capability = type("Cap", (), {"gateway_name": "eventual_gateway"})()

        def __init__(self) -> None:
            self.reconcile_calls = 0

        def reconcile(self, *, live_run_id: str) -> dict:
            self.reconcile_calls += 1
            positions = []
            if self.reconcile_calls > 1:
                positions = [
                    {
                        "symbol": "BTC/USDT:USDT",
                        "contracts": 1.0,
                        "side": "long",
                        "entry_price": 100.0,
                        "mark_price": 100.0,
                    }
                ]
            return {"open_positions": positions, "open_orders": []}

        def submit_order(self, *, live_run_id: str, order_request: ExecutionOrderRequest) -> dict:
            raise AssertionError("protected position must not be resubmitted")

    gateway = EventuallyConsistentGateway()
    runtime, paper_run = _runtime_with_position(
        db_session,
        side=TradeSide.LONG,
        stop_price=95.0,
        take_price=120.0,
        mirror_to_gateway=True,
        gateway=gateway,
    )

    result = runtime.run_cycle(
        paper_run_id=paper_run.paper_run_id or "",
        request=PaperRuntimeCycleRequest(symbols=[], timeframe="15m", enable_decision_veto=False),
    )

    assert gateway.reconcile_calls == 2
    assert result.closed_positions == 0
    assert result.open_position_symbols == ["BTC/USDT"]


def test_runtime_reconcile_recovers_exchange_only_position(db_session) -> None:
    class ExchangePositionGateway:
        capability = type("Cap", (), {"gateway_name": "recovery_gateway"})()

        def reconcile(self, *, live_run_id: str) -> dict:
            return {
                "open_positions": [
                    {
                        "symbol": "BTC/USDT:USDT",
                        "contracts": 0.25,
                        "side": "short",
                        "entry_price": 101.0,
                        "mark_price": 100.0,
                        "unrealized_pnl": 0.25,
                    }
                ],
                "open_orders": [],
            }

        def submit_order(self, *, live_run_id: str, order_request: ExecutionOrderRequest) -> dict:
            raise AssertionError("recovery must not submit an entry")

    runtime, paper_run = _runtime_without_position(
        db_session,
        gateway=ExchangePositionGateway(),
        mirror_to_gateway=True,
    )

    result = runtime.run_cycle(
        paper_run_id=paper_run.paper_run_id or "",
        request=PaperRuntimeCycleRequest(symbols=[], timeframe="15m", enable_decision_veto=False),
    )

    assert result.open_position_symbols == []
    assert any(action.action == "reconcile_unmanaged_external_position" for action in result.actions)
    recovered = ExecutionRepository(db_session).list_latest_positions_for_run(
        run_type="paper",
        run_id=paper_run.paper_run_id or "",
    )[0]
    assert recovered.side == TradeSide.SHORT
    assert recovered.quantity == 0.25
    assert recovered.entry_price == 101.0
    assert recovered.position_record_id is not None
    record = ExecutionRepository(db_session).get_position_record(recovered.position_record_id)
    assert record is not None
    assert record.management_status is PositionManagementStatus.UNMANAGED_EXTERNAL_POSITION


def test_runtime_does_not_open_over_unmanaged_exchange_position(db_session, monkeypatch) -> None:
    """An exchange position without managed identity must block a fresh entry.

    In one-way mode, submitting a new order could merge with, reduce, or reverse
    the external position. The directional scheduler must leave it untouched.
    """
    from shared.config import settings

    class ExternalPositionGateway:
        capability = type("Cap", (), {"gateway_name": "binance_usdt_perpetual"})()

        def __init__(self) -> None:
            self.submitted: list[ExecutionOrderRequest] = []

        def account_equity(self) -> float:
            return 10_000.0

        def sync_account(self, *, live_run_id: str) -> ExchangeAccountSnapshot:
            del live_run_id
            return ExchangeAccountSnapshot(
                exchange="binance",
                wallet_balance=10_000.0,
                margin_balance=10_000.0,
                available_balance=10_000.0,
                snapshot_time=datetime.now(UTC),
            )

        def reconcile(self, *, live_run_id: str) -> dict:
            del live_run_id
            return {
                "open_positions": [
                    {
                        "symbol": "BTC/USDT:USDT",
                        "contracts": 0.25,
                        "side": "long",
                        "entry_price": 100.0,
                        "mark_price": 101.0,
                        "unrealized_pnl": 0.25,
                    }
                ],
                "open_orders": [],
            }

        def load_market_rules_snapshot(self, *, symbol, leverage, loaded_at):  # noqa: ANN001
            return MarketRulesSnapshot(
                rules_snapshot_id=f"rules:{symbol}",
                symbol=symbol,
                market_status="TRADING",
                position_mode="ONE_WAY",
                margin_mode="CROSS",
                leverage=leverage,
                tick_size=Decimal("0.1"),
                step_size=Decimal("0.001"),
                min_quantity=Decimal("0.001"),
                min_notional=Decimal("5"),
                loaded_at=loaded_at,
                exchange="binance",
                market_type="swap",
                exchange_symbol=f"{symbol}:USDT",
                price_precision=1,
                amount_precision=3,
                contract_size=Decimal("1"),
                market_active=True,
            )

        def submit_order(self, *, live_run_id: str, order_request: ExecutionOrderRequest) -> dict:
            del live_run_id
            self.submitted.append(order_request)
            raise AssertionError("unmanaged exchange exposure must block a fresh entry")

    monkeypatch.setattr(settings, "binance_auto_execute", True)
    monkeypatch.setattr(settings, "binance_use_testnet", True)
    monkeypatch.setattr(settings, "live_trading_enabled", False)
    gateway = ExternalPositionGateway()
    runtime, paper_run = _runtime_without_position(db_session, gateway=gateway, mirror_to_gateway=True)
    armed_profile = {
        **paper_run.execution_profile,
        "strategy_lane": "directional",
        "execution_mode": "binance_testnet",
        "mirror_to_gateway": True,
        "cost_gate_verified": True,
        "acceptance_symbols": ["BTC/USDT", "ETH/USDT"],
        "acceptance_scope_hash": execution_scope_hash(),
    }
    paper_run = PaperRunRepository(db_session).update_paper_run(
        paper_run.paper_run_id or "",
        execution_profile=armed_profile,
    )
    assert paper_run is not None
    strategy = StrategyRepository(db_session).get_strategy(paper_run.strategy_id)
    assert strategy is not None
    ConfigSnapshotRepository(db_session).create_snapshot(
        ConfigSnapshot.create(
            paper_run_id=paper_run.paper_run_id or "",
            config={
                "execution_profile": armed_profile,
                "strategy_rules": strategy.rules.model_dump(mode="json"),
            },
            created_by="unmanaged-position-test",
            effective_cycle_id="seed",
        ),
        base_config_hash=None,
    )
    _store_bar(db_session, low=99, high=102, close=101, timeframe="15m")
    latest = DataRepository(db_session).get_latest_ohlcv_bar(symbol="BTC/USDT", timeframe="15m")
    assert latest is not None

    monkeypatch.setattr(
        runtime.signal_generator.decision_pipeline,
        "evaluate",
        lambda **_kwargs: DecisionPipelineResult(
            direction=TradeSide.LONG,
            should_trade=True,
            reason="forced_directional_entry",
            reference_price=Decimal("101"),
            bar_time=latest.timestamp,
            signals=[],
            ensemble=None,
            meta_label=None,
            veto_result=None,
            confidence_multiplier=1.0,
            atr=1.0,
            volatility_context={},
            trace={
                "pipeline_status": "bet_taken",
                "strategy_lane": "directional",
                "meta_label_win_rate": 0.8,
                "meta_label_average_win": 0.02,
                "meta_label_average_loss": 0.01,
                "round_trip_fee_rate": 0.0002,
                "round_trip_slippage_rate": 0.0,
            },
        ),
    )

    result = runtime.run_cycle(
        paper_run_id=paper_run.paper_run_id or "",
        request=PaperRuntimeCycleRequest(symbols=["BTC/USDT"], timeframe="15m", enable_decision_veto=False),
    )

    assert gateway.submitted == []
    assert result.opened_positions == 0
    assert any(action.action == "skip_unmanaged_external_position" for action in result.actions)
    persisted = PaperRunRepository(db_session).get_paper_run(paper_run.paper_run_id or "")
    assert persisted is not None
    assert persisted.paper_metrics_summary["unmanaged_external_symbols"] == ["BTC/USDT"]


def test_runtime_reconcile_cancels_orphan_exchange_protection(db_session) -> None:
    class OrphanProtectionGateway:
        capability = type("Cap", (), {"gateway_name": "orphan_gateway"})()

        def __init__(self) -> None:
            self.cancelled: list[tuple[str, str]] = []

        def reconcile(self, *, live_run_id: str) -> dict:
            return {
                "open_positions": [],
                "open_orders": [
                    {
                        "algoId": "orphan-tp-1",
                        "symbol": "BTCUSDT",
                        "orderType": "TAKE_PROFIT_MARKET",
                        "reduceOnly": True,
                    }
                ],
            }

        def cancel_protection_order(self, *, symbol: str, gateway_order_id: str) -> None:
            self.cancelled.append((symbol, gateway_order_id))

        def submit_order(self, *, live_run_id: str, order_request: ExecutionOrderRequest) -> dict:
            raise AssertionError("orphan cleanup must not submit an order")

    gateway = OrphanProtectionGateway()
    runtime, paper_run = _runtime_without_position(db_session, gateway=gateway, mirror_to_gateway=True)

    result = runtime.run_cycle(
        paper_run_id=paper_run.paper_run_id or "",
        request=PaperRuntimeCycleRequest(symbols=[], timeframe="15m", enable_decision_veto=False),
    )

    assert gateway.cancelled == [("BTC/USDT", "orphan-tp-1")]
    assert any(action.action == "reconcile_cancel_orphan_protection" for action in result.actions)


def test_runtime_reconcile_rearms_missing_exchange_protection(db_session) -> None:
    class MissingProtectionGateway:
        capability = type("Cap", (), {"gateway_name": "missing_protection_gateway"})()

        def __init__(self) -> None:
            self.refresh_calls: list[tuple[str, float]] = []

        def reconcile(self, *, live_run_id: str) -> dict:
            return {
                "open_positions": [
                    {
                        "symbol": "BTC/USDT:USDT",
                        "contracts": 1.0,
                        "side": "long",
                        "entry_price": 100.0,
                        "mark_price": 101.0,
                    }
                ],
                "open_orders": [],
            }

        def refresh_protection_orders(self, *, order_request, quantity, previous_refs):  # noqa: ANN001
            self.refresh_calls.append((order_request.symbol, quantity))
            assert order_request.stoploss_plan["price"] == 95.0
            assert order_request.takeprofit_plan["price"] == 120.0
            return [
                {"algoId": "rearmed-stop", "orderType": "STOP_MARKET"},
                {"algoId": "rearmed-take", "orderType": "TAKE_PROFIT_MARKET"},
            ]

    gateway = MissingProtectionGateway()
    runtime, paper_run = _runtime_with_position(
        db_session,
        side=TradeSide.LONG,
        stop_price=95.0,
        take_price=120.0,
        mirror_to_gateway=True,
        gateway=gateway,
    )

    result = runtime.run_cycle(
        paper_run_id=paper_run.paper_run_id or "",
        request=PaperRuntimeCycleRequest(symbols=[], timeframe="15m", enable_decision_veto=False),
    )

    assert gateway.refresh_calls == [("BTC/USDT", 1.0)]
    assert any(action.action == "reconcile_rearm_protection" for action in result.actions)


def test_runtime_gateway_close_cancels_entry_protection_orders(db_session, monkeypatch) -> None:
    from shared.config import settings

    class ProtectedPositionGateway:
        capability = type("Cap", (), {"gateway_name": "protected_gateway"})()

        def __init__(self) -> None:
            self.cancelled: list[str] = []

        def reconcile(self, *, live_run_id: str) -> dict:
            return {
                "open_positions": [
                    {
                        "symbol": "BTC/USDT:USDT",
                        "contracts": 1.0,
                        "side": "long",
                        "entry_price": 100.0,
                        "mark_price": 100.0,
                    }
                ],
                "open_orders": [
                    {"symbol": "BTCUSDT", "orderType": "STOP_MARKET", "reduceOnly": True},
                    {"symbol": "BTCUSDT", "orderType": "TAKE_PROFIT_MARKET", "reduceOnly": True},
                ],
            }

        def submit_order(self, *, live_run_id: str, order_request: ExecutionOrderRequest) -> dict:
            assert order_request.entry_context["close_only_mode"] is True
            return {
                "gateway_order_id": "close-1",
                "client_order_id": "close-client-1",
                "trade_ids": ["close-trade-1"],
                "commissions": [],
                "gateway_status": "filled",
                "quantity": 1.0,
                "filled_quantity": 1.0,
                "average_fill_price": 94.5,
                "fill_timestamp": "2026-07-24T00:00:00+00:00",
                "fill_source": "create_order",
                "protection_order_refs": [],
            }

        def cancel_protection_order(self, *, symbol: str, gateway_order_id: str) -> None:
            assert symbol == "BTC/USDT"
            self.cancelled.append(gateway_order_id)

    monkeypatch.setattr(settings, "binance_auto_execute", True)
    gateway = ProtectedPositionGateway()
    runtime, paper_run = _runtime_with_position(
        db_session,
        side=TradeSide.LONG,
        stop_price=95.0,
        take_price=120.0,
        mirror_to_gateway=True,
        gateway=gateway,
    )
    entry = ExecutionRepository(db_session).find_latest_filled_entry_order(
        run_type="paper",
        run_id=paper_run.paper_run_id or "",
        symbol="BTC/USDT",
    )
    assert entry is not None
    ExecutionRepository(db_session).update_order(
        entry.order_execution_id or "",
        entry_context={
            **entry.entry_context,
            "protection_order_refs": [
                {"algoId": "stop-1", "orderType": "STOP_MARKET"},
                {"algoId": "take-1", "orderType": "TAKE_PROFIT_MARKET"},
            ],
        },
    )
    _store_bar(db_session, low=94, high=100, close=96, timeframe="1m")

    result = runtime.run_cycle(
        paper_run_id=paper_run.paper_run_id or "",
        request=PaperRuntimeCycleRequest(symbols=["BTC/USDT"], timeframe="15m", enable_decision_veto=False),
    )

    assert result.closed_positions == 1
    assert result.actions[0].reference_price == 94.5
    refreshed = PaperRunRepository(db_session).get_paper_run(paper_run.paper_run_id or "")
    assert refreshed is not None
    assert refreshed.paper_metrics_summary["gross_realized_pnl_total"] == -5.5
    assert gateway.cancelled == ["stop-1", "take-1"]


def test_gateway_close_request_preserves_position_direction_for_gateway_side_mapping() -> None:
    request = ExecutionOrderRequest(
        strategy_id="strategy-1",
        symbol="BTC/USDT",
        direction=TradeSide.LONG,
        entry_context={"close_only_mode": True, "quantity": 1.0, "reference_price": 100.0},
    )
    long_position = PositionSnapshot(
        run_type="paper",
        run_id="run-1",
        symbol="BTC/USDT",
        side=TradeSide.LONG,
        quantity=1.0,
        entry_price=100.0,
        mark_price=100.0,
        snapshot_time=datetime.now(UTC),
    )
    short_position = long_position.model_copy(update={"side": TradeSide.SHORT, "quantity": -1.0})

    long_close = PaperCycleOrchestrator._gateway_order_request(order_request=request, position=long_position)
    short_close = PaperCycleOrchestrator._gateway_order_request(
        order_request=request.model_copy(update={"direction": TradeSide.SHORT}),
        position=short_position,
    )

    assert long_close.direction == TradeSide.LONG
    assert short_close.direction == TradeSide.SHORT


def test_runtime_exchange_execution_preserves_close_position_direction(db_session) -> None:
    runtime, _ = _runtime_with_position(
        db_session,
        side=TradeSide.LONG,
        stop_price=95.0,
        take_price=120.0,
    )
    position = PositionSnapshot(
        run_type="paper",
        run_id="run-1",
        symbol="BTC/USDT",
        side=TradeSide.LONG,
        quantity=1.0,
        entry_price=100.0,
        mark_price=100.0,
        snapshot_time=datetime.now(UTC),
    )
    request = ExecutionOrderRequest(
        strategy_id="strategy-1",
        symbol="BTC/USDT",
        direction=TradeSide.LONG,
        entry_context={"close_only_mode": True, "quantity": 1.0, "reference_price": 100.0},
    )

    gateway_request = runtime.exchange_execution.gateway_order_request(
        order_request=request,
        position=position,
    )

    assert gateway_request.direction == TradeSide.LONG


def test_runtime_exchange_execution_exposes_pending_limit_expiry() -> None:
    from services.execution.paper_exchange_execution import PaperExchangeExecutionService

    assert callable(PaperExchangeExecutionService.expire_pending_limit_entries)


def test_runtime_exposes_cycle_orchestrator(db_session) -> None:
    runtime, _ = _runtime_with_position(
        db_session,
        side=TradeSide.LONG,
        stop_price=95.0,
        take_price=120.0,
    )

    assert callable(runtime.cycle_orchestrator.run_cycle)


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
                "execution_mode": ("binance_testnet" if mirror_to_gateway else "local_paper"),
            },
            paper_status="running",
        )
    )
    execution_repo = ExecutionRepository(db_session)
    entry_order = execution_repo.create_order(
        OrderExecution(
            strategy_id=strategy.strategy_id,
            symbol="BTC/USDT",
            direction=side,
            execution_status="filled",
            gateway_order_id="paper-entry-1",
            order_origin="paper_scheduler",
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
    opened_at = datetime.now(UTC) - timedelta(minutes=5) - timedelta(hours=position_age_hours)
    position_record = execution_repo.create_position_record(
        PositionRecord(
            exchange_account="paper:paper:local",
            symbol="BTC/USDT",
            position_side=side,
            entry_order_id=entry_order.order_execution_id,
            entry_fill_id=entry_order.gateway_order_id,
            opened_at=opened_at,
            quantity=1.0,
            order_origin="paper_scheduler",
            strategy_id=strategy.strategy_id,
            run_id=paper_run.paper_run_id,
            management_status=(
                PositionManagementStatus.MANAGED_STRATEGY
                if mirror_to_gateway
                else PositionManagementStatus.PAPER_SIMULATION_ONLY
            ),
        )
    )
    execution_repo.create_protection_record(
        ProtectionRecord(
            position_record_id=position_record.position_record_id or "",
            stop_price=stop_price,
            take_profit_price=take_price,
            protection_source="strategy_entry",
        )
    )
    execution_repo.update_order(
        entry_order.order_execution_id or "",
        position_record_id=position_record.position_record_id,
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
            snapshot_time=opened_at,
            position_record_id=position_record.position_record_id,
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
    runtime.exchange_execution.register_session_managed_position(position_record.position_record_id)
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
    duration = {
        "1m": timedelta(minutes=1),
        "15m": timedelta(minutes=15),
        "1h": timedelta(hours=1),
        "4h": timedelta(hours=4),
    }[timeframe]
    now = datetime.now(UTC).replace(microsecond=0) - duration + timedelta(hours=offset_hours)
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


def test_authoritative_fill_price_prefers_confirmed_exchange_fill() -> None:
    import services.execution.paper_cycle_orchestrator as orchestrator_module

    resolver = getattr(orchestrator_module, "_authoritative_fill_price", None)
    assert resolver is not None
    order = OrderExecution(
        strategy_id="strategy-1",
        symbol="BTC/USDT",
        direction=TradeSide.LONG,
        entry_context={
            "exchange_fill_confirmed": True,
            "exchange_average_fill_price": 101.25,
        },
    )

    assert resolver(order=order, fallback_price=100.0) == 101.25


def test_exchange_first_partial_takeprofit_submits_before_local_projection(db_session, monkeypatch) -> None:
    from shared.config import settings

    class PartialFillGateway:
        capability = type("Cap", (), {"gateway_name": "partial_fill_gateway"})()

        def __init__(self) -> None:
            self.submitted: list[ExecutionOrderRequest] = []

        def reconcile(self, *, live_run_id: str) -> dict:
            return {
                "open_positions": [
                    {
                        "symbol": "BTC/USDT:USDT",
                        "contracts": 1.0,
                        "side": "long",
                        "entry_price": 100.0,
                        "mark_price": 110.0,
                    }
                ],
                "open_orders": [
                    {"symbol": "BTCUSDT", "orderType": "STOP_MARKET", "reduceOnly": True},
                    {"symbol": "BTCUSDT", "orderType": "TAKE_PROFIT_MARKET", "reduceOnly": True},
                ],
            }

        def submit_order(self, *, live_run_id: str, order_request: ExecutionOrderRequest) -> dict:
            self.submitted.append(order_request)
            return {
                "gateway_order_id": "partial-close-1",
                "client_order_id": "partial-close-client-1",
                "trade_ids": ["partial-close-trade-1"],
                "commissions": [],
                "gateway_status": "filled",
                "quantity": 0.5,
                "filled_quantity": 0.5,
                "average_fill_price": 110.5,
                "fill_timestamp": "2026-07-24T00:00:00+00:00",
                "fill_source": "create_order",
                "protection_order_refs": [],
            }

        def cancel_protection_order(self, *, symbol: str, gateway_order_id: str) -> None:
            return None

        def refresh_protection_orders(self, *, order_request, quantity, previous_refs):  # noqa: ANN001
            assert quantity == 0.5
            assert order_request.stoploss_plan["price"] == 100.0
            return [{"algoId": "remaining-stop", "orderType": "STOP_MARKET"}]

    monkeypatch.setattr(settings, "binance_auto_execute", True)
    gateway = PartialFillGateway()
    runtime, paper_run = _runtime_with_position(
        db_session,
        side=TradeSide.LONG,
        stop_price=95.0,
        take_price=110.0,
        takeprofit_rules={"risk_reward": 2.0, "partial_close_fraction": 0.5},
        mirror_to_gateway=True,
        gateway=gateway,
    )
    _store_bar(db_session, low=106, high=111, close=110, timeframe="1m")

    result = runtime.run_cycle(
        paper_run_id=paper_run.paper_run_id or "",
        request=PaperRuntimeCycleRequest(symbols=["BTC/USDT"], timeframe="15m", enable_decision_veto=False),
    )

    assert len(gateway.submitted) == 1
    assert gateway.submitted[0].entry_context["close_only_mode"] is True
    assert result.actions[0].action == "partial_takeprofit_long"
    position = ExecutionRepository(db_session).list_latest_positions_for_run(
        run_type="paper", run_id=paper_run.paper_run_id or ""
    )[0]
    assert position.quantity == 0.5
    refreshed = PaperRunRepository(db_session).get_paper_run(paper_run.paper_run_id or "")
    assert refreshed is not None
    assert refreshed.paper_metrics_summary["gross_realized_pnl_total"] == 5.25


def test_directional_sampling_fallback_reaches_exchange_fill_and_local_projection(db_session, monkeypatch) -> None:
    """A starved primary directional decision must reach the Testnet gateway
    through the bounded fallback and project only the confirmed exchange fill.
    """
    from shared.config import settings

    class FilledDirectionalGateway:
        capability = type("Cap", (), {"gateway_name": "binance_usdt_perpetual"})()

        def __init__(self) -> None:
            self.submitted: list[ExecutionOrderRequest] = []

        def account_equity(self) -> float:
            return 10_000.0

        def sync_account(self, *, live_run_id: str) -> ExchangeAccountSnapshot:
            del live_run_id
            return ExchangeAccountSnapshot(
                exchange="binance",
                wallet_balance=10_000.0,
                margin_balance=10_000.0,
                available_balance=10_000.0,
                snapshot_time=datetime.now(UTC),
            )

        def reconcile(self, *, live_run_id: str) -> dict:
            del live_run_id
            return {"open_positions": [], "open_orders": []}

        def load_market_rules_snapshot(self, *, symbol, leverage, loaded_at):  # noqa: ANN001
            return MarketRulesSnapshot(
                rules_snapshot_id=f"rules:{symbol}",
                symbol=symbol,
                market_status="TRADING",
                position_mode="ONE_WAY",
                margin_mode="CROSS",
                leverage=leverage,
                tick_size=Decimal("0.1"),
                step_size=Decimal("0.001"),
                min_quantity=Decimal("0.001"),
                min_notional=Decimal("5"),
                loaded_at=loaded_at,
                exchange="binance",
                market_type="swap",
                exchange_symbol=f"{symbol}:USDT",
                price_precision=1,
                amount_precision=3,
                contract_size=Decimal("1"),
                market_active=True,
            )

        def submit_order(self, *, live_run_id: str, order_request: ExecutionOrderRequest) -> dict:
            del live_run_id
            self.submitted.append(order_request)
            assert order_request.trade_intent is not None
            assert order_request.market_rules_snapshot is not None
            assert order_request.entry_context["testnet_sampling_mode"] is True
            quantity = float(order_request.trade_intent.target_quantity)
            return {
                "gateway_order_id": "directional-natural-1",
                "client_order_id": "directional-natural-client-1",
                "trade_ids": ["directional-natural-trade-1"],
                "commissions": [],
                "gateway_status": "filled",
                "quantity": quantity,
                "filled_quantity": quantity,
                "average_fill_price": 100.5,
                "fill_timestamp": "2026-07-24T00:00:01+00:00",
                "fill_source": "create_order",
                "protection_order_refs": [
                    {"algoId": "directional-stop-1", "orderType": "STOP_MARKET"},
                    {"algoId": "directional-tp-1", "orderType": "TAKE_PROFIT_MARKET"},
                ],
            }

        def pretrade_market_snapshot(
            self,
            *,
            order_request: ExecutionOrderRequest,
        ) -> PretradeMarketSnapshot:
            del order_request
            now = datetime.now(UTC)
            return PretradeMarketSnapshot(
                server_time=now,
                bid=Decimal("100.4"),
                ask=Decimal("100.5"),
                mark_price=Decimal("100.1"),
                decision_bar_close_time=now - timedelta(seconds=10),
                decision_age_seconds=10,
                atr=Decimal("1"),
                tick_size=Decimal("0.1"),
                step_size=Decimal("0.001"),
            )

    monkeypatch.setattr(settings, "binance_auto_execute", True)
    monkeypatch.setattr(settings, "binance_use_testnet", True)
    monkeypatch.setattr(settings, "live_trading_enabled", False)
    gateway = FilledDirectionalGateway()
    runtime, paper_run = _runtime_without_position(db_session, gateway=gateway, mirror_to_gateway=True)
    armed_profile = {
        **paper_run.execution_profile,
        "strategy_lane": "directional",
        "execution_mode": "binance_testnet",
        "mirror_to_gateway": True,
        "cost_gate_verified": True,
        "simulation_sampling_fallback_enabled": True,
        "acceptance_symbols": ["BTC/USDT", "ETH/USDT"],
        "acceptance_scope_hash": execution_scope_hash(),
    }
    paper_run = PaperRunRepository(db_session).update_paper_run(
        paper_run.paper_run_id or "",
        execution_profile=armed_profile,
    )
    assert paper_run is not None
    strategy = StrategyRepository(db_session).get_strategy(paper_run.strategy_id)
    assert strategy is not None
    ConfigSnapshotRepository(db_session).create_snapshot(
        ConfigSnapshot.create(
            paper_run_id=paper_run.paper_run_id or "",
            config={
                "execution_profile": armed_profile,
                "strategy_rules": strategy.rules.model_dump(mode="json"),
            },
            created_by="exchange-first-e2e-test",
            effective_cycle_id="seed",
        ),
        base_config_hash=None,
    )
    _store_bar(db_session, low=99, high=101, close=100, timeframe="15m")
    latest = DataRepository(db_session).get_latest_ohlcv_bar(symbol="BTC/USDT", timeframe="15m")
    assert latest is not None
    calls = 0

    def _primary(*, strategy, symbol, timeframe, **_kwargs) -> DecisionPipelineResult:
        nonlocal calls
        del symbol, timeframe
        calls += 1
        candidate_id = strategy.rules.entry_rules.get("candidate_id")
        return DecisionPipelineResult(
            direction=None,
            should_trade=False,
            reason="multi_timeframe_disagreement",
            reference_price=Decimal("100"),
            bar_time=latest.timestamp,
            signals=[],
            ensemble=None,
            meta_label=None,
            veto_result=None,
            confidence_multiplier=1.0,
            atr=1.0,
            volatility_context={},
            trace={
                "pipeline_status": "multi_timeframe_disagreement",
                "strategy_lane": "directional",
                "candidate_id": candidate_id,
            },
        )

    def _sampling(*, symbol, timeframe, primary, decision_time=None) -> DecisionPipelineResult:  # noqa: ANN001
        del symbol, timeframe, primary, decision_time
        return DecisionPipelineResult(
            direction=TradeSide.LONG,
            should_trade=True,
            reason="testnet_sampling_lane_signal",
            reference_price=Decimal("100"),
            bar_time=latest.timestamp,
            signals=[],
            ensemble=None,
            meta_label=None,
            veto_result=None,
            confidence_multiplier=1.0,
            atr=1.0,
            volatility_context={"sampling": True},
            trace={
                "pipeline_status": "testnet_sampling_signal",
                "testnet_sampling_mode": True,
                "evidence_class": "NON_PROMOTABLE_PIPELINE_SAMPLE",
            },
        )

    monkeypatch.setattr(runtime.signal_generator.decision_pipeline, "evaluate", _primary)
    monkeypatch.setattr(runtime.signal_generator, "_sampling_lane_decision", _sampling)

    result = runtime.run_cycle(
        paper_run_id=paper_run.paper_run_id or "",
        request=PaperRuntimeCycleRequest(symbols=["BTC/USDT"], timeframe="15m", enable_decision_veto=True),
    )

    assert calls == 1
    assert len(gateway.submitted) == 1
    assert result.opened_positions == 1
    assert result.rejected_orders == 0
    assert result.actions[0].action == "open_long"
    orders = ExecutionRepository(db_session).list_orders()
    assert len(orders) == 1
    assert orders[0].gateway_order_id == "directional-natural-1"
    assert orders[0].gateway_status == "filled"
    assert orders[0].entry_context["exchange_fill_confirmed"] is True
    positions = ExecutionRepository(db_session).list_latest_positions_for_run(
        run_type="paper", run_id=paper_run.paper_run_id or ""
    )
    assert len(positions) == 1
    assert positions[0].entry_price == 100.5
    assert positions[0].quantity > 0
