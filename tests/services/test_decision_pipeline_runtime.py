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
from shared.models import MarketExtras, PaperRun, PaperRunStepRequest, RiskEventType, StrategyContract, StrategyRules


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


def test_decision_pipeline_does_not_fallback_trade_without_signals(db_session) -> None:
    data_repo = DataRepository(db_session)
    start_at = datetime.now(UTC).replace(microsecond=0) - timedelta(hours=59)
    flat_bars = []
    for index in range(60):
        flat_bars.append(
            {
                "symbol": "BTC/USDT",
                "exchange": "binance",
                "timeframe": "1h",
                "time": start_at + timedelta(hours=index),
                "open": Decimal("100"),
                "high": Decimal("100.1"),
                "low": Decimal("99.9"),
                "close": Decimal("100"),
                "volume": Decimal("100"),
            }
        )
    data_repo.store_ohlcv_bars(flat_bars)
    data_repo.store_market_extras(
        [
            MarketExtras(
                symbol="BTC/USDT:USDT",
                time=start_at + timedelta(hours=59),
                funding_rate=Decimal("-0.0010"),
                long_ratio=Decimal("0.35"),
                short_ratio=Decimal("0.65"),
            )
        ]
    )
    strategy = StrategyContract(
        strategy_id="strategy-no-signal",
        strategy_key="no_signal_strategy",
        source="manual",
        core_thesis="No deterministic signal should mean no trade, not fallback direction.",
        rules=StrategyRules(entry_rules={"enabled_signals": ["rsi"], "meta_label_min_win_rate": 0.4}),
    )
    generator = PaperSignalGenerator(data_repo=data_repo)

    order = generator.generate_order(
        paper_run=PaperRun(
            paper_run_id="paper-no-signal",
            strategy_id=strategy.strategy_id,
            gate_decision_ref="backtest-1",
            execution_profile={"account_equity": 10_000, "equity_peak": 10_000},
        ),
        strategy=strategy,
        request=PaperRunStepRequest(symbol="BTC/USDT", timeframe="1h", enable_decision_veto=False),
        positions=[],
    )

    trace = order.entry_context["decision_pipeline"]
    assert trace["pipeline_status"] == "technical_signals_insufficient"
    assert "market_intelligence" not in trace["volatility"]
    assert order.entry_context["paper_order_should_trade"] is False
    assert order.veto_result is not None
    assert "technical_signals_insufficient" in (order.veto_result.veto_reason or "")


def test_market_intelligence_joins_ensemble_only_after_technical_signal(db_session) -> None:
    data_repo = DataRepository(db_session)
    start_at = datetime.now(UTC).replace(microsecond=0) - timedelta(hours=59)
    data_repo.store_ohlcv_bars(_bars(start_at))
    data_repo.store_market_extras(
        [
            MarketExtras(
                symbol="BTC/USDT:USDT",
                time=start_at + timedelta(hours=59),
                funding_rate=Decimal("-0.0005"),
                long_ratio=Decimal("0.40"),
                short_ratio=Decimal("0.60"),
            )
        ]
    )
    strategy = _strategy()
    generator = PaperSignalGenerator(
        data_repo=data_repo,
        execution_repo=ExecutionRepository(db_session),
        agent_repo=AgentTaskRepository(db_session),
        strategy_repo=StrategyRepository(db_session),
    )

    order = generator.generate_order(
        paper_run=PaperRun(
            paper_run_id="paper-intelligence",
            strategy_id=strategy.strategy_id,
            gate_decision_ref="backtest-1",
            execution_profile={"account_equity": 10_000, "equity_peak": 10_000},
        ),
        strategy=strategy,
        request=PaperRunStepRequest(symbol="BTC/USDT", timeframe="1h", enable_decision_veto=False),
        positions=[],
    )

    trace = order.entry_context["decision_pipeline"]
    sources = {signal["source"] for signal in trace["signals"]}
    intelligence = trace["volatility"]["market_intelligence"]
    raw_vote_weights = {
        vote["strategy_id"]: vote["weight"]
        for vote in trace["ensemble"]["raw_votes"]
        if vote["strategy_id"].startswith("market_intelligence:")
    }

    assert "market_intelligence" in sources
    assert intelligence["vote_weight"] <= 0.30
    assert intelligence["should_participate"] is True
    assert raw_vote_weights
    assert max(raw_vote_weights.values()) <= 0.30


def test_decision_pipeline_respects_enabled_signal_list(db_session) -> None:
    data_repo = DataRepository(db_session)
    start_at = datetime.now(UTC).replace(microsecond=0) - timedelta(hours=59)
    rows = []
    for index in range(60):
        price = Decimal("100") + Decimal(index) * Decimal("0.5")
        rows.append(
            {
                "symbol": "BTC/USDT",
                "exchange": "binance",
                "timeframe": "1h",
                "time": start_at + timedelta(hours=index),
                "open": price,
                "high": price + Decimal("0.6"),
                "low": price - Decimal("0.6"),
                "close": price,
                "volume": Decimal("100"),
            }
        )
    data_repo.store_ohlcv_bars(rows)
    strategy = StrategyContract(
        strategy_id="strategy-ema-only",
        strategy_key="ema_only_strategy",
        source="manual",
        core_thesis="EMA-only trend strategy should not emit unrelated signal families.",
        rules=StrategyRules(entry_rules={"enabled_signals": ["ema_trend"], "meta_label_min_win_rate": 0.4}),
    )
    generator = PaperSignalGenerator(data_repo=data_repo)

    order = generator.generate_order(
        paper_run=PaperRun(
            paper_run_id="paper-ema-only",
            strategy_id=strategy.strategy_id,
            gate_decision_ref="backtest-1",
            execution_profile={"account_equity": 10_000, "equity_peak": 10_000},
        ),
        strategy=strategy,
        request=PaperRunStepRequest(symbol="BTC/USDT", timeframe="1h", enable_decision_veto=False),
        positions=[],
    )

    trace = order.entry_context["decision_pipeline"]
    sources = {signal["source"] for signal in trace["signals"]}
    assert sources == {"technical_ema_trend"}
    assert trace["volatility"]["enabled_signals"] == ["ema_trend"]
