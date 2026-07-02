from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from shared.models import MarketExtras
from services.data import DataRepository


def test_strategy_to_backtest_to_paper_vertical_slice(api_client) -> None:
    idea_resp = api_client.post(
        "/strategies/ideas",
        json={
            "title": "Binance carry idea",
            "source": "manual_note",
            "hypothesis_summary": "Funding windows can drive BTC/ETH carry trades.",
            "symbol_scope": ["BTC/USDT", "ETH/USDT"],
        },
    )
    assert idea_resp.status_code == 201
    idea_id = idea_resp.json()["idea_id"]

    draft_resp = api_client.post(f"/strategies/ideas/{idea_id}/drafts")
    assert draft_resp.status_code == 201
    draft_id = draft_resp.json()["draft_id"]

    strategy_resp = api_client.post(f"/strategies/{draft_id}/materialize")
    assert strategy_resp.status_code == 201
    strategy_id = strategy_resp.json()["strategy_id"]

    version_resp = api_client.post(
        "/strategies/versions",
        json={
            "strategy_id": strategy_id,
            "version_label": "v1",
            "change_summary": "initial persisted version",
        },
    )
    assert version_resp.status_code == 201
    version_id = version_resp.json()["version_id"]

    backtest_resp = api_client.post(
        "/backtests",
        json={
            "strategy_id": strategy_id,
            "version_id": version_id,
            "execution_engine": "freqtrade",
            "sample_split_plan": {"train": "2024Q1", "oos": "2024Q2"},
            "validation_methodology": {"lane": "carry_research"},
            "cost_model_ref": "spot hedge reconciliation performed platform-side",
            "stress_test_scenarios": ["funding_flip", "spread_widening"],
            "metrics_summary": {
                "strategy_id": strategy_id,
                "engine": "freqtrade",
                "sharpe": 1.5,
                "profit_factor": 1.4,
                "max_drawdown": 0.12,
                "win_rate": 0.57,
                "expectancy": 0.1,
                "total_cost_bps": 14.0,
            },
            "eligibility_result": {
                "strategy_id": strategy_id,
                "passed": True,
                "decision_status": "conditional",
                "reason": "deflated sharpe pending",
                "deflated_sharpe_applied": False,
            },
        },
    )
    assert backtest_resp.status_code == 201
    backtest_id = backtest_resp.json()["backtest_run_id"]

    eligibility_resp = api_client.get(f"/backtests/{backtest_id}/eligibility")
    assert eligibility_resp.status_code == 200
    assert eligibility_resp.json()["decision_status"] == "conditional"

    ingestion_resp = api_client.post(
        "/ingestion/jobs",
        json={
            "source_family": "A",
            "source_name": "binance",
            "job_type": "top20_historical_backfill",
            "schedule_mode": "manual",
            "input_window": {
                "requested_at": datetime.now(timezone.utc).isoformat(),
            },
        },
    )
    assert ingestion_resp.status_code == 201
    assert len(ingestion_resp.json()["target_symbols"]) == 20
    assert ingestion_resp.json()["target_symbols"][:2] == ["BTC/USDT", "ETH/USDT"]

    paper_resp = api_client.post(
        "/paper-runs",
        json={
            "strategy_id": strategy_id,
            "version_id": version_id,
            "gate_decision_ref": backtest_id,
        },
    )
    assert paper_resp.status_code == 201
    body = paper_resp.json()
    assert body["candidate_symbols"][:2] == ["BTC/USDT", "ETH/USDT"]
    assert body["gate_decision_ref"] == backtest_id


def test_carry_backtest_api_uses_persisted_market_data(api_client, db_session) -> None:
    strategy_resp = api_client.post(
        "/strategies",
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

    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
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
        "/backtests/carry",
        json={
            "strategy_id": strategy_id,
            "spot_symbol": "BTC/USDT",
            "perp_symbol": "BTC/USDT:USDT",
            "timeframe": "1h",
            "start_at": start.isoformat(),
            "end_at": (start + timedelta(hours=16)).isoformat(),
        },
    )

    assert backtest_resp.status_code == 201
    body = backtest_resp.json()
    assert body["eligibility_result"]["decision_status"] == "conditional"
    assert body["validation_methodology"]["data_quality"]["gap_check"]["has_gaps"] is False
