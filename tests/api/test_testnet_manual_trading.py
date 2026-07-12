from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from apps.api.config import Settings, validate_trading_environment
from services.data import DataRepository
from services.strategy_library import ExecutionRepository, HypothesisRepository, ValidationRepository
from shared.models import (
    BacktestEngine,
    BacktestReport,
    BacktestRun,
    GateDecision,
    HypothesisRecord,
    OrderExecution,
    PodRiskReport,
    RiskEvent,
    RiskEventType,
    RiskSeverity,
    TradeSide,
    ValidationBenchmarkResult,
)


def _create_validated_strategy(api_client, db_session) -> tuple[str, str]:
    strategy_resp = api_client.post(
        "/api/v1/strategies",
        json={
            "strategy_key": "manual_testnet_console_strategy",
            "source": "manual",
            "core_thesis": "manual console orders still require validated strategy evidence",
            "rules": {
                "entry_rules": {"funding_threshold": 0.0001},
                "exit_rules": {"basis_reversion_bps": 5},
                "stoploss_rules": {"price": 59000},
                "takeprofit_rules": {"price": 64000},
                "position_rules": {"risk_per_trade": 0.01, "max_leverage": 2},
            },
        },
    )
    assert strategy_resp.status_code == 201
    strategy_id = strategy_resp.json()["strategy_id"]
    hypothesis = HypothesisRepository(db_session).create_hypothesis(
        HypothesisRecord(
            strategy_id=strategy_id,
            title="Manual trading console validation",
            statement="Manual testnet/paper orders use the same strategy validation boundary as automation.",
            benchmark_plan={"benchmarks": ["passive_hold"]},
            acceptance_criteria={"min_deflated_sharpe": 1.0},
            status="approved",
        )
    )
    backtest = ValidationRepository(db_session).create_backtest_run(
        BacktestRun(
            strategy_id=strategy_id,
            validation_methodology={"hypothesis_id": hypothesis.hypothesis_id},
            execution_engine="vectorbt",
            metrics_summary=BacktestReport(
                strategy_id=strategy_id,
                engine=BacktestEngine.VECTORBT,
                sharpe=1.8,
                deflated_sharpe=1.25,
                profit_factor=1.5,
                max_drawdown=0.08,
                win_rate=0.56,
                expectancy=0.06,
                hypothesis_id=hypothesis.hypothesis_id,
                benchmark_results=[
                    ValidationBenchmarkResult(
                        benchmark_name="passive_hold",
                        benchmark_type="market_baseline",
                        baseline_return=0.02,
                        strategy_return=0.08,
                        excess_return=0.06,
                        passed=True,
                    )
                ],
                validation_windows=[{"window_id": "oos-1", "passed": True, "sharpe": 1.1, "expectancy": 0.04}],
                pod_risk_report=PodRiskReport(
                    pod_id="manual-console",
                    passed=True,
                    violations=[],
                    max_expected_loss=0.02,
                    max_expected_leverage=2.0,
                    data_freshness_ok=True,
                ),
            ),
            eligibility_result=GateDecision(
                strategy_id=strategy_id,
                passed=True,
                decision_status="accepted",
                reason="manual console admitted",
            ),
        )
    )
    return strategy_id, backtest.backtest_run_id


def _store_fresh_bar(db_session, *, symbol: str = "BTC/USDT", close: Decimal = Decimal("61000")) -> None:
    DataRepository(db_session).store_ohlcv_bars(
        [
            {
                "symbol": symbol,
                "exchange": "binance",
                "timeframe": "1h",
                "time": datetime.now(UTC).replace(microsecond=0),
                "open": close,
                "high": close,
                "low": close,
                "close": close,
                "volume": Decimal("100"),
            }
        ]
    )


def test_trading_environment_rejects_mainnet_trading_in_paper_or_testnet_env() -> None:
    settings = Settings(
        app_env="paper",
        binance_use_testnet=False,
        live_trading_enabled=True,
        admin_api_token="local-token",
    )

    with pytest.raises(ValueError, match="paper/testnet environments require BINANCE_USE_TESTNET=true"):
        validate_trading_environment(settings)


