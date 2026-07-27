from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from services.data.repository import DataRepository
from shared.models import Exchange, MarketExtras, OHLCVBar, Timeframe


def _bar(symbol: str, at: datetime, close: str, *, timeframe: str = "1h") -> OHLCVBar:
    return OHLCVBar(
        symbol=symbol,
        exchange=Exchange.BINANCE,
        timeframe=Timeframe(timeframe),
        time=at,
        open=Decimal(close),
        high=Decimal(close),
        low=Decimal(close),
        close=Decimal(close),
        volume=Decimal("10"),
    )


def test_timeseries_repository_store_and_query(db_session) -> None:
    repo = DataRepository(db_session)
    start = datetime(2024, 1, 1, tzinfo=UTC)
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


def test_timeseries_repository_upserts_duplicate_market_rows(db_session) -> None:
    repo = DataRepository(db_session)
    start = datetime(2024, 1, 1, tzinfo=UTC)

    repo.store_ohlcv_bars([_bar("BTC/USDT", start, "42000")])
    repo.store_ohlcv_bars([_bar("BTC/USDT", start, "42100")])
    repo.store_market_extras([MarketExtras(symbol="BTC/USDT:USDT", time=start, funding_rate=Decimal("0.0008"))])
    repo.store_market_extras([MarketExtras(symbol="BTC/USDT:USDT", time=start, funding_rate=Decimal("0.0009"))])

    loaded_bars = repo.list_ohlcv_bars(symbol="BTC/USDT", timeframe="1h")
    loaded_extras = repo.list_market_extras(symbol="BTC/USDT:USDT")

    assert len(loaded_bars) == 1
    assert loaded_bars[0].close == Decimal("42100")
    assert len(loaded_extras) == 1
    assert loaded_extras[0].funding_rate == Decimal("0.0009")


def test_gap_and_freshness_checks(db_session) -> None:
    repo = DataRepository(db_session)
    start = datetime(2024, 1, 1, tzinfo=UTC)
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


def test_freshness_uses_closed_candle_time_not_open_time(db_session) -> None:
    repo = DataRepository(db_session)
    opened_at = datetime(2026, 7, 21, 1, 0, tzinfo=UTC)
    repo.store_ohlcv_bars([_bar("BTC/USDT", opened_at, "118000", timeframe="15m")])

    freshness = repo.check_freshness(
        symbol="BTC/USDT",
        timeframe="15m",
        reference_time=opened_at + timedelta(minutes=15, seconds=5),
        max_delay=timedelta(seconds=10),
    )

    assert freshness["is_fresh"] is True
    assert freshness["latest_open_time"] == opened_at
    assert freshness["latest_close_time"] == opened_at + timedelta(minutes=15)
    assert freshness["delay_seconds"] == 5.0


def test_latest_closed_bar_excludes_the_current_open_candle(db_session) -> None:
    repo = DataRepository(db_session)
    opened_at = datetime(2026, 7, 27, 10, 0, tzinfo=UTC)
    repo.store_ohlcv_bars(
        [
            _bar("BTC/USDT", opened_at, "65000", timeframe="15m"),
            _bar("BTC/USDT", opened_at + timedelta(minutes=15), "65100", timeframe="15m"),
        ]
    )

    latest = repo.get_latest_closed_ohlcv_bar(
        symbol="BTC/USDT",
        timeframe="15m",
        reference_time=opened_at + timedelta(minutes=20),
    )

    assert latest is not None
    assert latest.timestamp == opened_at
