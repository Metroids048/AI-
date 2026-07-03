from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from services.data import DataRepository
from services.strategy_library import (
    ExecutionRepository,
    PaperRunRepository,
    ValidationRepository,
)
from shared.models import (
    BacktestReport,
    BacktestRun,
    GateDecision,
    MarketExtras,
    OrderExecution,
    PaperRun,
    PositionSnapshot,
    RiskEvent,
    TradeSide,
)


def _bar(symbol: str, at: datetime, close: str) -> dict:
    return {
        "symbol": symbol,
        "exchange": "binance",
        "timeframe": "1h",
        "time": at,
        "open": Decimal(close),
        "high": Decimal(close),
        "low": Decimal(close),
        "close": Decimal(close),
        "volume": Decimal("100"),
    }


def test_market_snapshot_and_ohlcv_read_persisted_data(api_client, db_session) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    DataRepository(db_session).store_ohlcv_bars(
        [
            _bar("BTC/USDT", now - timedelta(hours=1), "42000"),
            _bar("BTC/USDT", now, "42100"),
            _bar("BTC/USDT:USDT", now - timedelta(hours=1), "42050"),
            _bar("BTC/USDT:USDT", now, "42205.25"),
        ]
    )
    DataRepository(db_session).store_market_extras(
        [MarketExtras(symbol="BTC/USDT:USDT", time=now, funding_rate=Decimal("0.0008"))]
    )

    snapshot = api_client.get(
        "/api/v1/market/snapshot",
        params={"symbol": "BTC/USDT", "perp_symbol": "BTC/USDT:USDT"},
    )
    assert snapshot.status_code == 200
    body = snapshot.json()
    assert body["data_status"] == "ok"
    assert body["spot_last_price"] == "42100"
    assert body["perp_last_price"] == "42205.25"
    assert body["funding_rate"] == "0.0008"
    assert body["basis_bps"] > 20

    candles = api_client.get(
        "/api/v1/market/ohlcv",
        params={"symbol": "BTC/USDT", "timeframe": "1h", "limit": 1},
    )
    assert candles.status_code == 200
    assert candles.json()["data_status"] == "ok"
    assert len(candles.json()["candles"]) == 1
    assert candles.json()["candles"][0]["close"] == "42100"


def test_market_ohlcv_empty_state_is_explicit(api_client) -> None:
    response = api_client.get("/api/v1/market/ohlcv", params={"symbol": "BTC/USDT"})

    assert response.status_code == 200
    assert response.json()["data_status"] == "empty"
    assert response.json()["candles"] == []


def test_console_overview_aggregates_execution_and_risk_state(api_client, db_session) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    data_repo = DataRepository(db_session)
    data_repo.store_ohlcv_bars(
        [
            _bar("BTC/USDT", now, "42000"),
            _bar("BTC/USDT:USDT", now, "42084"),
        ]
    )
    data_repo.store_market_extras([MarketExtras(symbol="BTC/USDT:USDT", time=now, funding_rate=Decimal("0.0007"))])
    data_repo.store_risk_event(
        RiskEvent(
            event_type="exchange_incident",
            severity="high",
            source="manual",
            description="binance degradation",
            affected_scope=["BTC/USDT"],
            expires_at=now + timedelta(hours=1),
        )
    )

    backtest = ValidationRepository(db_session).create_backtest_run(
        BacktestRun(
            strategy_id="strategy-console",
            execution_engine="freqtrade",
            metrics_summary=BacktestReport(
                strategy_id="strategy-console",
                engine="freqtrade",
                sharpe=1.2,
                profit_factor=1.4,
                max_drawdown=0.12,
                win_rate=0.55,
                expectancy=0.04,
            ),
            eligibility_result=GateDecision(
                strategy_id="strategy-console",
                passed=True,
                decision_status="conditional",
            ),
        )
    )
    paper_run = PaperRunRepository(db_session).create_paper_run(
        PaperRun(strategy_id="strategy-console", gate_decision_ref=backtest.backtest_run_id)
    )
    execution_repo = ExecutionRepository(db_session)
    execution_repo.create_order(
        OrderExecution(
            strategy_id="strategy-console",
            symbol="BTC/USDT",
            direction=TradeSide.LONG,
            execution_status="rejected",
            rejection_reason="blocking_risk_event",
            paper_run_id=paper_run.paper_run_id,
        )
    )
    execution_repo.create_position_snapshot(
        PositionSnapshot(
            run_type="paper",
            run_id=paper_run.paper_run_id or "paper-run",
            symbol="BTC/USDT",
            side=TradeSide.LONG,
            quantity=0.1,
            entry_price=42000,
            mark_price=42100,
            unrealized_pnl=10,
            snapshot_time=now,
        )
    )

    response = api_client.get("/api/v1/console/overview")

    assert response.status_code == 200
    body = response.json()
    assert body["global_risk_status"] == "blocked"
    assert body["market"]["data_status"] == "ok"
    assert body["latest_backtests"][0]["backtest_run_id"] == backtest.backtest_run_id
    assert body["paper_runs"][0]["paper_run_id"] == paper_run.paper_run_id
    assert body["orders"][0]["rejection_reason"] == "blocking_risk_event"
    assert body["positions"][0]["unrealized_pnl"] == 10
    assert body["risk_events"][0]["severity"] == "high"


def test_console_control_status_updates(api_client, db_session) -> None:
    paper_run = PaperRunRepository(db_session).create_paper_run(
        PaperRun(strategy_id="strategy-controls", paper_status="queued")
    )
    risk_event = DataRepository(db_session).store_risk_event(
        RiskEvent(
            event_type="exchange_incident",
            severity="high",
            source="manual",
            description="operator acknowledgement test",
        )
    )

    pause_response = api_client.patch(
        f"/api/v1/execution/paper-runs/{paper_run.paper_run_id}/status",
        json={"paper_status": "paused"},
    )
    assert pause_response.status_code == 200
    assert pause_response.json()["paper_status"] == "paused"

    risk_response = api_client.patch(
        f"/api/v1/risk/events/{risk_event.risk_event_id}/resolution",
        json={"resolution_status": "acknowledged"},
    )
    assert risk_response.status_code == 200
    assert risk_response.json()["resolution_status"] == "acknowledged"
