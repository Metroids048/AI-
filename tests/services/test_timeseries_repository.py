from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from shared.models import MarketExtras, OHLCVBar
from services.data.repository import DataRepository


def _bar(symbol: str, at: datetime, close: str) -> OHLCVBar:
    return OHLCVBar(
        symbol=symbol,
        exchange="binance",
        timeframe="1h",
        time=at,
        open=Decimal(close),
        high=Decimal(close),
        low=Decimal(close),
        close=Decimal(close),
        volume=Decimal("10"),
    )


def test_timeseries_repository_store_and_query(db_session) -> None:
    repo = DataRepository(db_session)
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    bars = [
        _bar("BTC/USDT", start, "42000"),
        _bar("BTC/USDT", start + timedelta(hours=1), "42100"),
    ]
    extras = [
        MarketExtras(symbol="BTC/USDT:USDT", time=start, funding_rate=Decimal("0.0008")),
    ]

    assert repo.store_ohlcv_bars(bars) == 2
    assert repo.store_market_extras(extras) == 1

    loaded_bars = repo.list_ohlcv_bars(symbol="BTC/USDT", timeframe="1h")
    loaded_extras = repo.list_market_extras(symbol="BTC/USDT:USDT")

    assert [bar.symbol for bar in loaded_bars] == ["BTC/USDT", "BTC/USDT"]
    assert str(loaded_extras[0].funding_rate) == "0.0008"


def test_gap_and_freshness_checks(db_session) -> None:
    repo = DataRepository(db_session)
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    repo.store_ohlcv_bars(
        [
            _bar("ETH/USDT", start, "2300"),
            _bar("ETH/USDT", start + timedelta(hours=2), "2310"),
        ]
    )

    gap = repo.check_gaps(
        symbol="ETH/USDT",
        timeframe="1h",
        start_at=start,
        end_at=start + timedelta(hours=2),
    )
    freshness = repo.check_freshness(
        symbol="ETH/USDT",
        timeframe="1h",
        reference_time=start + timedelta(hours=3),
        max_delay=timedelta(hours=2),
    )

    assert gap["has_gaps"] is True
    assert gap["missing_timestamps"] == [start + timedelta(hours=1)]
    assert freshness["is_fresh"] is True
