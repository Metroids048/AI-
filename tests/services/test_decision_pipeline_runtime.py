from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from services.data import DataRepository, MarketDataHeartbeatService
from services.execution.net_edge import net_edge_rejection_codes
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
    assert trace["strategy_lane"] == "directional"
    assert order.signal_ensemble_id is not None
    assert order.meta_label_id is not None
    assert order.entry_context["paper_order_should_trade"] is True
    assert order.stoploss_plan["price"] != float(Decimal(order.entry_context["reference_price"]) * Decimal("0.98"))
    assert order.entry_context["requested_notional"] > 0
    assert order.entry_context["order_type"] == "limit"
    assert order.entry_context["limit_price"] > float(order.entry_context["reference_price"])


def test_signal_observation_orders_relax_sampling_filters_but_remain_observational(db_session, monkeypatch) -> None:
    data_repo = DataRepository(db_session)
    data_repo.store_ohlcv_bars(_bars(datetime.now(UTC).replace(microsecond=0) - timedelta(hours=59)))
    strategy = _strategy()
    generator = PaperSignalGenerator(
        data_repo=data_repo,
        execution_repo=ExecutionRepository(db_session),
        agent_repo=AgentTaskRepository(db_session),
        strategy_repo=StrategyRepository(db_session),
    )
    observed: dict[str, bool] = {}
    evaluate = generator.decision_pipeline.evaluate

    def capture_relaxed_signals(**kwargs):
        observed["relaxed_signals"] = kwargs["relaxed_signals"]
        return evaluate(**kwargs)

    monkeypatch.setattr(generator.decision_pipeline, "evaluate", capture_relaxed_signals)

    order = generator.generate_order(
        paper_run=PaperRun(
            paper_run_id="observation-paper-1",
            strategy_id=strategy.strategy_id,
            gate_decision_ref="backtest-1",
            execution_profile={
                "strategy_lane": "signal_observation",
                "account_equity": 10_000,
                "equity_peak": 10_000,
            },
        ),
        strategy=strategy,
        request=PaperRunStepRequest(symbol="BTC/USDT", timeframe="1h", enable_decision_veto=False),
        positions=[],
    )

    assert order.entry_context["decision_pipeline"]["strategy_lane"] == "signal_observation"
    assert observed["relaxed_signals"] is True
    assert order.entry_context["observation_only_mode"] is True
    assert order.entry_context["strategy_performance_eligible"] is False
    assert order.entry_context["edge_stats_source"] == "raw_bar_proxy_non_authoritative"
    assert net_edge_rejection_codes(order.entry_context) == []


def test_auto_directional_order_requires_validated_symbol_evidence(db_session) -> None:
    data_repo = DataRepository(db_session)
    data_repo.store_ohlcv_bars(_bars(datetime.now(UTC).replace(microsecond=0) - timedelta(hours=59)))
    strategy = _strategy().model_copy(update={"strategy_key": "auto_paper_mature_templates"})
    generator = PaperSignalGenerator(data_repo=data_repo)

    order = generator.generate_order(
        paper_run=PaperRun(
            paper_run_id="auto-evidence-paper",
            strategy_id=strategy.strategy_id,
            gate_decision_ref="backtest-1",
            execution_profile={
                "strategy_lane": "directional",
                "account_equity": 10_000,
                "equity_peak": 10_000,
            },
        ),
        strategy=strategy,
        request=PaperRunStepRequest(symbol="BTC/USDT", timeframe="1h", enable_decision_veto=False),
        positions=[],
    )

    assert order.entry_context["validated_edge_required"] is True
    assert order.entry_context["validated_edge_net_expectancy"] is None
    assert order.entry_context["edge_stats_source"] == "validated_edge_stats_missing_or_stale"
    assert net_edge_rejection_codes(order.entry_context) == ["validated_edge_stats_missing_or_stale"]


def test_market_data_heartbeat_writes_data_stale_risk_event(db_session) -> None:
    result = MarketDataHeartbeatService(data_repo=DataRepository(db_session)).check_symbol(
        symbol="BTC/USDT",
        timeframe="1m",
        max_delay_seconds=120,
    )

    assert result["is_fresh"] is False
    events = DataRepository(db_session).list_risk_events(active_only=True)
    assert events[0].event_type == RiskEventType.DATA_STALE


