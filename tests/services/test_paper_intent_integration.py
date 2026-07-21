from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from services.data import DataRepository
from services.execution.gatekeeper import ExecutionGatekeeperService
from services.execution.paper_cycle_orchestrator import PaperCycleOrchestrator
from services.strategy_library import (
    ConfigSnapshotRepository,
    ExecutionRepository,
    HypothesisRepository,
    PaperRunRepository,
    ReviewRepository,
    RiskProfileRepository,
    StrategyRepository,
    ValidationRepository,
)
from shared.models import (
    BacktestRun,
    ConfigSnapshot,
    ExecutionOrderRequest,
    ExecutionRiskState,
    GateDecision,
    PaperRun,
    StrategyCreate,
    StrategyRules,
    TradeSide,
)


def _entry_request() -> ExecutionOrderRequest:
    return ExecutionOrderRequest(
        strategy_id="paper-intent-strategy",
        version_id="v2026.07",
        symbol="BTC/USDT",
        direction=TradeSide.LONG,
        entry_context={"requested_notional": 1250, "timeframe": "15m"},
        stoploss_plan={"price": 61_500},
        takeprofit_plan={"price": 64_500},
    )


def _active_snapshot() -> ConfigSnapshot:
    return ConfigSnapshot.create(
        paper_run_id="paper-run-1",
        config={"execution_profile": {"execution_mode": "paper_only"}},
        created_by="test",
        effective_cycle_id="seed",
    ).model_copy(update={"config_snapshot_id": "config-snapshot-1"})


def test_automatic_open_entry_carries_immutable_intent_from_active_snapshot() -> None:
    request = _entry_request()
    candle_close_time = datetime(2026, 7, 21, 9, 0, tzinfo=UTC)

    attached = PaperCycleOrchestrator._with_open_trade_intent(
        base_order=request,
        cycle_id="paper-run-1:BTC/USDT:15m:2026-07-21T09:00:00+00:00",
        active_config=_active_snapshot(),
        reference_price=Decimal("62500"),
        decision_candle_close_time=candle_close_time,
    )

    assert attached.trade_intent is not None
    assert attached.trade_intent.cycle_id == "paper-run-1:BTC/USDT:15m:2026-07-21T09:00:00+00:00"
    assert attached.trade_intent.decision_id == attached.trade_intent.cycle_id
    assert attached.trade_intent.strategy_id == request.strategy_id
    assert attached.trade_intent.strategy_version == request.version_id
    assert attached.trade_intent.symbol == request.symbol
    assert attached.trade_intent.target_quantity == Decimal("0.02")
    assert attached.trade_intent.signal_reference_price == Decimal("62500")
    assert attached.trade_intent.protection.stop_price == Decimal("61500")
    assert attached.trade_intent.protection.take_profit_price == Decimal("64500")
    assert attached.trade_intent.signal_candle_close_time == candle_close_time
    assert attached.trade_intent.config_snapshot_id == "config-snapshot-1"
    assert attached.trade_intent.config_hash == _active_snapshot().config_hash
    assert attached.trade_intent.action.value == "OPEN"


def test_no_active_snapshot_leaves_paper_entry_without_fake_intent() -> None:
    request = _entry_request()

    attached = PaperCycleOrchestrator._with_open_trade_intent(
        base_order=request,
        cycle_id="paper-run-1:BTC/USDT:15m:2026-07-21T09:00:00+00:00",
        active_config=None,
        reference_price=Decimal("62500"),
        decision_candle_close_time=datetime(2026, 7, 21, 9, 0, tzinfo=UTC),
    )

    assert attached.trade_intent is None


def test_gatekeeper_persists_open_intent_and_config_identity_fields(db_session) -> None:
    strategy = StrategyRepository(db_session).create_strategy(
        StrategyCreate(
            strategy_key="paper_intent_gatekeeper",
            source="test",
            core_thesis="persist immutable paper intent identity",
            rules=StrategyRules(
                stoploss_rules={"basis_bps": 20},
                takeprofit_rules={"basis_bps": 40},
                position_rules={"risk_per_trade": 0.01, "max_leverage": 2},
            ),
        )
    )
    backtest = ValidationRepository(db_session).create_backtest_run(
        BacktestRun(
            strategy_id=strategy.strategy_id,
            execution_engine="test",
            eligibility_result=GateDecision(
                strategy_id=strategy.strategy_id,
                passed=True,
                decision_status="accepted",
                reason="test accepted",
            ),
        )
    )
    now = datetime.now(UTC).replace(microsecond=0)
    DataRepository(db_session).store_ohlcv_bars(
        [
            {
                "symbol": "BTC/USDT",
                "exchange": "binance",
                "timeframe": "15m",
                "time": now - timedelta(minutes=16),
                "open": Decimal("62500"),
                "high": Decimal("62600"),
                "low": Decimal("62400"),
                "close": Decimal("62500"),
                "volume": Decimal("12"),
            }
        ]
    )
    paper_run = PaperRunRepository(db_session).create_paper_run(
        PaperRun(
            paper_run_id="paper-run-1",
            strategy_id=strategy.strategy_id,
            gate_decision_ref=backtest.backtest_run_id,
            candidate_symbols=["BTC/USDT"],
            paper_status="running",
        )
    )
    snapshot = ConfigSnapshotRepository(db_session).create_snapshot(
        _active_snapshot().model_copy(update={"paper_run_id": paper_run.paper_run_id or ""}),
        base_config_hash=None,
    )
    request = PaperCycleOrchestrator._with_open_trade_intent(
        base_order=_entry_request().model_copy(
            update={
                "strategy_id": strategy.strategy_id,
                "validation_backtest_run_id": backtest.backtest_run_id,
                "risk_state": ExecutionRiskState(account_equity=10_000, equity_peak=10_000, requested_notional=1250),
                "paper_run_id": paper_run.paper_run_id,
            }
        ),
        cycle_id=f"{paper_run.paper_run_id}:BTC/USDT:15m:2026-07-21T09:00:00+00:00",
        active_config=snapshot,
        reference_price=Decimal("62500"),
        decision_candle_close_time=now - timedelta(minutes=15),
    )
    gatekeeper = ExecutionGatekeeperService(
        data_repo=DataRepository(db_session),
        validation_repo=ValidationRepository(db_session),
        hypothesis_repo=HypothesisRepository(db_session),
        risk_profile_repo=RiskProfileRepository(db_session),
        execution_repo=ExecutionRepository(db_session),
        paper_repo=PaperRunRepository(db_session),
        review_repo=ReviewRepository(db_session),
    )

    created = gatekeeper.submit_order(request)
    persisted = ExecutionRepository(db_session).get_order(created.order_execution_id or "")

    assert request.trade_intent is not None
    assert persisted is not None
    assert persisted.intent_id == request.trade_intent.intent_id
    assert persisted.cycle_id == request.trade_intent.cycle_id
    assert persisted.decision_id == request.trade_intent.decision_id
    assert persisted.config_snapshot_id == request.trade_intent.config_snapshot_id
    assert persisted.config_hash == request.trade_intent.config_hash
