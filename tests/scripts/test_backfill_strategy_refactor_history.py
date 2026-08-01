from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from sqlalchemy import create_engine, func, inspect, select
from sqlalchemy.orm import Session

from scripts.backfill_strategy_refactor_history import (
    ArchiveSliceSpec,
    BinanceMainnetKlineClient,
    IncompleteTimeSliceError,
    StrategyHistoryBackfiller,
    _persisted_slice_is_complete,
    _validate_persisted_coverage,
    aggregate_complete_bars,
    fetch_mainnet_funding_history,
    initialize_timeseries_database,
    iter_series_chunks,
    parse_vision_archive,
    require_trusted_binance_url,
    store_bars_in_batches,
)
from services.data.repository import DataRepository, ohlcv_bars
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


def _vision_archive(*, csv_name: str, opened_at: list[datetime]) -> tuple[bytes, str]:
    header = (
        "open_time,open,high,low,close,volume,close_time,quote_volume,count,"
        "taker_buy_volume,taker_buy_quote_volume,ignore\n"
    )
    rows = []
    for index, timestamp in enumerate(opened_at):
        open_time = int(timestamp.timestamp() * 1000)
        rows.append(
            f"{open_time},{100 + index},{102 + index},{99 + index},{101 + index},"
            f"{10 + index},{open_time + 59_999},0,1,0,0,0\n"
        )
    payload = BytesIO()
    with ZipFile(payload, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr(csv_name, header + "".join(rows))
    content = payload.getvalue()
    return content, hashlib.sha256(content).hexdigest()


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


def test_mainnet_tail_client_never_falls_back_to_testnet_after_transient_failure() -> None:
    calls: list[str] = []

    def _fail(url: str):
        calls.append(url)
        raise TimeoutError("transient mainnet public REST timeout")

    client = BinanceMainnetKlineClient(request_json=_fail)

    with pytest.raises(TimeoutError, match="transient mainnet"):
        client.fetch_ohlcv_history(
            symbol="BTC/USDT:USDT",
            timeframe="1m",
            start_at=datetime(2026, 7, 29, tzinfo=UTC),
            end_at=datetime(2026, 7, 29, 1, tzinfo=UTC),
        )

    assert len(calls) == 1
    assert calls[0].startswith("https://fapi.binance.com/fapi/v1/klines?")
    assert "testnet" not in calls[0]


def test_funding_history_uses_fixed_mainnet_origin_and_preserves_signed_rates(monkeypatch) -> None:
    calls: list[str] = []

    def fake_request(url: str):
        calls.append(url)
        return [
            {"fundingTime": 1_672_531_200_000, "fundingRate": "0.0001"},
            {"fundingTime": 1_672_560_000_000, "fundingRate": "-0.0002"},
        ]

    monkeypatch.setattr("scripts.backfill_strategy_refactor_history._request_json", fake_request)
    extras = fetch_mainnet_funding_history(
        symbol="BTC/USDT",
        start_at=datetime(2023, 1, 1, tzinfo=UTC),
        end_exclusive=datetime(2023, 1, 2, tzinfo=UTC),
    )

    assert [item.funding_rate for item in extras] == [Decimal("0.0001"), Decimal("-0.0002")]
    assert len(calls) == 1
    assert calls[0].startswith("https://fapi.binance.com/fapi/v1/fundingRate?")
    assert "testnet" not in calls[0]


def test_trusted_source_rejects_redirect_to_testnet() -> None:
    with pytest.raises(ValueError, match="untrusted Binance data source"):
        require_trusted_binance_url(
            requested_url="https://data.binance.vision/data/futures/um/daily/klines/BTCUSDT/1m/a.zip",
            effective_url="https://testnet.binancefuture.com/fapi/v1/klines",
            expected_host="data.binance.vision",
        )


def test_vision_archive_rejects_incomplete_minute_slice_before_persistence() -> None:
    start = datetime(2023, 1, 1, tzinfo=UTC)
    csv_name = "BTCUSDT-1m-2023-01-01.zip.csv"
    payload, checksum = _vision_archive(
        csv_name=csv_name,
        opened_at=[start, start + timedelta(minutes=2)],
    )
    spec = ArchiveSliceSpec(
        symbol="BTC/USDT",
        source_url="https://data.binance.vision/data/futures/um/daily/klines/BTCUSDT/1m/a.zip",
        csv_name=csv_name,
        expected_start=start,
        expected_end_exclusive=start + timedelta(minutes=3),
    )

    with pytest.raises(IncompleteTimeSliceError, match="expected 3 consecutive 1m bars"):
        parse_vision_archive(payload=payload, expected_sha256=checksum, spec=spec)


def test_validated_archive_batch_upsert_is_idempotent(tmp_path) -> None:
    start = datetime(2023, 1, 1, tzinfo=UTC)
    csv_name = "BTCUSDT-1m-2023-01-01.csv"
    payload, checksum = _vision_archive(
        csv_name=csv_name,
        opened_at=[start + timedelta(minutes=index) for index in range(3)],
    )
    spec = ArchiveSliceSpec(
        symbol="BTC/USDT",
        source_url="https://data.binance.vision/data/futures/um/daily/klines/BTCUSDT/1m/a.zip",
        csv_name=csv_name,
        expected_start=start,
        expected_end_exclusive=start + timedelta(minutes=3),
    )
    bars = parse_vision_archive(payload=payload, expected_sha256=checksum, spec=spec)
    database_url = f"sqlite:///{tmp_path / 'history.db'}"
    initialize_timeseries_database(database_url)
    engine = create_engine(database_url)
    try:
        with Session(engine) as session:
            repository = DataRepository(session)
            assert store_bars_in_batches(repository, bars, batch_size=2) == 3
            assert store_bars_in_batches(repository, bars, batch_size=2) == 3
            count = session.scalar(select(func.count()).select_from(ohlcv_bars))
            assert count == 3
            assert _persisted_slice_is_complete(
                session,
                symbol="BTC/USDT",
                start_at=start,
                end_exclusive=start + timedelta(minutes=3),
            )
    finally:
        engine.dispose()


def test_higher_timeframe_aggregation_uses_ohlcv_rules_and_skips_incomplete_bucket() -> None:
    start = datetime(2023, 1, 1, tzinfo=UTC)
    complete = [
        _bar("BTC/USDT", "1m", start + timedelta(minutes=index)).model_copy(
            update={
                "open": Decimal(str(100 + index)),
                "high": Decimal(str(105 + index)),
                "low": Decimal(str(95 - index)),
                "close": Decimal(str(101 + index)),
                "volume": Decimal(str(index + 1)),
            }
        )
        for index in range(5)
    ]

    aggregated = aggregate_complete_bars(complete, timeframe="5m")
    incomplete = aggregate_complete_bars([*complete[:2], *complete[3:]], timeframe="5m")

    assert len(aggregated.bars) == 1
    assert aggregated.incomplete_bucket_starts == []
    assert aggregated.bars[0].timestamp == start
    assert aggregated.bars[0].open == Decimal("100")
    assert aggregated.bars[0].high == Decimal("109")
    assert aggregated.bars[0].low == Decimal("91")
    assert aggregated.bars[0].close == Decimal("105")
    assert aggregated.bars[0].volume == Decimal("15")
    assert incomplete.bars == []
    assert incomplete.incomplete_bucket_starts == [start]


def test_persisted_coverage_streams_and_rejects_a_missing_minute(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'coverage.db'}"
    initialize_timeseries_database(database_url)
    engine = create_engine(database_url)
    start = datetime(2023, 1, 1, tzinfo=UTC)
    try:
        with Session(engine) as session:
            repository = DataRepository(session)
            repository.store_ohlcv_bars(_bar("BTC/USDT", "1m", start + timedelta(minutes=index)) for index in (0, 2))
            with pytest.raises(IncompleteTimeSliceError, match="first gap"):
                _validate_persisted_coverage(
                    session,
                    symbol="BTC/USDT",
                    start_at=start,
                    end_exclusive=start + timedelta(minutes=3),
                )
    finally:
        engine.dispose()
