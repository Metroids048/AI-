from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import create_engine, inspect

from scripts.backfill_strategy_refactor_history import (
    StrategyHistoryBackfiller,
    initialize_timeseries_database,
    iter_series_chunks,
)
from shared.models import Exchange, OHLCVBar, Timeframe


def _bar(symbol: str, timeframe: str, timestamp: datetime) -> OHLCVBar:
    return OHLCVBar(
        symbol=symbol,
        exchange=Exchange.BINANCE,
        timeframe=Timeframe(timeframe),
        time=timestamp,
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100"),
        volume=Decimal("10"),
    )


def test_series_chunks_are_closed_non_overlapping_and_cover_end() -> None:
    start = datetime(2023, 1, 1, tzinfo=UTC)
    end = datetime(2023, 1, 3, tzinfo=UTC)

    chunks = list(
        iter_series_chunks(
            start_at=start,
            end_at=end,
            timeframe="1h",
            chunk_span=timedelta(days=1),
        )
    )

    assert chunks == [
        (start, datetime(2023, 1, 1, 23, tzinfo=UTC)),
        (datetime(2023, 1, 2, tzinfo=UTC), datetime(2023, 1, 2, 23, tzinfo=UTC)),
        (datetime(2023, 1, 3, tzinfo=UTC), end),
    ]
    assert all(
        current[1] + timedelta(hours=1) == following[0] for current, following in zip(chunks, chunks[1:], strict=False)
    )


def test_initialize_timeseries_database_creates_ohlcv_table(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'strategy-history.db'}"

    initialize_timeseries_database(database_url)

    engine = create_engine(database_url)
    try:
        assert "ohlcv_bars" in inspect(engine).get_table_names()
    finally:
        engine.dispose()


class _FakeRepository:
    def __init__(self) -> None:
        self.stored: list[OHLCVBar] = []

    def store_ohlcv_bars(self, bars) -> int:
        materialized = list(bars)
        self.stored.extend(materialized)
        return len(materialized)


class _FlakyClient:
    def __init__(self) -> None:
        self.calls = 0

    def fetch_ohlcv_history(self, *, symbol, timeframe, start_at, end_at):
        self.calls += 1
        if self.calls == 1:
            raise TimeoutError("transient public REST timeout")
        return [_bar(symbol, timeframe, start_at)]


def test_backfiller_retries_and_normalizes_perp_symbol_without_unbounded_batch() -> None:
    repository = _FakeRepository()
    client = _FlakyClient()
    commits: list[bool] = []
    sleeps: list[float] = []
    backfiller = StrategyHistoryBackfiller(
        data_repo=repository,
        client=client,
        commit=lambda: commits.append(True),
        sleep=sleeps.append,
    )

    result = backfiller.backfill_ohlcv_series(
        symbol="BTC/USDT",
        timeframe="1h",
        start_at=datetime(2023, 1, 1, tzinfo=UTC),
        end_at=datetime(2023, 1, 1, tzinfo=UTC),
        chunk_span=timedelta(days=1),
    )

    assert client.calls == 2
    assert sleeps == [1.0]
    assert commits == [True]
    assert result.rows_fetched == 1
    assert result.rows_written == 1
    assert result.chunk_count == 1
    assert repository.stored[0].symbol == "BTC/USDT"
    assert repository.stored[0].timeframe is Timeframe.H1
