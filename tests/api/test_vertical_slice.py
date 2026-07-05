from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from services.data import DataRepository
from services.strategy_library import HypothesisRepository, ValidationRepository
from shared.models import (
    BacktestReport,
    BacktestRun,
    GateDecision,
    HypothesisRecord,
    MarketExtras,
    PodRiskReport,
    ValidationBenchmarkResult,
)


def test_strategy_to_backtest_to_paper_vertical_slice(api_client, db_session) -> None:
    idea_resp = api_client.post(
        "/api/v1/strategies/ideas",
        json={
            "title": "Binance carry idea",
            "source": "manual_note",
            "hypothesis_summary": "Funding windows can drive BTC/ETH carry trades.",
            "symbol_scope": ["BTC/USDT", "ETH/USDT"],
        },
    )
    assert idea_resp.status_code == 201
    idea_id = idea_resp.json()["idea_id"]

    draft_resp = api_client.post(f"/api/v1/strategies/ideas/{idea_id}/drafts")
    assert draft_resp.status_code == 201
    draft_id = draft_resp.json()["draft_id"]

    strategy_resp = api_client.post(f"/api/v1/strategies/{draft_id}/materialize")
    assert strategy_resp.status_code == 201
    strategy_id = strategy_resp.json()["strategy_id"]

    version_resp = api_client.post(
        "/api/v1/strategies/versions",
        json={
            "strategy_id": strategy_id,
            "version_label": "v1",
            "change_summary": "initial persisted version",
        },
    )
    assert version_resp.status_code == 201
    version_id = version_resp.json()["version_id"]
    hypothesis = HypothesisRepository(db_session).create_hypothesis(
        HypothesisRecord(
            strategy_id=strategy_id,
            idea_id=idea_id,
            title="Carry vertical slice hypothesis",
            statement="Carry strategy should beat passive hold and survive OOS plus pod risk review before paper.",
            benchmark_plan={"benchmarks": ["passive_hold", "strict_random_control"]},
            acceptance_criteria={"min_deflated_sharpe": 1.0},
            status="approved",
        )
    )

    backtest = ValidationRepository(db_session).create_backtest_run(
        BacktestRun(
            strategy_id=strategy_id,
            version_id=version_id,
            execution_engine="freqtrade",
            sample_split_plan={"train": "2024Q1", "oos": "2024Q2"},
            validation_methodology={"lane": "carry_research", "hypothesis_id": hypothesis.hypothesis_id},
            cost_model_ref="spot hedge reconciliation performed platform-side",
            stress_test_scenarios=["funding_flip", "spread_widening"],
            metrics_summary=BacktestReport(
                strategy_id=strategy_id,
                engine="freqtrade",
                sharpe=1.5,
                deflated_sharpe=1.2,
                profit_factor=1.4,
                max_drawdown=0.12,
                win_rate=0.57,
                expectancy=0.1,
                total_cost_bps=14.0,
                hypothesis_id=hypothesis.hypothesis_id,
                benchmark_results=[
                    ValidationBenchmarkResult(
                        benchmark_name="passive_hold",
                        benchmark_type="market_baseline",
                        baseline_return=0.03,
                        strategy_return=0.09,
                        excess_return=0.06,
                        passed=True,
                    ),
                    ValidationBenchmarkResult(
                        benchmark_name="strict_random_control",
                        benchmark_type="strict_random_control",
                        baseline_return=0.01,
                        strategy_return=0.09,
                        excess_return=0.08,
                        passed=True,
                    ),
                ],
                validation_windows=[{"window_id": "oos-1", "passed": True, "sharpe": 1.05, "expectancy": 0.03}],
                pod_risk_report=PodRiskReport(
                    pod_id="carry-pod",
                    passed=True,
                    violations=[],
                    max_expected_loss=0.03,
                    max_expected_leverage=1.0,
                    data_freshness_ok=True,
                ),
            ),
            eligibility_result=GateDecision(
                strategy_id=strategy_id,
                passed=True,
                decision_status="accepted",
                reason="validated",
            ),
        )
    )

    eligibility_resp = api_client.get(f"/api/v1/backtests/{backtest.backtest_run_id}/eligibility")
    assert eligibility_resp.status_code == 200
    assert eligibility_resp.json()["decision_status"] == "accepted"

    ingestion_resp = api_client.post(
        "/api/v1/ingestion/jobs",
        json={
            "source_family": "A",
            "source_name": "binance",
            "job_type": "top20_historical_backfill",
            "schedule_mode": "manual",
            "input_window": {
                "requested_at": datetime.now(UTC).isoformat(),
            },
        },
    )
    assert ingestion_resp.status_code == 202
    ingestion_job_id = ingestion_resp.json()["resource_id"]

    ingestion_job = api_client.get(f"/api/v1/ingestion/jobs/{ingestion_job_id}")
    assert ingestion_job.status_code == 200
    assert len(ingestion_job.json()["target_symbols"]) == 20
    assert ingestion_job.json()["target_symbols"][:2] == ["BTC/USDT", "ETH/USDT"]

    paper_resp = api_client.post(
        "/api/v1/execution/paper-runs",
        json={
            "strategy_id": strategy_id,
            "version_id": version_id,
            "gate_decision_ref": backtest.backtest_run_id,
        },
    )
    assert paper_resp.status_code == 202
    paper_run_id = paper_resp.json()["resource_id"]

    paper_run = api_client.get(f"/api/v1/execution/paper-runs/{paper_run_id}")
    assert paper_run.status_code == 200
    body = paper_run.json()
    assert body["candidate_symbols"][:2] == ["BTC/USDT", "ETH/USDT"]
    assert body["gate_decision_ref"] == backtest.backtest_run_id


