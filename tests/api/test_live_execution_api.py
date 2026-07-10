from __future__ import annotations

from services.strategy_library import HypothesisRepository, ValidationRepository
from shared.models import (
    BacktestReport,
    BacktestRun,
    BacktestEngine,
    GateDecision,
    HypothesisRecord,
    PodRiskReport,
    ValidationBenchmarkResult,
)


def _create_live_admitted_backtest(api_client, db_session) -> tuple[str, str]:
    strategy_resp = api_client.post(
        "/api/v1/strategies",
        json={
            "strategy_key": "live_run_admitted_strategy",
            "source": "manual",
            "core_thesis": "live admission should require validation evidence",
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
            title="Live admission hypothesis",
            statement="Only strategies with complete promotion evidence can enter live.",
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
                    pod_id="live-pod",
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
    return strategy_id, backtest.backtest_run_id


def test_live_run_creation_rejects_without_validation_backtest(api_client) -> None:
    strategy_resp = api_client.post(
        "/api/v1/strategies",
        json={
            "strategy_key": "live_missing_backtest",
            "source": "manual",
            "core_thesis": "live requires validation evidence",
        },
    )
    assert strategy_resp.status_code == 201

    live_resp = api_client.post(
        "/api/v1/execution/live-runs",
        json={"strategy_id": strategy_resp.json()["strategy_id"]},
    )

    assert live_resp.status_code == 400
    assert live_resp.json()["error_code"] == "live_admission_rejected"


def test_live_run_creation_requires_complete_promotion_evidence(api_client, db_session) -> None:
    strategy_resp = api_client.post(
        "/api/v1/strategies",
        json={
            "strategy_key": "live_incomplete_evidence",
            "source": "manual",
            "core_thesis": "raw backtest pass is not enough for live",
        },
    )
    assert strategy_resp.status_code == 201
    strategy_id = strategy_resp.json()["strategy_id"]
    backtest = ValidationRepository(db_session).create_backtest_run(
        BacktestRun(
            strategy_id=strategy_id,
            execution_engine="freqtrade",
            metrics_summary=BacktestReport(
                strategy_id=strategy_id,
                engine=BacktestEngine.FREQTRADE,
                sharpe=1.7,
                profit_factor=1.5,
                max_drawdown=0.1,
                win_rate=0.56,
                expectancy=0.09,
            ),
            eligibility_result=GateDecision(
                strategy_id=strategy_id,
                passed=True,
                decision_status="accepted",
                reason="legacy pass only",
            ),
        )
    )

    live_resp = api_client.post(
        "/api/v1/execution/live-runs",
        json={
            "strategy_id": strategy_id,
            "validation_backtest_run_id": backtest.backtest_run_id,
        },
    )

    assert live_resp.status_code == 400
    assert "missing_hypothesis" in live_resp.json()["message"]


def test_live_run_creation_succeeds_with_complete_promotion_evidence(api_client, db_session) -> None:
    strategy_id, backtest_run_id = _create_live_admitted_backtest(api_client, db_session)

    live_resp = api_client.post(
        "/api/v1/execution/live-runs",
        json={
            "strategy_id": strategy_id,
            "validation_backtest_run_id": backtest_run_id,
        },
    )

    assert live_resp.status_code == 201
    body = live_resp.json()
    assert body["strategy_id"] == strategy_id
    assert body["validation_backtest_run_id"] == backtest_run_id
    assert body["live_status"] == "queued"
