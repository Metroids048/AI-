from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from services.data import DataRepository
from services.strategy_library import StrategyRepository, ValidationRepository
from shared.models import BacktestReport, BacktestRun, GateDecision, StrategyCreate, StrategyRules


def test_risk_event_rejects_execution_and_review_writeback(api_client, db_session) -> None:
    strategy = StrategyRepository(db_session).create_strategy(
        StrategyCreate(
            strategy_key="gatekeeper_strategy",
            source="manual",
            core_thesis="gatekeeper",
            rules=StrategyRules(stoploss_rules={"basis_bps": 20}),
        )
    )
    backtest = ValidationRepository(db_session).create_backtest_run(
        BacktestRun(
            strategy_id=strategy.strategy_id,
            execution_engine="freqtrade",
            metrics_summary=BacktestReport(
                strategy_id=strategy.strategy_id,
                engine="freqtrade",
                sharpe=1.4,
                profit_factor=1.35,
                max_drawdown=0.10,
                win_rate=0.52,
                expectancy=0.08,
            ),
            eligibility_result=GateDecision(
                strategy_id=strategy.strategy_id,
                passed=True,
                decision_status="accepted",
                reason="ready for paper",
            ),
        )
    )

    now = datetime.now(UTC).replace(microsecond=0)
    DataRepository(db_session).store_ohlcv_bars(
        [
            {
                "symbol": "BTC/USDT",
                "exchange": "binance",
                "timeframe": "1h",
                "time": now - timedelta(minutes=30),
                "open": Decimal("60000"),
                "high": Decimal("60100"),
                "low": Decimal("59900"),
                "close": Decimal("60050"),
                "volume": Decimal("12"),
            }
        ]
    )

    profile_resp = api_client.post("/api/v1/risk/profiles", json={})
    assert profile_resp.status_code == 201
    risk_profile_id = profile_resp.json()["risk_profile_id"]

    risk_event_resp = api_client.post(
        "/api/v1/risk/events",
        json={
            "event_type": "exchange_incident",
            "severity": "high",
            "source": "binance_status",
            "description": "binance degradation",
            "affected_scope": ["BTC/USDT"],
            "resolution_status": "detected",
            "occurred_at": now.isoformat(),
            "expires_at": (now + timedelta(hours=1)).isoformat(),
        },
    )
    assert risk_event_resp.status_code == 201

    order_resp = api_client.post(
        "/api/v1/execution/orders",
        json={
            "strategy_id": strategy.strategy_id,
            "symbol": "BTC/USDT",
            "direction": "long",
            "entry_context": {"timeframe": "1h"},
            "stoploss_plan": {"price": 59000},
            "takeprofit_plan": {"price": 62000},
            "validation_backtest_run_id": backtest.backtest_run_id,
            "risk_profile_id": risk_profile_id,
            "risk_state": {
                "account_equity": 10000,
                "equity_peak": 10000,
                "daily_realized_pnl": 0,
                "weekly_realized_pnl": 0,
                "consecutive_losses": 0,
                "api_failures_window": 0,
                "open_positions": 0,
                "symbol_exposure": 0,
                "total_exposure": 0,
                "requested_notional": 100,
                "requested_leverage": 1,
            },
        },
    )
    assert order_resp.status_code == 201
    assert order_resp.json()["execution_status"] == "rejected"
    assert "blocking_risk_event" in order_resp.json()["rejection_reason"]

    failure_resp = api_client.post(
        "/api/v1/failures",
        json={
            "strategy_id": strategy.strategy_id,
            "origin_run_type": "paper",
            "origin_run_id": "paper-run-1",
            "failure_type": "risk_gate_reject",
            "failure_summary": "Rejected because of blocking risk event",
            "recommended_change": "Wait for exchange health recovery",
        },
    )
    assert failure_resp.status_code == 201

    strategy_resp = api_client.get(f"/api/v1/strategies/{strategy.strategy_id}")
    assert strategy_resp.status_code == 200
    assert "Rejected because of blocking risk event" in strategy_resp.json()["failure_reasons"]

    review_resp = api_client.post(f"/api/v1/reviews/daily/{datetime.now(UTC).date().isoformat()}")
    assert review_resp.status_code == 201
    assert "Rejected because of blocking risk event" in review_resp.json()["deviation_analysis"]