def test_validation_report_surfaces_promotion_gate_with_hypothesis_context(api_client, db_session) -> None:
    strategy_resp = api_client.post(
        "/api/v1/strategies",
        json={
            "strategy_key": "validation_report_hypothesis",
            "source": "manual",
            "core_thesis": "validation report should include promotion-gate evidence",
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
            title="Validation report hypothesis",
            statement="Report API should reflect complete promotion evidence.",
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
                engine="freqtrade",
                sharpe=1.7,
                deflated_sharpe=1.3,
                profit_factor=1.5,
                max_drawdown=0.1,
                win_rate=0.58,
                expectancy=0.09,
                hypothesis_id=hypothesis.hypothesis_id,
                benchmark_results=[
                    ValidationBenchmarkResult(
                        benchmark_name="passive_hold",
                        benchmark_type="market_baseline",
                        baseline_return=0.01,
                        strategy_return=0.08,
                        excess_return=0.07,
                        passed=True,
                    )
                ],
                validation_windows=[{"window_id": "oos-1", "passed": True, "sharpe": 1.1, "expectancy": 0.04}],
                pod_risk_report=PodRiskReport(
                    pod_id="validation-pod",
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
                reason="validated",
            ),
        )
    )

    report_resp = api_client.get(f"/api/v1/validation/reports/{backtest.backtest_run_id}")

    assert report_resp.status_code == 200
    report = report_resp.json()
    assert report["promotion_gate"]["passed"] is True
    assert report["promotion_gate"]["reason"] == "hypothesis + benchmark + OOS + pod risk evidence complete"


def test_carry_backtest_api_uses_persisted_market_data(api_client, db_session) -> None:
    strategy_resp = api_client.post(
        "/api/v1/strategies",
        json={
            "strategy_key": "carry_api_v1",
            "source": "manual",
            "core_thesis": "funding carry api flow",
            "rules": {
                "entry_rules": {"funding_threshold_bps": 5},
                "exit_rules": {"hold_hours": 8},
                "stoploss_rules": {"basis_bps": 20},
                "takeprofit_rules": {"close_after_windows": 1},
                "position_rules": {"notional_usdt": 1000},
            },
        },
    )
    assert strategy_resp.status_code == 201
    strategy_id = strategy_resp.json()["strategy_id"]

    start = datetime(2024, 1, 1, tzinfo=UTC)
    repo = DataRepository(db_session)
    repo.store_ohlcv_bars(
        [
            {
                "symbol": "BTC/USDT",
                "exchange": "binance",
                "timeframe": "1h",
                "time": start,
                "open": Decimal("42000"),
                "high": Decimal("42000"),
                "low": Decimal("42000"),
                "close": Decimal("42000"),
                "volume": Decimal("50"),
            },
            {
                "symbol": "BTC/USDT",
                "exchange": "binance",
                "timeframe": "1h",
                "time": start + timedelta(hours=8),
                "open": Decimal("42100"),
                "high": Decimal("42100"),
                "low": Decimal("42100"),
                "close": Decimal("42100"),
                "volume": Decimal("50"),
            },
            {
                "symbol": "BTC/USDT",
                "exchange": "binance",
                "timeframe": "1h",
                "time": start + timedelta(hours=16),
                "open": Decimal("42180"),
                "high": Decimal("42180"),
                "low": Decimal("42180"),
                "close": Decimal("42180"),
                "volume": Decimal("50"),
            },
            {
                "symbol": "BTC/USDT:USDT",
                "exchange": "binance",
                "timeframe": "1h",
                "time": start,
                "open": Decimal("42010"),
                "high": Decimal("42010"),
                "low": Decimal("42010"),
                "close": Decimal("42010"),
                "volume": Decimal("50"),
            },
            {
                "symbol": "BTC/USDT:USDT",
                "exchange": "binance",
                "timeframe": "1h",
                "time": start + timedelta(hours=8),
                "open": Decimal("41920"),
                "high": Decimal("41920"),
                "low": Decimal("41920"),
                "close": Decimal("41920"),
                "volume": Decimal("50"),
            },
            {
                "symbol": "BTC/USDT:USDT",
                "exchange": "binance",
                "timeframe": "1h",
                "time": start + timedelta(hours=16),
                "open": Decimal("41840"),
                "high": Decimal("41840"),
                "low": Decimal("41840"),
                "close": Decimal("41840"),
                "volume": Decimal("50"),
            },
        ]
    )
    repo.store_market_extras(
        [
            MarketExtras(symbol="BTC/USDT:USDT", time=start, funding_rate=Decimal("0.0008")),
            MarketExtras(
                symbol="BTC/USDT:USDT",
                time=start + timedelta(hours=8),
                funding_rate=Decimal("0.0007"),
            ),
        ]
    )

    backtest_resp = api_client.post(
        "/api/v1/backtests/carry",
        json={
            "strategy_id": strategy_id,
            "spot_symbol": "BTC/USDT",
            "perp_symbol": "BTC/USDT:USDT",
            "timeframe": "1h",
            "start_at": start.isoformat(),
            "end_at": (start + timedelta(hours=16)).isoformat(),
        },
    )

    assert backtest_resp.status_code == 202
    backtest_run_id = backtest_resp.json()["resource_id"]

    run_resp = api_client.get(f"/api/v1/backtests/{backtest_run_id}")
    assert run_resp.status_code == 200
    body = run_resp.json()
    assert body["eligibility_result"]["decision_status"] == "rejected_with_reason"
    assert body["validation_methodology"]["data_quality"]["gap_check"]["has_gaps"] is False
