from __future__ import annotations

from datetime import datetime, timezone

from services.data.binance import (
    BinanceUniverseSelector,
    normalize_funding_rate_history,
    normalize_ohlcv_rows,
)


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
    assert bars[0].timestamp == datetime(2024, 4, 1, 0, 0, tzinfo=timezone.utc)


def test_normalize_funding_rate_history() -> None:
    rows = [
        {"timestamp": 1711929600000, "fundingRate": "0.0001"},
        {"timestamp": 1711958400000, "fundingRate": "-0.0002"},
    ]
    extras = normalize_funding_rate_history(rows=rows, symbol="BTC/USDT:USDT")
    assert [str(item.funding_rate) for item in extras] == ["0.0001", "-0.0002"]
