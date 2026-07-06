from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from services.data import DataRepository
from services.data.binance import BinanceUniverseSelector


def test_usdm_top20_selector_ranks_by_quote_volume_and_filters_non_usdt() -> None:
    tickers = [
        {"symbol": "ETHUSDT", "quoteVolume": "2000"},
        {"symbol": "BTCUSDT", "quoteVolume": "5000"},
        {"symbol": "USDCUSDT", "quoteVolume": "9000"},
        {"symbol": "BTCDOMUSDT", "quoteVolume": "8000"},
        {"symbol": "SOLUSDT", "quoteVolume": "1000"},
        {"symbol": "ETHBTC", "quoteVolume": "7000"},
    ]

    symbols = BinanceUniverseSelector().select_top_usdm_symbols(tickers, limit=3)

    assert symbols == ["BTC/USDT", "ETH/USDT", "SOL/USDT"]


def test_market_universe_api_falls_back_to_configured_top20(api_client) -> None:
    response = api_client.get("/api/v1/market/universe?limit=5")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 5
    assert body["items"][0]["symbol"] == "BTC/USDT"
    assert body["items"][0]["perp_symbol"] == "BTC/USDT:USDT"
    assert body["items"][0]["source"] in {"binance_usdm_24h_ticker", "fallback_default_top20"}


def test_funding_arbitrage_signal_rejects_negative_net_edge(api_client, db_session) -> None:
    repo = DataRepository(db_session)
    now = datetime.now(UTC).replace(microsecond=0)
    repo.store_ohlcv_bars(
        [
            {
                "symbol": "BTC/USDT",
                "exchange": "binance",
                "timeframe": "1h",
                "time": now,
                "open": Decimal("60000"),
                "high": Decimal("60000"),
                "low": Decimal("60000"),
                "close": Decimal("60000"),
                "volume": Decimal("10"),
            },
            {
                "symbol": "BTC/USDT:USDT",
                "exchange": "binance",
                "timeframe": "1h",
                "time": now,
                "open": Decimal("59940"),
                "high": Decimal("59940"),
                "low": Decimal("59940"),
                "close": Decimal("59940"),
                "volume": Decimal("10"),
            },
        ]
    )
    repo.store_market_extras(
        [
            {
                "symbol": "BTC/USDT:USDT",
                "time": now,
                "funding_rate": Decimal("-0.0002"),
            }
        ]
    )

    response = api_client.get(
        "/api/v1/market/funding-arbitrage-signal"
        "?symbol=BTC/USDT&perp_symbol=BTC/USDT:USDT&fee_bps=8&slippage_bps=6"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["should_enter_paper"] is False
    assert "negative_net_edge" in body["rejection_reasons"]


def test_funding_arbitrage_signal_accepts_positive_funding_after_costs(api_client, db_session) -> None:
    repo = DataRepository(db_session)
    now = datetime.now(UTC).replace(microsecond=0)
    repo.store_ohlcv_bars(
        [
            {
                "symbol": "BTC/USDT",
                "exchange": "binance",
                "timeframe": "1h",
                "time": now,
                "open": Decimal("60000"),
                "high": Decimal("60000"),
                "low": Decimal("60000"),
                "close": Decimal("60000"),
                "volume": Decimal("10"),
            },
            {
                "symbol": "BTC/USDT:USDT",
                "exchange": "binance",
                "timeframe": "1h",
                "time": now,
                "open": Decimal("60120"),
                "high": Decimal("60120"),
                "low": Decimal("60120"),
                "close": Decimal("60120"),
                "volume": Decimal("10"),
            },
        ]
    )
    repo.store_market_extras(
        [
            {
                "symbol": "BTC/USDT:USDT",
                "time": now,
                "funding_rate": Decimal("0.0015"),
            }
        ]
    )

    response = api_client.get(
        "/api/v1/market/funding-arbitrage-signal"
        "?symbol=BTC/USDT&perp_symbol=BTC/USDT:USDT&fee_bps=4&slippage_bps=2"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["should_enter_paper"] is True
    assert body["estimated_net_edge_bps"] > 0
    assert body["recommended_strategy_template"]["source"] == "binance_funding_arbitrage"