def test_research_agent_scans_local_alpha_and_persists_ideas(api_client, tmp_path) -> None:
    alpha_root = tmp_path / "alpha"
    alpha_root.mkdir()
    (alpha_root / "alpha_candidates.jsonl").write_text(
        "\n".join(
            [
                '{"expression":"rank(close/delay(close,5))","family":"momentum","risk_adjusted_score":0.42}',
                '{"expression":"ts_mean(volume,20)","family":"volume","risk_adjusted_score":0.21}',
            ]
        ),
        encoding="utf-8",
    )

    submit_resp = api_client.post(
        "/api/v1/agents/tasks",
        json={
            "agent_type": "research_agent",
            "task_type": "scan_local_alpha",
            "input_payload": {
                "alpha_root": str(alpha_root),
                "limit": 2,
                "persist_ideas": True,
            },
        },
    )
    assert submit_resp.status_code == 202
    task_id = submit_resp.json()["resource_id"]

    task_resp = api_client.get(f"/api/v1/agents/tasks/{task_id}")
    assert task_resp.status_code == 200
    assert task_resp.json()["task_status"] == "completed"
    assert task_resp.json()["output_payload"]["idea_count"] == 2

    ideas_resp = api_client.get("/api/v1/strategies/ideas")
    assert ideas_resp.status_code == 200
    assert ideas_resp.json()["total"] == 2


def test_research_agent_requires_explicit_alpha_root(api_client, monkeypatch) -> None:
    from apps.api.config import settings

    monkeypatch.setattr(settings, "worldquant_alpha_local_path", "")

    submit_resp = api_client.post(
        "/api/v1/agents/tasks",
        json={
            "agent_type": "research_agent",
            "task_type": "scan_local_alpha",
            "input_payload": {"persist_ideas": True},
        },
    )
    assert submit_resp.status_code == 202
    task_id = submit_resp.json()["resource_id"]

    task_resp = api_client.get(f"/api/v1/agents/tasks/{task_id}")
    assert task_resp.status_code == 200
    assert task_resp.json()["task_status"] == "failed"
    assert task_resp.json()["output_payload"]["message"] == "alpha_root is required"


def test_research_agent_writes_alpha_rejections_to_review_memory(api_client, tmp_path) -> None:
    alpha_root = tmp_path / "alpha"
    alpha_root.mkdir()
    (alpha_root / "alpha_candidates.jsonl").write_text(
        "\n".join(
            [
                '{"expression":"rank(close)-rank(volume)","family":"momentum","risk_adjusted_score":0.42}',
                '{"expression":"ts_delta(capex_to_total_assets,252)","family":"fundamental","risk_adjusted_score":0.91}',
            ]
        ),
        encoding="utf-8",
    )

    submit_resp = api_client.post(
        "/api/v1/agents/tasks",
        json={
            "agent_type": "research_agent",
            "task_type": "scan_local_alpha",
            "input_payload": {
                "alpha_root": str(alpha_root),
                "limit": 2,
                "persist_ideas": True,
            },
        },
    )
    assert submit_resp.status_code == 202
    task_id = submit_resp.json()["resource_id"]

    task_resp = api_client.get(f"/api/v1/agents/tasks/{task_id}")
    assert task_resp.status_code == 200
    persisted_ids = task_resp.json()["output_payload"]["persisted_idea_ids"]
    assert len(persisted_ids) == 2

    failures_resp = api_client.get("/api/v1/failures?failure_type=alpha_evaluator_reject")
    assert failures_resp.status_code == 200
    failures = failures_resp.json()["items"]
    assert len(failures) == 1
    assert failures[0]["strategy_id"] is None
    assert failures[0]["idea_id"] in persisted_ids
    assert "capex_to_total_assets" in failures[0]["failure_summary"]

    idea_failure_resp = api_client.get(f"/api/v1/failures?idea_id={failures[0]['idea_id']}")
    assert idea_failure_resp.status_code == 200
    assert idea_failure_resp.json()["total"] == 1
