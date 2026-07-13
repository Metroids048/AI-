from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from services.data import DataRepository
from services.strategy_library import (
    ExecutionRepository,
    HypothesisRepository,
    RiskProfileRepository,
    StrategyRepository,
    ValidationRepository,
)
from shared.models import (
    BacktestEngine,
    BacktestReport,
    BacktestRun,
    BinanceTestnetAccountStatus,
    BinanceTestnetOrderView,
    GateDecision,
    HypothesisRecord,
    OrderExecution,
    PodRiskReport,
    RiskProfile,
    ValidationBenchmarkResult,
)


def _create_validated_paper_run(
    api_client,
    db_session,
    *,
    strategy_key: str = "paper_runtime_trend",
    stoploss_rules: dict | None = None,
    takeprofit_rules: dict | None = None,
) -> tuple[str, str]:
    strategy_resp = api_client.post(
        "/api/v1/strategies",
        json={
            "strategy_key": strategy_key,
            "source": "open_source:freqtrade",
            "core_thesis": "runtime should auto-monitor candidate symbols and keep paper trading inside gates",
            "rules": {
                "entry_rules": {
                    "ema_fast": 20,
                    "ema_slow": 50,
                    "macd_confirmation": True,
                    "fee_bps": 0,
                    "slippage_bps": 0,
                },
                "exit_rules": {"max_hold_bars": 48},
                "stoploss_rules": stoploss_rules or {"fixed_bps": 5000},
                "takeprofit_rules": takeprofit_rules or {"risk_reward": 2},
                "position_rules": {"risk_per_trade": 0.01, "max_leverage": 1},
            },
        },
    )
    assert strategy_resp.status_code == 201
    strategy_id = strategy_resp.json()["strategy_id"]
    hypothesis = HypothesisRepository(db_session).create_hypothesis(
        HypothesisRecord(
            strategy_id=strategy_id,
            title="Paper runtime admission hypothesis",
            statement="Autonomous paper cycles should only run on strategies with full promotion evidence.",
            benchmark_plan={"benchmarks": ["passive_hold", "strict_random_control"]},
            acceptance_criteria={"min_deflated_sharpe": 1.0},
            status="approved",
        )
    )
    backtest = ValidationRepository(db_session).create_backtest_run(
        BacktestRun(
            strategy_id=strategy_id,
            validation_methodology={"hypothesis_id": hypothesis.hypothesis_id},
            execution_engine="freqtrade",
            metrics_summary=BacktestReport(
                strategy_id=strategy_id,
                engine=BacktestEngine.FREQTRADE,
                sharpe=1.5,
                deflated_sharpe=1.2,
                profit_factor=1.4,
                max_drawdown=0.1,
                win_rate=0.55,
                expectancy=0.08,
                hypothesis_id=hypothesis.hypothesis_id,
                benchmark_results=[
                    ValidationBenchmarkResult(
                        benchmark_name="strict_random_control",
                        benchmark_type="strict_random_control",
                        baseline_return=0.01,
                        strategy_return=0.07,
                        excess_return=0.06,
                        passed=True,
                    )
                ],
                validation_windows=[{"window_id": "oos-1", "passed": True, "sharpe": 1.05, "expectancy": 0.03}],
                pod_risk_report=PodRiskReport(
                    pod_id="paper-runtime-pod",
                    passed=True,
                    violations=[],
                    max_expected_loss=0.02,
                    max_expected_leverage=1.0,
                    data_freshness_ok=True,
                ),
            ),
            eligibility_result=GateDecision(
                strategy_id=strategy_id,
                passed=True,
                decision_status="accepted",
                reason="validated for autonomous paper runtime",
            ),
        )
    )
    paper_resp = api_client.post(
        "/api/v1/execution/paper-runs",
        json={
            "strategy_id": strategy_id,
            "gate_decision_ref": backtest.backtest_run_id,
            "candidate_symbols": ["BTC/USDT", "ETH/USDT"],
            "execution_profile": {"account_equity": 10_000, "equity_peak": 10_000},
        },
    )
    assert paper_resp.status_code == 202
    return strategy_id, paper_resp.json()["resource_id"]


def _trend_closes(*, start: Decimal, step: Decimal, count: int = 80) -> list[Decimal]:
    return [start + step * Decimal(index) for index in range(count)]


