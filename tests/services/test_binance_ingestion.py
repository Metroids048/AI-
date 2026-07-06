from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from services.data.binance import (
    BinanceCcxtClient,
    BinanceUniverseSelector,
    normalize_funding_rate_history,
    normalize_ohlcv_rows,
    normalize_ws_kline_event,
    normalize_ws_mark_price_event,
    platform_symbol_to_binance_raw,
    spot_to_usdm_perp_symbol,
    stream_symbol,
)
from services.data.tasks import enqueue_binance_ingestion


def test_top20_selector_prefers_liquid_usdt_pairs() -> None:
    selector = BinanceUniverseSelector()
    symbols = selector.select_top_symbols(
        {
            "BTC/USDT": {"quoteVolume": 1000},
            "ETH/USDT": {"quoteVolume": 900},
            "BNB/USDT": {"quoteVolume": 800},
            "BTCUP/USDT": {"quoteVolume": 700},
            "USDC/USDT": {"quoteVolume": 600},
        },
        limit=3,
    )
    assert symbols == ["BTC/USDT", "ETH/USDT", "BNB/USDT"]


def test_normalize_ohlcv_rows() -> None:
    rows = [
        [1711929600000, 42000, 42500, 41800, 42300, 123.45],
        [1711933200000, 42300, 42600, 42200, 42550, 100.0],
    ]
    bars = normalize_ohlcv_rows(rows=rows, symbol="BTC/USDT", timeframe="1h")
    assert len(bars) == 2
    assert bars[0].symbol == "BTC/USDT"
    assert bars[0].timestamp == datetime(2024, 4, 1, 0, 0, tzinfo=UTC)


def test_normalize_funding_rate_history() -> None:
    rows = [
        {"timestamp": 1711929600000, "fundingRate": "0.0001"},
        {"timestamp": 1711958400000, "fundingRate": "-0.0002"},
    ]
    extras = normalize_funding_rate_history(rows=rows, symbol="BTC/USDT:USDT")
    assert [str(item.funding_rate) for item in extras] == ["0.0001", "-0.0002"]


def test_binance_symbol_helpers() -> None:
    assert spot_to_usdm_perp_symbol("BTC/USDT") == "BTC/USDT:USDT"
    assert spot_to_usdm_perp_symbol("BTC/USDT:USDT") == "BTC/USDT:USDT"
    assert platform_symbol_to_binance_raw("BTC/USDT:USDT") == "BTCUSDT"
    assert stream_symbol("BTC/USDT:USDT") == "btcusdt"


def test_normalize_ws_kline_only_persists_closed_candles() -> None:
    open_payload = {
        "k": {
            "x": False,
            "t": 1711929600000,
            "o": "42000",
            "h": "42500",
            "l": "41800",
            "c": "42300",
            "v": "12.5",
        }
    }
    closed_payload = {**open_payload, "k": {**open_payload["k"], "x": True}}

    assert normalize_ws_kline_event(open_payload, symbol="BTC/USDT", timeframe="1h") is None
    bar = normalize_ws_kline_event(closed_payload, symbol="BTC/USDT", timeframe="1h")

    assert bar is not None
    assert bar.timestamp == datetime(2024, 4, 1, 0, 0, tzinfo=UTC)
    assert bar.close == Decimal("42300")


def test_normalize_ws_mark_price_extracts_funding_rate() -> None:
    extra = normalize_ws_mark_price_event(
        {"E": 1711929600000, "r": "0.0001"},
        symbol="BTC/USDT:USDT",
    )

    assert extra is not None
    assert extra.symbol == "BTC/USDT:USDT"
    assert extra.funding_rate == Decimal("0.0001")


class _FakeExchange:
    def __init__(self, *, ohlcv_batches=None, funding_batches=None):
        self.ohlcv_batches = list(ohlcv_batches or [])
        self.funding_batches = list(funding_batches or [])
        self.loaded = False

    def load_markets(self):
        self.loaded = True

    def fetch_ohlcv(self, symbol, timeframe, since=None, limit=None):
        return self.ohlcv_batches.pop(0) if self.ohlcv_batches else []

    def fetch_funding_rate_history(self, symbol, since=None, limit=None):
        return self.funding_batches.pop(0) if self.funding_batches else []

    def fetch_order_book(self, symbol, limit=None):
        return {
            "nonce": 123,
            "bids": [[61000, 0.1], [60990, 0.2]],
            "asks": [[61010, 0.15], [61020, 0.25]],
        }

    def fetch_trades(self, symbol, since=None, limit=None):
        return [
            {"id": "1", "timestamp": 1711929600000, "price": 61000, "amount": 0.01, "side": "buy"},
            {"id": "2", "timestamp": 1711929601000, "price": 61001, "amount": 0.02, "side": "sell"},
        ]

    def fapiPublicGetPremiumIndex(self, params):
        return {"symbol": params["symbol"], "time": 1711929600000, "lastFundingRate": "0.0003"}

    def close(self):
        return None


