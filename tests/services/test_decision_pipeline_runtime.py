from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from services.data import DataRepository, MarketDataHeartbeatService
from services.execution.paper_signal import PaperSignalGenerator
from services.strategy_library import (
    AgentTaskRepository,
    ExecutionRepository,
    NotificationRepository,
    StrategyRepository,
)
from shared.models import PaperRun, PaperRunStepRequest, RiskEventType, StrategyContract, StrategyRules


def _bars(start_at: datetime, count: int = 60) -> list[dict]:
    rows = []
    price = Decimal("100")
    for index in range(count):
        price += Decimal("1")
        rows.append(
            {
                "symbol": "BTC/USDT",
                "exchange": "binance",
                "timeframe": "1h",
                "time": start_at + timedelta(hours=index),
                "open": price - Decimal("0.5"),
                "high": price + Decimal("0.5"),
                "low": price - Decimal("1.0"),
                "close": price,
                "volume": Decimal("100"),
            }
        )
    return rows


def _strategy() -> StrategyContract:
    return StrategyContract(
        strategy_id="strategy-1",
        strategy_key="trend_strategy",
        source="manual",
        core_thesis="trend breakout should be fused before paper execution",
        rules=StrategyRules(
            entry_rules={"meta_label_min_win_rate": 0.4},
            exit_rules={"max_hold_bars": 24},
            stoploss_rules={"atr_multiple": 2.0},
            takeprofit_rules={"risk_reward": 2.0},
            position_rules={"risk_per_trade": 0.01, "max_leverage": 1},
        ),
    )


def test_paper_signal_uses_decision_pipeline_and_atr_stop(db_session) -> None:
    data_repo = DataRepository(db_session)
    data_repo.store_ohlcv_bars(_bars(datetime.now(UTC).replace(microsecond=0) - timedelta(hours=59)))
    strategy = _strategy()
    generator = PaperSignalGenerator(
        data_repo=data_repo,
        execution_repo=ExecutionRepository(db_session),
        agent_repo=AgentTaskRepository(db_session),
        strategy_repo=StrategyRepository(db_session),
    )

    order = generator.generate_order(
        paper_run=PaperRun(
            paper_run_id="paper-1",
            strategy_id=strategy.strategy_id,
            gate_decision_ref="backtest-1",
            execution_profile={"account_equity": 10_000, "equity_peak": 10_000},
        ),
        strategy=strategy,
        request=PaperRunStepRequest(symbol="BTC/USDT", timeframe="1h", enable_decision_veto=False),
        positions=[],
    )

    trace = order.entry_context["decision_pipeline"]
    assert trace["pipeline_status"] == "bet_taken"
    assert order.signal_ensemble_id is not None
    assert order.meta_label_id is not None
    assert order.entry_context["paper_order_should_trade"] is True
    assert order.stoploss_plan["price"] != float(Decimal(order.entry_context["reference_price"]) * Decimal("0.98"))
    assert order.entry_context["requested_notional"] > 0


def test_market_data_heartbeat_writes_data_stale_risk_event(db_session) -> None:
    result = MarketDataHeartbeatService(data_repo=DataRepository(db_session)).check_symbol(
        symbol="BTC/USDT",
        timeframe="1m",
        max_delay_seconds=120,
    )

    assert result["is_fresh"] is False
    events = DataRepository(db_session).list_risk_events(active_only=True)
    assert events[0].event_type == RiskEventType.DATA_STALE


def test_decision_veto_budget_exceeded_fails_closed(db_session, monkeypatch) -> None:
    monkeypatch.setattr("services.execution.decision_pipeline.settings.decision_veto_daily_budget", 0)
    data_repo = DataRepository(db_session)
    data_repo.store_ohlcv_bars(_bars(datetime.now(UTC).replace(microsecond=0) - timedelta(hours=59)))
    strategy = _strategy()
    generator = PaperSignalGenerator(
        data_repo=data_repo,
        execution_repo=ExecutionRepository(db_session),
        agent_repo=AgentTaskRepository(db_session),
        strategy_repo=StrategyRepository(db_session),
        notification_repo=NotificationRepository(db_session),
    )

    order = generator.generate_order(
        paper_run=PaperRun(
            paper_run_id="paper-1",
            strategy_id=strategy.strategy_id,
            gate_decision_ref="backtest-1",
            execution_profile={"account_equity": 10_000, "equity_peak": 10_000},
        ),
        strategy=strategy,
        request=PaperRunStepRequest(symbol="BTC/USDT", timeframe="1h", enable_decision_veto=True),
        positions=[],
    )

    assert order.veto_result is not None
    assert order.veto_result.veto is True
    assert "budget exceeded" in (order.veto_result.veto_reason or "")
    assert NotificationRepository(db_session).get_notification("llm_budget:" + datetime.now(UTC).date().isoformat())