def test_trading_status_api_reports_effective_mock_auto_execution(api_client, monkeypatch) -> None:
    from apps.api.config import settings
    from apps.api.routers import runs as runs_router

    monkeypatch.setattr(settings, "binance_api_key", "key")
    monkeypatch.setattr(settings, "binance_api_secret", "secret")
    monkeypatch.setattr(settings, "binance_use_testnet", True)
    monkeypatch.setattr(settings, "live_trading_enabled", False)
    monkeypatch.setattr(settings, "binance_auto_execute", True)
    monkeypatch.setattr(settings, "app_build_id", "test-build")
    monkeypatch.setattr(
        runs_router,
        "configured_gateways",
        lambda: (_ for _ in ()).throw(AssertionError("status endpoint must not initialize a network gateway")),
    )

    response = api_client.get("/api/v1/execution/trading-status")

    assert response.status_code == 200
    body = response.json()
    assert body["exchange"] == "binance"
    assert body["live_trading_enabled"] is False
    assert body["auto_execute_enabled"] is True
    assert body["auto_execution_state"] == "armed"
    assert body["fixed_top20_count"] == 20
    assert body["backend_build_id"] == "test-build"
    assert "task_run_counts" in body
    assert "task_failure_counts" in body
    assert "top20_coverage_count" in body
    assert body["queue_backlog_status"] == "not_probed"
    assert "api_secret" not in str(body).lower()
    assert "api_key" not in str(body).lower()


def test_trading_status_reports_external_desktop_scheduler(api_client, monkeypatch) -> None:
    from apps.api.routers import runs as runs_router

    monkeypatch.setattr(runs_router, "_local_scheduler_process_running", lambda: True)

    response = api_client.get("/api/v1/execution/trading-status")

    assert response.status_code == 200
    assert response.json()["scheduler_mode"] == "external_local"
    assert response.json()["scheduler_running"] is True


def test_manual_trading_context_is_paper_only_and_reused(api_client) -> None:
    first = api_client.get("/api/v1/execution/manual-trading-context", params={"mode": "paper"})
    second = api_client.post("/api/v1/execution/manual-trading-context", params={"mode": "paper"})
    rejected = api_client.get("/api/v1/execution/manual-trading-context", params={"mode": "testnet"})

    assert first.status_code == 200
    assert second.status_code == 201
    assert rejected.status_code == 400
    first_body = first.json()
    second_body = second.json()
    assert first_body["context_key"] == "manual_paper_sandbox"
    assert first_body["strategy_id"] == second_body["strategy_id"]
    assert first_body["validation_backtest_run_id"] == second_body["validation_backtest_run_id"]
    assert first_body["paper_run_id"] == second_body["paper_run_id"]
    assert "Paper-only" in first_body["warning"]


def test_manual_paper_order_uses_auto_context(api_client, db_session) -> None:
    context = api_client.get("/api/v1/execution/manual-trading-context").json()
    _store_fresh_bar(db_session, symbol="ETH/USDT", close=Decimal("3200"))

    response = api_client.post(
        "/api/v1/execution/manual-orders",
        json={
            "mode": "paper",
            "strategy_id": context["strategy_id"],
            "validation_backtest_run_id": context["validation_backtest_run_id"],
            "paper_run_id": context["paper_run_id"],
            "symbol": "ETH/USDT",
            "direction": "long",
            "quantity": 0.1,
            "reference_price": 3200,
            "leverage": 1,
            "timeframe": "1h",
            "stoploss_price": 3168,
            "takeprofit_price": 3264,
            "account_equity": 10000,
        },
    )

    assert response.status_code == 201
    assert response.json()["execution_status"] == "filled"
    assert response.json()["gateway_name"] == "paper_manual"