def test_ccxt_client_paginates_ohlcv_and_funding_history() -> None:
    spot = _FakeExchange(
        ohlcv_batches=[
            [
                [1711929600000, 42000, 42500, 41800, 42300, 123.45],
                [1711933200000, 42300, 42600, 42200, 42550, 100.0],
            ],
            [[1711936800000, 42550, 42700, 42400, 42600, 90.0]],
        ]
    )
    usdm = _FakeExchange(
        funding_batches=[
            [
                {"timestamp": 1711929600000, "fundingRate": "0.0001"},
                {"timestamp": 1711958400000, "fundingRate": "-0.0002"},
            ]
        ]
    )
    client = BinanceCcxtClient(spot_exchange=spot, usdm_exchange=usdm)
    start = datetime(2024, 4, 1, 0, 0, tzinfo=UTC)
    end = datetime(2024, 4, 1, 2, 0, tzinfo=UTC)

    bars = client.fetch_ohlcv_history(
        symbol="BTC/USDT",
        timeframe="1h",
        start_at=start,
        end_at=end,
        limit=2,
    )
    funding = client.fetch_funding_rate_history(
        symbol="BTC/USDT:USDT",
        start_at=start,
        end_at=datetime(2024, 4, 1, 8, 0, tzinfo=UTC),
        limit=2,
    )

    assert [item.close for item in bars] == [Decimal("42300"), Decimal("42550"), Decimal("42600")]
    assert [item.funding_rate for item in funding] == [Decimal("0.0001"), Decimal("-0.0002")]


def test_ccxt_client_fetches_live_order_book_trades_and_premium_index() -> None:
    exchange = _FakeExchange()
    client = BinanceCcxtClient(spot_exchange=exchange, usdm_exchange=exchange)

    book = client.fetch_live_order_book(symbol="BTC/USDT:USDT", limit=2)
    trades = client.fetch_live_trades(symbol="BTC/USDT:USDT", limit=2)
    premium = client.fetch_premium_index(symbol="BTC/USDT:USDT")

    assert book.data_status == "ok"
    assert book.source == "binance_public_rest"
    assert book.last_update_id == 123
    assert book.bids[1].total == Decimal("0.3")
    assert trades.data_status == "ok"
    assert trades.trades[1].side == "sell"
    assert premium is not None
    assert premium.funding_rate == Decimal("0.0003")


class _FakeBackfillClient:
    def fetch_ohlcv_history(self, *, symbol, timeframe, start_at, end_at):
        return normalize_ohlcv_rows(
            rows=[[1711929600000, 42000, 42500, 41800, 42300, 123.45]],
            symbol=symbol,
            timeframe=timeframe,
        )

    def fetch_funding_rate_history(self, *, symbol, start_at, end_at):
        return normalize_funding_rate_history(
            rows=[{"timestamp": 1711929600000, "fundingRate": "0.0001"}],
            symbol=symbol,
        )


def test_binance_ingestion_task_writes_ohlcv_and_funding(api_client) -> None:
    payload = {
        "source_family": "A",
        "source_name": "binance",
        "job_type": "binance_ohlcv_backfill",
        "schedule_mode": "manual_backfill",
        "target_symbols": ["BTC/USDT", "BTC/USDT:USDT"],
        "input_window": {
            "timeframe": "1h",
            "start_at": "2024-04-01T00:00:00+00:00",
            "end_at": "2024-04-01T01:00:00+00:00",
        },
    }
    result = enqueue_binance_ingestion(payload, client=_FakeBackfillClient())

    assert result["job_status"] == "succeeded"
    assert result["execution_summary"]["rows_written_total"] == 2

    funding_result = enqueue_binance_ingestion(
        {
            **payload,
            "job_type": "binance_funding_backfill",
            "target_symbols": ["BTC/USDT:USDT"],
        },
        client=_FakeBackfillClient(),
    )
    assert funding_result["job_status"] == "succeeded"
    assert funding_result["execution_summary"]["rows_written_total"] == 1

    response = api_client.get(
        "/api/v1/market/snapshot",
        params={"symbol": "BTC/USDT", "perp_symbol": "BTC/USDT:USDT", "timeframe": "1h"},
    )
    assert response.status_code == 200
    assert response.json()["data_status"] in {"ok", "stale"}
    assert response.json()["funding_rate"] == "0.0001"