def _store_trend_bars(db_session, *, symbol: str, closes: list[Decimal], start_at: datetime) -> None:
    repo = DataRepository(db_session)
    bars = []
    for index, close in enumerate(closes):
        timestamp = start_at + timedelta(hours=index)
        bars.append(
            {
                "symbol": symbol,
                "exchange": "binance",
                "timeframe": "1h",
                "time": timestamp,
                "open": close,
                "high": close,
                "low": close,
                "close": close,
                "volume": Decimal("10"),
            }
        )
    repo.store_ohlcv_bars(bars)


def test_paper_runtime_auto_cycle_opens_positions_and_updates_status(api_client, db_session) -> None:
    _, paper_run_id = _create_validated_paper_run(api_client, db_session)
    start_at = datetime.now(UTC).replace(microsecond=0) - timedelta(hours=79)
    _store_trend_bars(
        db_session,
        symbol="BTC/USDT",
        closes=_trend_closes(start=Decimal("60000"), step=Decimal("100")),
        start_at=start_at,
    )
    _store_trend_bars(
        db_session,
        symbol="ETH/USDT",
        closes=_trend_closes(start=Decimal("3000"), step=Decimal("-5")),
        start_at=start_at,
    )

    cycle_resp = api_client.post(
        f"/api/v1/execution/paper-runs/{paper_run_id}/auto-cycle",
        json={"max_symbols": 2, "timeframe": "1h", "enable_decision_veto": False},
    )

    assert cycle_resp.status_code == 200
    body = cycle_resp.json()
    assert body["opened_positions"] == 2
    assert body["closed_positions"] == 0
    assert body["rejected_orders"] == 0
    assert set(body["open_position_symbols"]) == {"BTC/USDT", "ETH/USDT"}
    assert {item["symbol"]: item["action"] for item in body["actions"]} == {
        "BTC/USDT": "open_long",
        "ETH/USDT": "open_short",
    }

    status_resp = api_client.get(f"/api/v1/execution/paper-runs/{paper_run_id}/runtime-status")

    assert status_resp.status_code == 200
    status_body = status_resp.json()
    assert status_body["paper_status"] == "running"
    assert set(status_body["open_position_symbols"]) == {"BTC/USDT", "ETH/USDT"}
    assert status_body["last_action_counts"]["opened"] == 2


def test_paper_runtime_auto_cycle_all_runs_running_paper_runs(api_client, db_session) -> None:
    strategy_id, paper_run_id = _create_validated_paper_run(api_client, db_session)
    start_at = datetime.now(UTC).replace(microsecond=0) - timedelta(hours=79)
    _store_trend_bars(
        db_session,
        symbol="BTC/USDT",
        closes=_trend_closes(start=Decimal("60000"), step=Decimal("100")),
        start_at=start_at,
    )
    status_resp = api_client.patch(
        f"/api/v1/execution/paper-runs/{paper_run_id}/status",
        json={"paper_status": "running"},
    )
    assert status_resp.status_code == 200
    strategy = StrategyRepository(db_session).get_strategy(strategy_id)
    assert strategy is not None
    assert strategy.paper_status.value == "running"

    cycle_resp = api_client.post(
        "/api/v1/execution/paper-runs/auto-cycle-all",
        json={"symbols": ["BTC/USDT"], "max_symbols": 1, "timeframe": "1h", "enable_decision_veto": False},
    )

    assert cycle_resp.status_code == 200
    body = cycle_resp.json()
    assert body["paper_runs"] == 1
    assert body["results"][0]["paper_run_id"] == paper_run_id
    assert body["results"][0]["opened_positions"] == 1


def test_paper_run_execution_profile_patch_preserves_existing_keys(api_client, db_session) -> None:
    _, paper_run_id = _create_validated_paper_run(api_client, db_session)

    profile_resp = api_client.patch(
        f"/api/v1/execution/paper-runs/{paper_run_id}/execution-profile",
        json={"mirror_to_gateway": True},
    )

    assert profile_resp.status_code == 200
    profile = profile_resp.json()["execution_profile"]
    assert profile["mirror_to_gateway"] is True
    assert profile["account_equity"] == 10_000
    assert profile["equity_peak"] == 10_000


