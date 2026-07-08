from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from apps.api.config import settings
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


def test_exchange_stream_event_builders_handle_realtime_payloads() -> None:
    from apps.api.routers.market import _exchange_stream_event_from_binance_payload

    kline = _exchange_stream_event_from_binance_payload(
        {
            "data": {
                "e": "kline",
                "k": {
                    "t": 1711929600000,
                    "o": "61000",
                    "h": "61200",
                    "l": "60900",
                    "c": "61100",
                    "v": "10",
                    "x": False,
                },
            }
        },
        symbol="BTC/USDT",
        perp_symbol="BTC/USDT:USDT",
        timeframe="1m",
    )
    book = _exchange_stream_event_from_binance_payload(
        {"data": {"lastUpdateId": 123, "bids": [["61000", "0.1"]], "asks": [["61010", "0.2"]]}},
        symbol="BTC/USDT",
        perp_symbol="BTC/USDT:USDT",
        timeframe="1m",
    )
    trade = _exchange_stream_event_from_binance_payload(
        {"data": {"e": "trade", "t": 1, "p": "61005", "q": "0.01", "T": 1711929600000, "m": False}},
        symbol="BTC/USDT",
        perp_symbol="BTC/USDT:USDT",
        timeframe="1m",
    )

    assert kline is not None
    assert kline["event"] == "kline"
    assert kline["payload"]["closed"] is False
    assert kline["payload"]["close"] == "61100"
    assert book is not None
    assert book["event"] == "order_book"
    assert book["payload"]["source"] == "binance_public_ws"
    assert book["payload"]["bids"][0]["total"] == "0.1"
    assert trade is not None
    assert trade["event"] == "trade"
    assert trade["payload"]["side"] == "buy"


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


def test_market_live_public_rest_endpoints_return_binance_source(api_client, db_session, monkeypatch) -> None:
    from apps.api.config import settings
    from apps.api.routers import market as market_router
    from services.data.binance import normalize_ohlcv_rows
    from shared.models import MarketExtras, MarketOrderBookResponse, MarketTrade, MarketTradesResponse, OrderBookLevel

    class _FakeLiveClient:
        def fetch_recent_ohlcv(self, *, symbol, timeframe, limit=300):
            return normalize_ohlcv_rows(
                rows=[[1711929600000, 61000, 61200, 60900, 61100, 10]],
                symbol=symbol,
                timeframe=timeframe,
            )

        def fetch_premium_index(self, *, symbol):
            return MarketExtras(symbol=symbol, time=datetime.now(UTC), funding_rate=Decimal("0.0002"))

        def fetch_live_order_book(self, *, symbol, limit=20):
            return MarketOrderBookResponse(
                symbol=symbol,
                data_status="ok",
                source="binance_public_rest",
                bids=[OrderBookLevel(price=Decimal("61000"), quantity=Decimal("0.1"), total=Decimal("0.1"))],
                asks=[OrderBookLevel(price=Decimal("61010"), quantity=Decimal("0.2"), total=Decimal("0.2"))],
            )

        def fetch_live_trades(self, *, symbol, limit=50):
            return MarketTradesResponse(
                symbol=symbol,
                data_status="ok",
                source="binance_public_rest",
                trades=[MarketTrade(trade_id="1", price=Decimal("61000"), quantity=Decimal("0.01"), side="buy")],
            )

    monkeypatch.setattr(settings, "binance_live_market_enabled", True)
    monkeypatch.setattr(market_router, "BinanceCcxtClient", _FakeLiveClient)

    candles = api_client.get("/api/v1/market/ohlcv", params={"symbol": "BTC/USDT", "timeframe": "1h"})
    snapshot = api_client.get("/api/v1/market/snapshot", params={"symbol": "BTC/USDT", "perp_symbol": "BTC/USDT:USDT"})
    book = api_client.get("/api/v1/market/order-book", params={"symbol": "BTC/USDT:USDT"})
    trades = api_client.get("/api/v1/market/trades", params={"symbol": "BTC/USDT:USDT"})

    assert candles.status_code == 200
    assert candles.json()["source"] == "binance_public_rest"
    assert candles.json()["candles"][0]["close"] == "61100"
    assert snapshot.status_code == 200
    assert snapshot.json()["funding_rate"] == "0.0002"
    assert book.json()["source"] == "binance_public_rest"
    assert book.json()["bids"][0]["price"] == "61000"
    assert trades.json()["source"] == "binance_public_rest"
    assert trades.json()["trades"][0]["side"] == "buy"


def test_market_ohlcv_websocket_stream_sends_persisted_snapshot(api_client, db_session) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    DataRepository(db_session).store_ohlcv_bars([_bar("BTC/USDT", now, "42100")])

    with api_client.websocket_connect(
        f"/api/v1/market/ohlcv/stream?symbol=BTC/USDT&timeframe=1h&limit=5&token={settings.admin_api_token}"
    ) as websocket:
        message = websocket.receive_json()

    assert message["event"] == "ohlcv_snapshot"
    assert message["source"] == "persisted_market_data"
    assert message["feed_status"]["status"] in {"idle", "live", "reconnecting"}
    assert message["payload"]["data_status"] == "ok"
    assert message["payload"]["candles"][0]["close"] == "42100"


def test_exchange_websocket_stream_sends_initial_terminal_snapshot(api_client, db_session) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    DataRepository(db_session).store_ohlcv_bars(
        [
            _bar("BTC/USDT", now, "42100"),
            _bar("BTC/USDT:USDT", now, "42150"),
        ]
    )

    with api_client.websocket_connect(
        f"/api/v1/market/exchange-stream?symbol=BTC/USDT&perp_symbol=BTC/USDT:USDT&timeframe=1h&limit=5&token={settings.admin_api_token}"
    ) as websocket:
        message = websocket.receive_json()

    assert message["event"] == "exchange_snapshot"
    assert message["payload"]["symbol"] == "BTC/USDT"
    assert message["payload"]["perp_symbol"] == "BTC/USDT:USDT"
    assert message["payload"]["ohlcv"]["data_status"] == "ok"
    assert message["payload"]["ohlcv"]["candles"][0]["close"] == "42100"
    assert message["payload"]["feed_status"]["source"] == "rest_polling"


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