def test_manual_paper_reopen_after_close_counts_only_current_positions(api_client, db_session) -> None:
    context = api_client.get("/api/v1/execution/manual-trading-context").json()
    _store_fresh_bar(db_session, symbol="ETH/USDT", close=Decimal("3200"))
    body = {
        "mode": "paper",
        "strategy_id": context["strategy_id"],
        "validation_backtest_run_id": context["validation_backtest_run_id"],
        "paper_run_id": context["paper_run_id"],
        "symbol": "ETH/USDT",
        "direction": "long",
        "quantity": 0.1,
        "reference_price": 3200,
        "leverage": 1,
        "timeframe": "1h",
        "stoploss_price": 3168,
        "takeprofit_price": 3264,
        "account_equity": 10000,
    }

    first = api_client.post("/api/v1/execution/manual-orders", json=body)
    close = api_client.post(
        "/api/v1/execution/close-position",
        json={
            "mode": "paper",
            "strategy_id": context["strategy_id"],
            "validation_backtest_run_id": context["validation_backtest_run_id"],
            "paper_run_id": context["paper_run_id"],
            "symbol": "ETH/USDT",
            "reference_price": 3200,
            "timeframe": "1h",
            "account_equity": 10000,
        },
    )
    second = api_client.post("/api/v1/execution/manual-orders", json=body)

    assert first.status_code == 201
    assert close.status_code == 201
    assert second.status_code == 201
    assert second.json()["execution_status"] == "filled"