def test_paper_run_auto_settings_updates_profile_and_strategy_rules(api_client, db_session) -> None:
    strategy_id, paper_run_id = _create_validated_paper_run(api_client, db_session)
    risk_profile = RiskProfile(risk_profile_id="auto-settings-risk")
    RiskProfileRepository(db_session).create_profile(risk_profile)
    attach_resp = api_client.patch(
        f"/api/v1/execution/paper-runs/{paper_run_id}/execution-profile",
        json={"risk_profile_id": "auto-settings-risk"},
    )
    assert attach_resp.status_code == 200

    response = api_client.patch(
        f"/api/v1/execution/paper-runs/{paper_run_id}/auto-settings",
        json={
            "execution_mode": "binance_simulation_first",
            "max_leverage": 5,
            "risk_per_trade": 0.01,
            "order_notional_usdt": 120,
            "max_open_positions": 5,
            "max_symbols": 20,
            "max_symbol_exposure": 0.15,
            "max_total_exposure": 0.5,
            "daily_loss_limit": 0.04,
            "weekly_loss_limit": 0.08,
            "hard_stop_drawdown_limit": 0.2,
            "strategy_lanes": ["carry", "trend_breakout"],
            "stoploss": {"atr_multiple": 2, "fixed_bps": 250},
            "takeprofit": {"risk_reward": 2.5, "trail_after_r": 1.5},
            "llm_veto_enabled": False,
            "market_intelligence_enabled": True,
        },
    )

    assert response.status_code == 200
    profile = response.json()["execution_profile"]
    assert profile["execution_mode"] == "binance_simulation_first"
    assert profile["mirror_to_gateway"] is True
    assert profile["max_open_positions"] == 5
    stored_profile = RiskProfileRepository(db_session).get_profile("auto-settings-risk")
    assert stored_profile is not None
    assert stored_profile.max_leverage == 5
    assert stored_profile.max_open_positions == 5
    strategy = StrategyRepository(db_session).get_strategy(strategy_id)
    assert strategy is not None
    assert strategy.rules.position_rules["order_notional_usdt"] == 120
    assert strategy.rules.entry_rules["strategy_lanes"] == ["carry", "trend_breakout"]

    # The operator-set max_leverage (5x) must actually drive the tier table that
    # PaperSignalGenerator reads at order time — not stay pinned at the stale
    # default (core=20x) the client echoed back in the request body.
    tiers = profile["asset_risk_tiers"]
    assert tiers["core"]["leverage"] == 5
    assert tiers["standard"]["leverage"] == 2.5


def test_order_sync_reconciles_non_btc_orders_across_fixed_top20(api_client, db_session, monkeypatch) -> None:
    from apps.api.routers import runs as runs_router

    strategy_id, paper_run_id = _create_validated_paper_run(api_client, db_session)
    ExecutionRepository(db_session).create_order(
        OrderExecution(
            strategy_id=strategy_id,
            paper_run_id=paper_run_id,
            symbol="ETH/USDT",
            direction="long",
            execution_status="filled",
            stoploss_present=True,
            gateway_order_id="eth-order-1",
            gateway_status="filled",
        )
    )
    monkeypatch.setattr(
        runs_router,
        "probe_testnet_account",
        lambda order_limit=20, order_symbols=None: BinanceTestnetAccountStatus(
            connected=True,
            recent_orders=[
                BinanceTestnetOrderView(
                    order_id="eth-order-1",
                    symbol="ETH/USDT",
                    side="buy",
                    order_type="market",
                    status="filled",
                    quantity=0.01,
                ),
                BinanceTestnetOrderView(
                    order_id="sol-external-1",
                    symbol="SOL/USDT",
                    side="sell",
                    order_type="market",
                    status="filled",
                    quantity=1,
                ),
            ],
        ),
    )

    response = api_client.get(f"/api/v1/execution/paper-runs/{paper_run_id}/order-sync")

    assert response.status_code == 200
    body = response.json()
    assert len(body["symbol_summary"]) == 20
    eth = next(item for item in body["symbol_summary"] if item["symbol"] == "ETH/USDT")
    sol = next(item for item in body["symbol_summary"] if item["symbol"] == "SOL/USDT")
    assert eth["matched_order_count"] == 1
    assert sol["unmatched_gateway_order_count"] == 1
    assert body["matched_local_order_count"] == 1
    assert [item["order_id"] for item in body["unmatched_gateway_orders"]] == ["sol-external-1"]


