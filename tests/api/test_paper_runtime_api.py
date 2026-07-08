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
    PodRiskReport,
    ValidationBenchmarkResult,
)


def _create_validated_paper_run(api_client, db_session) -> tuple[str, str]:
    strategy_resp = api_client.post(
        "/api/v1/strategies",
        json={
            "strategy_key": "paper_runtime_trend",
            "source": "open_source:freqtrade",
            "core_thesis": "runtime should auto-monitor candidate symbols and keep paper trading inside gates",
            "rules": {
                "entry_rules": {"ema_fast": 20, "ema_slow": 50, "macd_confirmation": True},
                "exit_rules": {"max_hold_bars": 48},
                "stoploss_rules": {"atr_multiple": 2},
                "takeprofit_rules": {"risk_reward": 2},
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
                engine="freqtrade",
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
    start_at = datetime.now(UTC).replace(microsecond=0) - timedelta(hours=1)
    _store_trend_bars(db_session, symbol="BTC/USDT", closes=[Decimal("60000"), Decimal("60400")], start_at=start_at)
    _store_trend_bars(db_session, symbol="ETH/USDT", closes=[Decimal("3000"), Decimal("2950")], start_at=start_at)

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
    _, paper_run_id = _create_validated_paper_run(api_client, db_session)
    start_at = datetime.now(UTC).replace(microsecond=0) - timedelta(hours=1)
    _store_trend_bars(db_session, symbol="BTC/USDT", closes=[Decimal("60000"), Decimal("60400")], start_at=start_at)
    status_resp = api_client.patch(
        f"/api/v1/execution/paper-runs/{paper_run_id}/status",
        json={"paper_status": "running"},
    )
    assert status_resp.status_code == 200

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


def test_paper_runtime_auto_cycle_closes_position_on_opposite_signal(api_client, db_session) -> None:
    _, paper_run_id = _create_validated_paper_run(api_client, db_session)
    start_at = datetime.now(UTC).replace(microsecond=0) - timedelta(hours=2)
    _store_trend_bars(db_session, symbol="BTC/USDT", closes=[Decimal("60000"), Decimal("60400")], start_at=start_at)

    first_cycle = api_client.post(
        f"/api/v1/execution/paper-runs/{paper_run_id}/auto-cycle",
        json={"symbols": ["BTC/USDT"], "max_symbols": 1, "timeframe": "1h", "enable_decision_veto": False},
    )
    assert first_cycle.status_code == 200
    assert first_cycle.json()["opened_positions"] == 1

    _store_trend_bars(
        db_session,
        symbol="BTC/USDT",
        closes=[Decimal("60400"), Decimal("59800")],
        start_at=datetime.now(UTC).replace(microsecond=0) - timedelta(hours=1),
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