def test_manual_paper_order_fills_and_creates_position(api_client, db_session) -> None:
    strategy_id, backtest_run_id = _create_validated_strategy(api_client, db_session)
    _store_fresh_bar(db_session)

    response = api_client.post(
        "/api/v1/execution/manual-orders",
        json={
            "mode": "paper",
            "strategy_id": strategy_id,
            "validation_backtest_run_id": backtest_run_id,
            "symbol": "BTC/USDT",
            "direction": "long",
            "quantity": 0.01,
            "reference_price": 61000,
            "leverage": 2,
            "stoploss_price": 59000,
            "takeprofit_price": 64000,
            "account_equity": 10000,
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["execution_status"] == "filled"
    assert body["gateway_name"] == "paper_manual"
    positions = api_client.get("/api/v1/execution/positions").json()["items"]
    assert positions[0]["symbol"] == "BTC/USDT"
    assert positions[0]["quantity"] == 0.01


def test_manual_order_rejects_blank_validation_evidence_before_gatekeeper(api_client) -> None:
    response = api_client.post(
        "/api/v1/execution/manual-orders",
        json={
            "mode": "paper",
            "strategy_id": " ",
            "validation_backtest_run_id": "",
            "symbol": "BTC/USDT",
            "direction": "long",
            "quantity": 0.01,
            "reference_price": 61000,
            "leverage": 1,
            "stoploss_price": 59000,
            "account_equity": 10000,
        },
    )

    assert response.status_code == 422


def test_manual_order_without_stoploss_is_rejected(api_client, db_session) -> None:
    strategy_id, backtest_run_id = _create_validated_strategy(api_client, db_session)
    _store_fresh_bar(db_session)

    response = api_client.post(
        "/api/v1/execution/manual-orders",
        json={
            "mode": "paper",
            "strategy_id": strategy_id,
            "validation_backtest_run_id": backtest_run_id,
            "symbol": "BTC/USDT",
            "direction": "long",
            "quantity": 0.01,
            "reference_price": 61000,
            "leverage": 1,
            "account_equity": 10000,
        },
    )

    assert response.status_code == 400
    assert response.json()["error_code"] == "manual_order_rejected"
    assert "missing_stoploss" in response.json()["message"]


def test_manual_order_blocked_by_active_high_risk_event(api_client, db_session) -> None:
    strategy_id, backtest_run_id = _create_validated_strategy(api_client, db_session)
    _store_fresh_bar(db_session)
    DataRepository(db_session).store_risk_event(
        RiskEvent(
            event_type=RiskEventType.EXCHANGE_INCIDENT,
            severity=RiskSeverity.HIGH,
            source="test",
            description="Binance incident blocks manual trading",
            affected_scope=["BTC/USDT"],
            expires_at=datetime.now(UTC) + timedelta(minutes=30),
        )
    )

    response = api_client.post(
        "/api/v1/execution/manual-orders",
        json={
            "mode": "paper",
            "strategy_id": strategy_id,
            "validation_backtest_run_id": backtest_run_id,
            "symbol": "BTC/USDT",
            "direction": "long",
            "quantity": 0.01,
            "reference_price": 61000,
            "leverage": 1,
            "stoploss_price": 59000,
            "account_equity": 10000,
        },
    )

    assert response.status_code == 400
    assert "blocking_risk_event" in response.json()["message"]


def test_close_position_uses_close_only_without_stoploss(api_client, db_session) -> None:
    strategy_id, backtest_run_id = _create_validated_strategy(api_client, db_session)
    _store_fresh_bar(db_session)
    open_resp = api_client.post(
        "/api/v1/execution/manual-orders",
        json={
            "mode": "paper",
            "strategy_id": strategy_id,
            "validation_backtest_run_id": backtest_run_id,
            "symbol": "BTC/USDT",
            "direction": "long",
            "quantity": 0.01,
            "reference_price": 61000,
            "leverage": 1,
            "stoploss_price": 59000,
            "account_equity": 10000,
        },
    )
    assert open_resp.status_code == 201

    close_resp = api_client.post(
        "/api/v1/execution/close-position",
        json={
            "mode": "paper",
            "strategy_id": strategy_id,
            "validation_backtest_run_id": backtest_run_id,
            "symbol": "BTC/USDT",
            "reference_price": 61200,
            "account_equity": 10000,
        },
    )

    assert close_resp.status_code == 201
    body = close_resp.json()
    assert body["execution_status"] == "filled"
    assert body["close_only_mode"] is True
    latest_positions = api_client.get("/api/v1/execution/positions").json()["items"]
    assert latest_positions[-1]["quantity"] == 0.0


def test_adjust_leverage_is_audited_for_paper_mode(api_client, db_session) -> None:
    strategy_id, _ = _create_validated_strategy(api_client, db_session)

    response = api_client.post(
        "/api/v1/execution/adjust-leverage",
        json={"mode": "paper", "strategy_id": strategy_id, "symbol": "BTC/USDT", "leverage": 2},
    )

    assert response.status_code == 200
    assert response.json()["gateway_status"] == "paper_leverage_updated"


def test_cancel_order_marks_open_paper_order_cancelled(api_client, db_session) -> None:
    strategy_id, backtest_run_id = _create_validated_strategy(api_client, db_session)
    order = ExecutionRepository(db_session).create_order(
        OrderExecution(
            strategy_id=strategy_id,
            symbol="BTC/USDT",
            direction=TradeSide.LONG,
            execution_status="accepted",
            stoploss_present=True,
            validation_backtest_run_id=backtest_run_id,
            gateway_name="paper_manual",
            gateway_status="accepted",
        )
    )

    response = api_client.post(
        "/api/v1/execution/cancel-order",
        json={"mode": "paper", "order_execution_id": order.order_execution_id},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["execution_status"] == "cancelled"
    assert body["gateway_status"] == "cancelled"
    assert body["lifecycle_history"][-1]["event"] == "manual_cancel"


def test_cancel_order_rejects_filled_order(api_client, db_session) -> None:
    strategy_id, backtest_run_id = _create_validated_strategy(api_client, db_session)
    order = ExecutionRepository(db_session).create_order(
        OrderExecution(
            strategy_id=strategy_id,
            symbol="BTC/USDT",
            direction=TradeSide.LONG,
            execution_status="filled",
            stoploss_present=True,
            validation_backtest_run_id=backtest_run_id,
        )
    )

    response = api_client.post(
        "/api/v1/execution/cancel-order",
        json={"mode": "paper", "order_execution_id": order.order_execution_id},
    )

    assert response.status_code == 400
    assert response.json()["error_code"] == "cancel_order_rejected"