def test_paper_runtime_auto_cycle_closes_position_on_opposite_signal(api_client, db_session) -> None:
    _, paper_run_id = _create_validated_paper_run(api_client, db_session)
    start_at = datetime.now(UTC).replace(microsecond=0) - timedelta(hours=79)
    _store_trend_bars(
        db_session,
        symbol="BTC/USDT",
        closes=_trend_closes(start=Decimal("60000"), step=Decimal("100")),
        start_at=start_at,
    )

    first_cycle = api_client.post(
        f"/api/v1/execution/paper-runs/{paper_run_id}/auto-cycle",
        json={"symbols": ["BTC/USDT"], "max_symbols": 1, "timeframe": "1h", "enable_decision_veto": False},
    )
    assert first_cycle.status_code == 200
    assert first_cycle.json()["opened_positions"] == 1

    _store_trend_bars(
        db_session,
        symbol="BTC/USDT",
        closes=_trend_closes(start=Decimal("70000"), step=Decimal("-150")),
        start_at=datetime.now(UTC).replace(microsecond=0) + timedelta(hours=1),
    )

    second_cycle = api_client.post(
        f"/api/v1/execution/paper-runs/{paper_run_id}/auto-cycle",
        json={"symbols": ["BTC/USDT"], "max_symbols": 1, "timeframe": "1h", "enable_decision_veto": False},
    )

    assert second_cycle.status_code == 200
    body = second_cycle.json()
    assert body["opened_positions"] == 0
    assert body["closed_positions"] == 1
    assert body["open_position_symbols"] == []
    assert body["actions"][0]["action"] == "close_long"

    status_resp = api_client.get(f"/api/v1/execution/paper-runs/{paper_run_id}/runtime-status")
    assert status_resp.status_code == 200
    assert status_resp.json()["open_position_symbols"] == []


def test_paper_runtime_auto_cycle_partial_closes_via_exit_ladder(api_client, db_session) -> None:
    _, paper_run_id = _create_validated_paper_run(
        api_client,
        db_session,
        stoploss_rules={"fixed_bps": 200},
        takeprofit_rules={
            "exit_ladder": [{"r_multiple": 1.0, "close_fraction": 0.4}],
            "remainder_trail_after_r": 2.5,
        },
    )
    start_at = datetime.now(UTC).replace(microsecond=0) - timedelta(hours=79)
    closes = _trend_closes(start=Decimal("60000"), step=Decimal("100"))
    _store_trend_bars(db_session, symbol="BTC/USDT", closes=closes, start_at=start_at)

    first_cycle = api_client.post(
        f"/api/v1/execution/paper-runs/{paper_run_id}/auto-cycle",
        json={"symbols": ["BTC/USDT"], "max_symbols": 1, "timeframe": "1h", "enable_decision_veto": False},
    )
    assert first_cycle.status_code == 200
    first_body = first_cycle.json()
    assert first_body["opened_positions"] == 1
    assert first_body["actions"][0]["action"] == "open_long"

    # entry_price == last closed 1h bar's close (decision pipeline reference price);
    # stoploss is deterministic via fixed_bps, so the ladder's L1 trigger price
    # (entry + risk_distance * r_multiple) is computable without reading the
    # response back.
    entry_price = closes[-1]
    stop_distance = entry_price * Decimal("200") / Decimal("10000")
    trigger_price = entry_price + stop_distance

    DataRepository(db_session).store_ohlcv_bars(
        [
            {
                "symbol": "BTC/USDT",
                "exchange": "binance",
                "timeframe": "1m",
                "time": datetime.now(UTC).replace(microsecond=0),
                "open": entry_price,
                "high": trigger_price + Decimal("50"),
                "low": entry_price,
                "close": trigger_price,
                "volume": Decimal("5"),
            }
        ]
    )

    second_cycle = api_client.post(
        f"/api/v1/execution/paper-runs/{paper_run_id}/auto-cycle",
        json={"symbols": ["BTC/USDT"], "max_symbols": 1, "timeframe": "1h", "enable_decision_veto": False},
    )

    assert second_cycle.status_code == 200
    body = second_cycle.json()
    assert body["opened_positions"] == 0
    assert body["closed_positions"] == 0
    assert body["open_position_symbols"] == ["BTC/USDT"]
    ladder_action = body["actions"][0]
    assert ladder_action["action"] == "exit_ladder_partial_long"
    assert ladder_action["decision_trace"]["close_fraction"] == 0.4
    assert ladder_action["decision_trace"]["remaining_quantity"] > 0

    status_resp = api_client.get(f"/api/v1/execution/paper-runs/{paper_run_id}/runtime-status")
    assert status_resp.status_code == 200
    assert status_resp.json()["open_position_symbols"] == ["BTC/USDT"]
