from __future__ import annotations

from datetime import UTC, datetime

from apps.api.routers import runs as runs_router
from services.strategy_library import ExecutionRepository, HypothesisRepository, ValidationRepository
from shared.models import (
    BacktestEngine,
    BacktestReport,
    BacktestRun,
    ExchangeAccountSnapshot,
    ExchangeGatewayCapability,
    GateDecision,
    HypothesisRecord,
    PodRiskReport,
    ReconciliationRecord,
    ValidationBenchmarkResult,
)


class StubRuntimeGateway:
    capability = ExchangeGatewayCapability(
        gateway_name="binance_usdt_perpetual",
        exchange="binance",
        market_type="usdt_perpetual",
        supports_account_sync=True,
        supports_positions_sync=True,
        supports_order_submit=True,
        supports_order_cancel=True,
        supports_reconciliation=True,
    )


class StubLiveService:
    def __init__(self, db_session) -> None:
        self.db_session = db_session
        self.gateway = StubRuntimeGateway()

    def sync_account(self, *, live_run_id: str) -> ExchangeAccountSnapshot:
        snapshot = ExchangeAccountSnapshot(
            snapshot_id="acct-snap-1",
            live_run_id=live_run_id,
            exchange="binance",
            wallet_balance=1200.0,
            available_balance=1000.0,
            margin_balance=1180.0,
            unrealized_pnl=12.0,
            open_position_count=1,
            snapshot_time=datetime.now(UTC),
        )
        return ExecutionRepository(self.db_session).create_account_snapshot(snapshot)

    def reconcile_live_run(self, *, live_run_id: str) -> ReconciliationRecord:
        record = ReconciliationRecord(
            reconciliation_id="reconcile-1",
            live_run_id=live_run_id,
            reconciliation_status="ok",
            open_order_count=0,
            position_mismatches=[],
            notes=["stub reconcile"],
        )
        return ExecutionRepository(self.db_session).create_reconciliation_record(record)


def _create_live_run(api_client, db_session) -> str:
    strategy_resp = api_client.post(
        "/api/v1/strategies",
        json={
            "strategy_key": "execution_runtime_live_strategy",
            "source": "manual",
            "core_thesis": "runtime endpoints should use admitted live runs",
            "rules": {
                "stoploss_rules": {"price": 59000},
                "takeprofit_rules": {"price": 62000},
                "position_rules": {"risk_per_trade": 0.01},
            },
        },
    )
    assert strategy_resp.status_code == 201
    strategy_id = strategy_resp.json()["strategy_id"]
    hypothesis = HypothesisRepository(db_session).create_hypothesis(
        HypothesisRecord(
            strategy_id=strategy_id,
            title="Runtime endpoint hypothesis",
            statement="Execution runtime endpoints should work only for validation-admitted live runs.",
            benchmark_plan={"benchmarks": ["passive_hold"]},
            acceptance_criteria={"min_deflated_sharpe": 1.0},
            status="approved",
        )
    )
    backtest = ValidationRepository(db_session).create_backtest_run(
        BacktestRun(
            strategy_id=strategy_id,
            execution_engine="freqtrade",
            validation_methodology={"hypothesis_id": hypothesis.hypothesis_id},
            metrics_summary=BacktestReport(
                strategy_id=strategy_id,
                engine=BacktestEngine.FREQTRADE,
                sharpe=1.8,
                deflated_sharpe=1.4,
                profit_factor=1.6,
                max_drawdown=0.08,
                win_rate=0.57,
                expectancy=0.1,
                hypothesis_id=hypothesis.hypothesis_id,
                benchmark_results=[
                    ValidationBenchmarkResult(
                        benchmark_name="passive_hold",
                        benchmark_type="market_baseline",
                        baseline_return=0.02,
                        strategy_return=0.09,
                        excess_return=0.07,
                        passed=True,
                    )
                ],
                validation_windows=[{"window_id": "oos-1", "passed": True, "sharpe": 1.1, "expectancy": 0.03}],
                pod_risk_report=PodRiskReport(
                    pod_id="runtime-pod",
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
                reason="fully admitted",
            ),
        )
    )
    live_resp = api_client.post(
        "/api/v1/execution/live-runs",
        json={"strategy_id": strategy_id, "validation_backtest_run_id": backtest.backtest_run_id},
    )
    assert live_resp.status_code == 201
    return live_resp.json()["live_run_id"]


def test_execution_gateway_capabilities_endpoint_lists_runtime_capabilities(api_client) -> None:
    resp = api_client.get("/api/v1/execution/gateway-capabilities")

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] >= 1
    assert any(item["gateway_name"] for item in body["items"])


def test_live_account_sync_and_snapshot_query(api_client, db_session, monkeypatch) -> None:
    live_run_id = _create_live_run(api_client, db_session)
    monkeypatch.setattr(runs_router, "_live_service", lambda db: StubLiveService(db_session))

    sync_resp = api_client.post(f"/api/v1/execution/live-runs/{live_run_id}/sync-account")

    assert sync_resp.status_code == 201
    assert sync_resp.json()["live_run_id"] == live_run_id

    list_resp = api_client.get(f"/api/v1/execution/account-snapshots?live_run_id={live_run_id}")

    assert list_resp.status_code == 200
    assert list_resp.json()["total"] == 1
    assert list_resp.json()["items"][0]["wallet_balance"] == 1200.0


def test_live_reconcile_endpoint_persists_status(api_client, db_session, monkeypatch) -> None:
    live_run_id = _create_live_run(api_client, db_session)
    monkeypatch.setattr(runs_router, "_live_service", lambda db: StubLiveService(db_session))

    reconcile_resp = api_client.post(f"/api/v1/execution/live-runs/{live_run_id}/reconcile")

    assert reconcile_resp.status_code == 201
    assert reconcile_resp.json()["reconciliation_status"] == "ok"

    list_resp = api_client.get(f"/api/v1/execution/reconciliations?live_run_id={live_run_id}")

    assert list_resp.status_code == 200
    assert list_resp.json()["total"] == 1
    assert list_resp.json()["items"][0]["live_run_id"] == live_run_id