def test_market_data_heartbeat_resolves_only_matching_data_stale_event_after_recovery(db_session) -> None:
    repo = DataRepository(db_session)
    heartbeat = MarketDataHeartbeatService(data_repo=repo)
    heartbeat.check_symbol(symbol="BTC/USDT", timeframe="1m", max_delay_seconds=120)
    heartbeat.check_symbol(symbol="ETH/USDT", timeframe="1m", max_delay_seconds=120)
    now = datetime.now(UTC).replace(microsecond=0)
    repo.store_ohlcv_bars(
        [
            {
                "symbol": "BTC/USDT",
                "exchange": "binance",
                "timeframe": "1m",
                "time": now - timedelta(minutes=1),
                "open": Decimal("100"),
                "high": Decimal("101"),
                "low": Decimal("99"),
                "close": Decimal("100"),
                "volume": Decimal("10"),
            }
        ]
    )

    result = heartbeat.check_symbol(symbol="BTC/USDT", timeframe="1m", max_delay_seconds=120)

    assert result["is_fresh"] is True
    active = repo.list_risk_events(active_only=True)
    assert any(event.affected_scope == ["ETH/USDT"] for event in active)
    assert not any(event.affected_scope == ["BTC/USDT"] for event in active)


def test_decision_veto_budget_exceeded_is_audited_without_overriding_rule_entry(db_session, monkeypatch) -> None:
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
    assert order.veto_result.veto is False
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


def test_configured_multi_timeframe_confirmation_fails_closed_when_4h_data_is_missing(db_session) -> None:
    data_repo = DataRepository(db_session)
    start_at = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=15 * 59)
    rows = []
    for index in range(60):
        price = Decimal("100") + Decimal(index) * Decimal("0.5")
        rows.append(
            {
                "symbol": "BTC/USDT",
                "exchange": "binance",
                "timeframe": "15m",
                "time": start_at + timedelta(minutes=15 * index),
                "open": price,
                "high": price + Decimal("0.6"),
                "low": price - Decimal("0.6"),
                "close": price,
                "volume": Decimal("100"),
            }
        )
    data_repo.store_ohlcv_bars(rows)
    strategy = StrategyContract(
        strategy_id="strategy-mtf",
        strategy_key="mtf_strategy",
        source="manual",
        core_thesis="4h direction must confirm each 15m entry.",
        rules=StrategyRules(
            entry_rules={
                "enabled_signals": ["ema_trend"],
                "timeframe_model": "4h_direction_15m_entry",
                "meta_label_min_win_rate": 0.4,
            }
        ),
    )

    order = PaperSignalGenerator(data_repo=data_repo).generate_order(
        paper_run=PaperRun(
            paper_run_id="paper-mtf",
            strategy_id=strategy.strategy_id,
            gate_decision_ref="backtest-1",
            execution_profile={"account_equity": 10_000, "equity_peak": 10_000},
        ),
        strategy=strategy,
        request=PaperRunStepRequest(symbol="BTC/USDT", timeframe="15m", enable_decision_veto=False),
        positions=[],
    )

    trace = order.entry_context["decision_pipeline"]
    confirmation = trace["volatility"]["multi_timeframe"]
    assert order.entry_context["paper_order_should_trade"] is False
    assert trace["pipeline_status"] == "multi_timeframe_disagreement"
    assert confirmation["status"] == "confirmation_unavailable_fail_closed"


def test_operator_timeframe_model_requires_fresh_1h_state_confirmation(db_session) -> None:
    data_repo = DataRepository(db_session)
    now = datetime.now(UTC).replace(microsecond=0)
    entry_rows = []
    for index in range(60):
        price = Decimal("100") + Decimal(index)
        entry_rows.append(
            {
                "symbol": "BTC/USDT",
                "exchange": "binance",
                "timeframe": "15m",
                "time": now - timedelta(minutes=15 * (59 - index)),
                "open": price - Decimal("0.5"),
                "high": price + Decimal("0.5"),
                "low": price - Decimal("1"),
                "close": price,
                "volume": Decimal("100"),
            }
        )
    data_repo.store_ohlcv_bars(entry_rows)
    strategy = StrategyContract(
        strategy_id="operator-mtf",
        strategy_key="operator-mtf",
        source="test",
        core_thesis="15m entries require 1h state and 4h trend confirmation.",
        rules=StrategyRules(
            entry_rules={
                "enabled_signals": ["ema_trend"],
                "timeframe_model": "operator_experience_4h_15m_v1",
                "entry_timeframe": "15m",
                "state_timeframe": "1h",
                "direction_timeframe": "4h",
            }
        ),
    )

    order = PaperSignalGenerator(data_repo=data_repo).generate_order(
        paper_run=PaperRun(
            paper_run_id="operator-mtf-run",
            strategy_id=strategy.strategy_id,
            gate_decision_ref="backtest-1",
            execution_profile={"account_equity": 10_000, "equity_peak": 10_000},
        ),
        strategy=strategy,
        request=PaperRunStepRequest(symbol="BTC/USDT", timeframe="15m", enable_decision_veto=False),
        positions=[],
    )

    confirmation = order.entry_context["decision_pipeline"]["volatility"]["multi_timeframe"]
    assert order.entry_context["paper_order_should_trade"] is False
    assert confirmation["status"] == "state_confirmation_unavailable_fail_closed"
