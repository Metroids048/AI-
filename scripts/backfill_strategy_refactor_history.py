"""Backfill verified Binance USDT-M research OHLCV into an isolated database.

The primary source is Binance Vision's checksummed 1m archive.  Only the
unpublished archive tail is fetched from the fixed Binance mainnet public REST
endpoint.  No request path in this module can fall back to Testnet.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import time
from collections.abc import Callable, Iterable, Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from io import BytesIO, TextIOWrapper
from pathlib import Path
from typing import Any, Protocol, TypedDict
from urllib.error import HTTPError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen
from zipfile import ZipFile

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from services.data.binance import platform_symbol_to_binance_raw, spot_to_usdm_perp_symbol
from services.data.repository import DataRepository, create_timeseries_schema, ohlcv_bars
from shared.models import Exchange, MarketExtras, OHLCVBar, Timeframe

VISION_BASE_URL = "https://data.binance.vision"
MAINNET_FAPI_BASE_URL = "https://fapi.binance.com"
DEFAULT_DATABASE_URL = "sqlite:///./.strategy_refactor_history.db"
DEFAULT_CACHE_DIR = Path(".cache/strategy_refactor_history")
DEFAULT_SYMBOLS = ("BTC/USDT", "ETH/USDT")
DERIVED_TIMEFRAMES = ("5m", "15m", "1h", "4h")
TIMEFRAME_DELTAS = {
    "1m": timedelta(minutes=1),
    "5m": timedelta(minutes=5),
    "15m": timedelta(minutes=15),
    "1h": timedelta(hours=1),
    "4h": timedelta(hours=4),
}
DEFAULT_CHUNK_SPANS = {
    "1m": timedelta(days=7),
    "5m": timedelta(days=30),
    "15m": timedelta(days=90),
    "1h": timedelta(days=180),
    "4h": timedelta(days=365),
}


class IncompleteTimeSliceError(ValueError):
    """A source slice is not a complete, consecutive closed-candle interval."""


class ArchiveUnavailableError(FileNotFoundError):
    """A Binance Vision archive has not been published."""


class OhlcvHistoryClient(Protocol):
    def fetch_ohlcv_history(
        self,
        *,
        symbol: str,
        timeframe: str,
        start_at: datetime,
        end_at: datetime,
    ) -> Sequence[OHLCVBar]: ...


class OhlcvRepository(Protocol):
    def store_ohlcv_bars(self, bars: Iterable[OHLCVBar]) -> int: ...


@dataclass(frozen=True)
class SeriesBackfillResult:
    symbol: str
    timeframe: str
    start_at: datetime
    end_at: datetime
    chunk_count: int
    rows_fetched: int
    rows_written: int


@dataclass(frozen=True)
class ArchiveSliceSpec:
    symbol: str
    source_url: str
    csv_name: str
    expected_start: datetime
    expected_end_exclusive: datetime
    cadence: str = "monthly"

    @property
    def checksum_url(self) -> str:
        return f"{self.source_url}.CHECKSUM"

    @property
    def cache_name(self) -> str:
        return self.source_url.rsplit("/", 1)[-1]


@dataclass(frozen=True)
class DownloadedArchive:
    spec: ArchiveSliceSpec
    payload: bytes
    sha256: str
    cache_path: Path


@dataclass(frozen=True)
class AggregationResult:
    bars: list[OHLCVBar]
    incomplete_bucket_starts: list[datetime]


class AggregationAudit(TypedDict):
    rows_written: int
    incomplete_bucket_starts: list[str]


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _floor_4h(value: datetime) -> datetime:
    value = _utc(value)
    return value.replace(hour=(value.hour // 4) * 4, minute=0, second=0, microsecond=0)


def _next_month(value: datetime) -> datetime:
    if value.month == 12:
        return value.replace(year=value.year + 1, month=1)
    return value.replace(month=value.month + 1)


def _raw_symbol(symbol: str) -> str:
    return platform_symbol_to_binance_raw(spot_to_usdm_perp_symbol(symbol))


def require_trusted_binance_url(*, requested_url: str, effective_url: str, expected_host: str) -> None:
    """Reject scheme/host changes, including redirects to Binance Testnet."""

    requested = urlparse(requested_url)
    effective = urlparse(effective_url)
    if (
        requested.scheme != "https"
        or effective.scheme != "https"
        or requested.hostname != expected_host
        or effective.hostname != expected_host
        or requested.port is not None
        or effective.port is not None
    ):
        raise ValueError(
            "untrusted Binance data source: "
            f"requested={requested.scheme}://{requested.netloc}, effective={effective.scheme}://{effective.netloc}"
        )


def _request_bytes(url: str, *, expected_host: str, timeout: float = 60.0) -> bytes:
    require_trusted_binance_url(requested_url=url, effective_url=url, expected_host=expected_host)
    request = Request(url, headers={"User-Agent": "ai-quant-research-history/1.0"})
    with urlopen(request, timeout=timeout) as response:  # noqa: S310
        require_trusted_binance_url(
            requested_url=url,
            effective_url=response.geturl(),
            expected_host=expected_host,
        )
        return response.read()


def _request_json(url: str) -> Any:
    return json.loads(_request_bytes(url, expected_host="fapi.binance.com"))


def _parse_datetime(value: str) -> datetime:
    return _utc(datetime.fromisoformat(value.replace("Z", "+00:00")))


def _timestamp_from_binance(value: str) -> datetime:
    raw = int(value)
    divisor = 1_000_000 if raw >= 100_000_000_000_000 else 1_000
    return datetime.fromtimestamp(raw / divisor, tz=UTC)


def _canonical_bar_hash(bars: Iterable[OHLCVBar]) -> str:
    digest = hashlib.sha256()
    for bar in bars:
        digest.update(
            (
                "\t".join(
                    (
                        bar.timestamp.isoformat(),
                        format(bar.open, "f"),
                        format(bar.high, "f"),
                        format(bar.low, "f"),
                        format(bar.close, "f"),
                        format(bar.volume, "f"),
                    )
                )
                + "\n"
            ).encode()
        )
    return digest.hexdigest()


def _canonical_funding_hash(extras: Iterable[MarketExtras]) -> str:
    digest = hashlib.sha256()
    for item in extras:
        digest.update(f"{item.timestamp.isoformat()}\t{format(item.funding_rate or Decimal('0'), 'f')}\n".encode())
    return digest.hexdigest()


def fetch_mainnet_funding_history(*, symbol: str, start_at: datetime, end_exclusive: datetime) -> list[MarketExtras]:
    """Fetch point-in-time funding from Binance's fixed public mainnet origin."""

    raw_symbol = _raw_symbol(symbol)
    cursor_ms = int(_utc(start_at).timestamp() * 1_000)
    end_ms = int(_utc(end_exclusive).timestamp() * 1_000)
    by_timestamp: dict[datetime, MarketExtras] = {}
    while cursor_ms < end_ms:
        query = urlencode(
            {
                "symbol": raw_symbol,
                "startTime": cursor_ms,
                "endTime": end_ms - 1,
                "limit": 1000,
            }
        )
        rows = _request_json(f"{MAINNET_FAPI_BASE_URL}/fapi/v1/fundingRate?{query}")
        if not isinstance(rows, list) or not rows:
            break
        latest_ms = cursor_ms
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("unexpected Binance funding response item")
            timestamp_ms = int(row["fundingTime"])
            timestamp = datetime.fromtimestamp(timestamp_ms / 1_000, tz=UTC)
            if timestamp >= _utc(end_exclusive):
                continue
            by_timestamp[timestamp] = MarketExtras(
                symbol=symbol,
                time=timestamp,
                funding_rate=Decimal(str(row["fundingRate"])),
            )
            latest_ms = max(latest_ms, timestamp_ms)
        if latest_ms < cursor_ms or len(rows) < 1000:
            break
        cursor_ms = latest_ms + 1
    return [by_timestamp[timestamp] for timestamp in sorted(by_timestamp)]


def _validate_complete_bars(
    bars: Sequence[OHLCVBar],
    *,
    start_at: datetime,
    end_exclusive: datetime,
    timeframe: str,
    source: str,
) -> None:
    start_at = _utc(start_at)
    end_exclusive = _utc(end_exclusive)
    interval = TIMEFRAME_DELTAS[timeframe]
    span = end_exclusive - start_at
    expected_count = int(span / interval)
    if span <= timedelta(0) or span != interval * expected_count:
        raise ValueError(f"unaligned {timeframe} source boundaries: {start_at} to {end_exclusive}")
    expected_time = start_at
    for bar in bars:
        actual_time = _utc(bar.timestamp)
        if actual_time != expected_time:
            raise IncompleteTimeSliceError(
                f"{source}: expected {expected_count} consecutive {timeframe} bars "
                f"from {start_at.isoformat()} through {end_exclusive.isoformat()}, got "
                f"{len(bars)} with first gap at {expected_time.isoformat()}"
            )
        expected_time += interval
    if len(bars) != expected_count:
        raise IncompleteTimeSliceError(
            f"{source}: expected {expected_count} consecutive {timeframe} bars "
            f"from {start_at.isoformat()} through {end_exclusive.isoformat()}, got {len(bars)}"
        )


def parse_vision_archive(
    *,
    payload: bytes,
    expected_sha256: str,
    spec: ArchiveSliceSpec,
) -> list[OHLCVBar]:
    """Verify and parse one Binance Vision archive without extracting it."""

    actual_sha256 = hashlib.sha256(payload).hexdigest()
    if actual_sha256.lower() != expected_sha256.lower():
        raise ValueError(
            f"Binance Vision SHA-256 mismatch for {spec.source_url}: expected {expected_sha256}, got {actual_sha256}"
        )
    bars: list[OHLCVBar] = []
    with ZipFile(BytesIO(payload)) as archive:
        members = [item for item in archive.infolist() if not item.is_dir()]
        if len(members) != 1 or members[0].filename != spec.csv_name:
            raise ValueError(
                f"unexpected Binance Vision archive members for {spec.source_url}: "
                f"{[item.filename for item in members]}"
            )
        with archive.open(members[0]) as binary_stream:
            reader = csv.reader(TextIOWrapper(binary_stream, encoding="utf-8", newline=""))
            for row in reader:
                if not row or row[0] == "open_time":
                    continue
                if len(row) < 7:
                    raise ValueError(f"malformed Binance Vision kline row in {spec.source_url}")
                opened_at = _timestamp_from_binance(row[0])
                bars.append(
                    OHLCVBar(
                        symbol=spec.symbol,
                        exchange=Exchange.BINANCE,
                        timeframe=Timeframe.M1,
                        time=opened_at,
                        open=Decimal(row[1]),
                        high=Decimal(row[2]),
                        low=Decimal(row[3]),
                        close=Decimal(row[4]),
                        volume=Decimal(row[5]),
                    )
                )
    _validate_complete_bars(
        bars,
        start_at=spec.expected_start,
        end_exclusive=spec.expected_end_exclusive,
        timeframe="1m",
        source=spec.source_url,
    )
    return bars


def _checksum_digest(payload: bytes, *, expected_filename: str, source_url: str) -> str:
    parts = payload.decode("ascii").strip().split()
    if len(parts) != 2 or parts[1].lstrip("*") != expected_filename:
        raise ValueError(f"malformed Binance Vision checksum at {source_url}")
    digest = parts[0].lower()
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise ValueError(f"invalid Binance Vision SHA-256 at {source_url}")
    return digest


def _download_with_retries(
    url: str,
    *,
    expected_host: str,
    max_attempts: int = 3,
    sleep: Callable[[float], Any] = time.sleep,
) -> bytes:
    for attempt in range(1, max_attempts + 1):
        try:
            return _request_bytes(url, expected_host=expected_host)
        except HTTPError as exc:
            if exc.code == 404:
                raise ArchiveUnavailableError(url) from exc
            if attempt >= max_attempts:
                raise
        except (OSError, TimeoutError):
            if attempt >= max_attempts:
                raise
        sleep(float(2 ** (attempt - 1)))
    raise RuntimeError("unreachable download retry state")


def download_vision_archive(spec: ArchiveSliceSpec, *, cache_dir: Path) -> DownloadedArchive:
    """Download or reuse one archive only after its official checksum verifies."""

    symbol_cache = cache_dir / _raw_symbol(spec.symbol) / "1m"
    symbol_cache.mkdir(parents=True, exist_ok=True)
    archive_path = symbol_cache / spec.cache_name
    checksum_path = archive_path.with_suffix(archive_path.suffix + ".sha256")
    if archive_path.exists() and checksum_path.exists():
        digest = checksum_path.read_text(encoding="ascii").strip()
        payload = archive_path.read_bytes()
        if hashlib.sha256(payload).hexdigest() == digest:
            return DownloadedArchive(spec=spec, payload=payload, sha256=digest, cache_path=archive_path)

    checksum_payload = _download_with_retries(spec.checksum_url, expected_host="data.binance.vision")
    digest = _checksum_digest(
        checksum_payload,
        expected_filename=spec.cache_name,
        source_url=spec.checksum_url,
    )
    payload = _download_with_retries(spec.source_url, expected_host="data.binance.vision")
    actual = hashlib.sha256(payload).hexdigest()
    if actual != digest:
        raise ValueError(f"Binance Vision SHA-256 mismatch for {spec.source_url}: expected {digest}, got {actual}")
    temporary = archive_path.with_suffix(archive_path.suffix + ".part")
    temporary.write_bytes(payload)
    temporary.replace(archive_path)
    checksum_path.write_text(digest + "\n", encoding="ascii")
    return DownloadedArchive(spec=spec, payload=payload, sha256=digest, cache_path=archive_path)


def build_vision_archive_specs(
    *,
    symbol: str,
    start_at: datetime,
    end_exclusive: datetime,
) -> list[ArchiveSliceSpec]:
    """Use monthly archives for full past months and daily archives thereafter."""

    start_at = _utc(start_at)
    end_exclusive = _utc(end_exclusive)
    if start_at.day != 1 or start_at.time() != datetime.min.time():
        raise ValueError("archive backfill start_at must be a UTC calendar-month boundary")
    raw_symbol = _raw_symbol(symbol)
    specs: list[ArchiveSliceSpec] = []
    cursor = start_at
    current_month = end_exclusive.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    while cursor < min(current_month, end_exclusive):
        following = _next_month(cursor)
        year_month = cursor.strftime("%Y-%m")
        filename = f"{raw_symbol}-1m-{year_month}.zip"
        specs.append(
            ArchiveSliceSpec(
                symbol=symbol,
                source_url=(f"{VISION_BASE_URL}/data/futures/um/monthly/klines/{raw_symbol}/1m/{filename}"),
                csv_name=filename.removesuffix(".zip") + ".csv",
                expected_start=cursor,
                expected_end_exclusive=following,
                cadence="monthly",
            )
        )
        cursor = following
    complete_day_end = end_exclusive.replace(hour=0, minute=0, second=0, microsecond=0)
    while cursor < complete_day_end:
        following = cursor + timedelta(days=1)
        day = cursor.strftime("%Y-%m-%d")
        filename = f"{raw_symbol}-1m-{day}.zip"
        specs.append(
            ArchiveSliceSpec(
                symbol=symbol,
                source_url=(f"{VISION_BASE_URL}/data/futures/um/daily/klines/{raw_symbol}/1m/{filename}"),
                csv_name=filename.removesuffix(".zip") + ".csv",
                expected_start=cursor,
                expected_end_exclusive=following,
                cadence="daily",
            )
        )
        cursor = following
    return specs


def iter_series_chunks(
    *,
    start_at: datetime,
    end_at: datetime,
    timeframe: str,
    chunk_span: timedelta,
) -> Iterator[tuple[datetime, datetime]]:
    """Yield inclusive non-overlapping chunks aligned to the candle interval."""

    if timeframe not in TIMEFRAME_DELTAS:
        raise ValueError(f"unsupported timeframe: {timeframe}")
    start_at = _utc(start_at)
    end_at = _utc(end_at)
    interval = TIMEFRAME_DELTAS[timeframe]
    if end_at < start_at:
        raise ValueError("end_at must not be before start_at")
    if chunk_span < interval:
        raise ValueError("chunk_span must cover at least one candle interval")
    cursor = start_at
    while cursor <= end_at:
        chunk_end = min(cursor + chunk_span - interval, end_at)
        yield cursor, chunk_end
        cursor = chunk_end + interval


class BinanceMainnetKlineClient:
    """Fixed-origin public mainnet client for the archive publication tail."""

    def __init__(self, *, request_json: Callable[[str], Any] = _request_json) -> None:
        self.request_json = request_json

    def fetch_ohlcv_history(
        self,
        *,
        symbol: str,
        timeframe: str,
        start_at: datetime,
        end_at: datetime,
    ) -> list[OHLCVBar]:
        interval = TIMEFRAME_DELTAS[timeframe]
        cursor = _utc(start_at)
        inclusive_end = _utc(end_at)
        bars: list[OHLCVBar] = []
        while cursor <= inclusive_end:
            params = urlencode(
                {
                    "symbol": platform_symbol_to_binance_raw(symbol),
                    "interval": timeframe,
                    "startTime": int(cursor.timestamp() * 1000),
                    "endTime": int(inclusive_end.timestamp() * 1000),
                    "limit": 1000,
                }
            )
            url = f"{MAINNET_FAPI_BASE_URL}/fapi/v1/klines?{params}"
            payload = self.request_json(url)
            if not isinstance(payload, list) or not payload:
                break
            page: list[OHLCVBar] = []
            for row in payload:
                if not isinstance(row, Sequence) or len(row) < 7:
                    raise ValueError(f"malformed Binance mainnet REST kline response for {symbol}")
                opened_at = datetime.fromtimestamp(int(row[0]) / 1000, tz=UTC)
                if opened_at > inclusive_end:
                    continue
                page.append(
                    OHLCVBar(
                        symbol=symbol.split(":", 1)[0],
                        exchange=Exchange.BINANCE,
                        timeframe=Timeframe(timeframe),
                        time=opened_at,
                        open=Decimal(str(row[1])),
                        high=Decimal(str(row[2])),
                        low=Decimal(str(row[3])),
                        close=Decimal(str(row[4])),
                        volume=Decimal(str(row[5])),
                    )
                )
            if not page:
                break
            bars.extend(page)
            next_cursor = page[-1].timestamp + interval
            if next_cursor <= cursor:
                raise ValueError("Binance mainnet REST pagination did not advance")
            cursor = next_cursor
        return bars

    def close(self) -> None:
        return None


class StrategyHistoryBackfiller:
    """Fetch, normalize, validate, and commit one bounded series chunk at a time."""

    def __init__(
        self,
        *,
        data_repo: OhlcvRepository,
        client: OhlcvHistoryClient,
        commit: Callable[[], Any],
        sleep: Callable[[float], Any] = time.sleep,
        progress: Callable[[dict[str, Any]], Any] | None = None,
        max_attempts: int = 3,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        self.data_repo = data_repo
        self.client = client
        self.commit = commit
        self.sleep = sleep
        self.progress = progress
        self.max_attempts = max_attempts

    def _fetch_chunk(
        self,
        *,
        symbol: str,
        timeframe: str,
        start_at: datetime,
        end_at: datetime,
    ) -> Sequence[OHLCVBar]:
        for attempt in range(1, self.max_attempts + 1):
            try:
                return self.client.fetch_ohlcv_history(
                    symbol=spot_to_usdm_perp_symbol(symbol),
                    timeframe=timeframe,
                    start_at=start_at,
                    end_at=end_at,
                )
            except (OSError, TimeoutError):
                if attempt >= self.max_attempts:
                    raise
                self.sleep(float(2 ** (attempt - 1)))
        raise RuntimeError("unreachable retry state")

    def backfill_ohlcv_series(
        self,
        *,
        symbol: str,
        timeframe: str,
        start_at: datetime,
        end_at: datetime,
        chunk_span: timedelta,
        validate_complete: bool = False,
    ) -> SeriesBackfillResult:
        rows_fetched = 0
        rows_written = 0
        chunk_count = 0
        for chunk_start, chunk_end in iter_series_chunks(
            start_at=start_at,
            end_at=end_at,
            timeframe=timeframe,
            chunk_span=chunk_span,
        ):
            bars = self._fetch_chunk(
                symbol=symbol,
                timeframe=timeframe,
                start_at=chunk_start,
                end_at=chunk_end,
            )
            platform_bars = [bar.model_copy(update={"symbol": symbol}) for bar in bars]
            if validate_complete:
                _validate_complete_bars(
                    platform_bars,
                    start_at=chunk_start,
                    end_exclusive=chunk_end + TIMEFRAME_DELTAS[timeframe],
                    timeframe=timeframe,
                    source=MAINNET_FAPI_BASE_URL,
                )
            written = self.data_repo.store_ohlcv_bars(platform_bars)
            self.commit()
            chunk_count += 1
            rows_fetched += len(platform_bars)
            rows_written += written
            if self.progress is not None:
                self.progress(
                    {
                        "symbol": symbol,
                        "timeframe": timeframe,
                        "chunk": chunk_count,
                        "chunk_start": chunk_start.isoformat(),
                        "chunk_end": chunk_end.isoformat(),
                        "rows_fetched": len(platform_bars),
                        "rows_written": written,
                    }
                )
        return SeriesBackfillResult(
            symbol=symbol,
            timeframe=timeframe,
            start_at=_utc(start_at),
            end_at=_utc(end_at),
            chunk_count=chunk_count,
            rows_fetched=rows_fetched,
            rows_written=rows_written,
        )


def store_bars_in_batches(
    repository: OhlcvRepository,
    bars: Sequence[OHLCVBar],
    *,
    batch_size: int,
) -> int:
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    written = 0
    for offset in range(0, len(bars), batch_size):
        written += repository.store_ohlcv_bars(bars[offset : offset + batch_size])
    return written


def _bucket_start(value: datetime, interval: timedelta) -> datetime:
    epoch_seconds = int(_utc(value).timestamp())
    interval_seconds = int(interval.total_seconds())
    return datetime.fromtimestamp(epoch_seconds - epoch_seconds % interval_seconds, tz=UTC)


def aggregate_complete_bars(bars: Sequence[OHLCVBar], *, timeframe: str) -> AggregationResult:
    """Aggregate 1m bars using first/max/min/last/sum; omit incomplete buckets."""

    if timeframe not in DERIVED_TIMEFRAMES:
        raise ValueError(f"unsupported derived timeframe: {timeframe}")
    interval = TIMEFRAME_DELTAS[timeframe]
    expected_count = int(interval / TIMEFRAME_DELTAS["1m"])
    groups: dict[datetime, list[OHLCVBar]] = {}
    for bar in sorted(bars, key=lambda item: item.timestamp):
        groups.setdefault(_bucket_start(bar.timestamp, interval), []).append(bar)
    output: list[OHLCVBar] = []
    incomplete: list[datetime] = []
    for bucket, bucket_bars in sorted(groups.items()):
        expected_times = [bucket + timedelta(minutes=index) for index in range(expected_count)]
        if [bar.timestamp for bar in bucket_bars] != expected_times:
            incomplete.append(bucket)
            continue
        output.append(
            OHLCVBar(
                symbol=bucket_bars[0].symbol,
                exchange=Exchange.BINANCE,
                timeframe=Timeframe(timeframe),
                time=bucket,
                open=bucket_bars[0].open,
                high=max(bar.high for bar in bucket_bars),
                low=min(bar.low for bar in bucket_bars),
                close=bucket_bars[-1].close,
                volume=sum((bar.volume for bar in bucket_bars), start=Decimal(0)),
            )
        )
    return AggregationResult(bars=output, incomplete_bucket_starts=incomplete)


def _validate_backfill_boundaries(*, start_at: datetime, end_exclusive: datetime) -> None:
    start_at = _utc(start_at)
    end_exclusive = _utc(end_exclusive)
    if start_at.day != 1 or start_at.time() != datetime.min.time():
        raise ValueError("backfill start_at must be a UTC calendar-month boundary")
    if (
        end_exclusive.minute != 0
        or end_exclusive.second != 0
        or end_exclusive.microsecond != 0
        or end_exclusive.hour % 4 != 0
    ):
        raise ValueError("backfill end_exclusive must be aligned to a closed UTC 4h boundary")
    if end_exclusive <= start_at:
        raise ValueError("backfill end_exclusive must be after start_at")


def _validate_persisted_coverage(
    session: Session,
    *,
    symbol: str,
    start_at: datetime,
    end_exclusive: datetime,
) -> int:
    """Validate a persisted 1m series without materializing all OHLCV rows."""

    start_at = _utc(start_at)
    end_exclusive = _utc(end_exclusive)
    interval = TIMEFRAME_DELTAS["1m"]
    expected_count = int((end_exclusive - start_at) / interval)
    statement = (
        select(ohlcv_bars.c.time)
        .where(
            ohlcv_bars.c.symbol == symbol,
            ohlcv_bars.c.exchange == str(Exchange.BINANCE),
            ohlcv_bars.c.timeframe == "1m",
            ohlcv_bars.c.time >= start_at,
            ohlcv_bars.c.time < end_exclusive,
        )
        .order_by(ohlcv_bars.c.time)
    )
    expected_time = start_at
    row_count = 0
    for raw_time in session.execute(statement).scalars().yield_per(50_000):
        actual_time = _utc(raw_time)
        if actual_time != expected_time:
            raise IncompleteTimeSliceError(
                f"persisted {symbol} research series: first gap at {expected_time.isoformat()}, "
                f"got {actual_time.isoformat()}"
            )
        row_count += 1
        expected_time += interval
    if row_count != expected_count:
        raise IncompleteTimeSliceError(
            f"persisted {symbol} research series: expected {expected_count} consecutive 1m bars, got {row_count}"
        )
    return row_count


def _load_1m_bars(
    session: Session,
    *,
    symbol: str,
    start_at: datetime,
    end_exclusive: datetime,
) -> list[OHLCVBar]:
    rows = session.execute(
        select(
            ohlcv_bars.c.time,
            ohlcv_bars.c.open,
            ohlcv_bars.c.high,
            ohlcv_bars.c.low,
            ohlcv_bars.c.close,
            ohlcv_bars.c.volume,
        )
        .where(
            ohlcv_bars.c.symbol == symbol,
            ohlcv_bars.c.exchange == str(Exchange.BINANCE),
            ohlcv_bars.c.timeframe == "1m",
            ohlcv_bars.c.time >= start_at,
            ohlcv_bars.c.time < end_exclusive,
        )
        .order_by(ohlcv_bars.c.time)
    ).all()
    return [
        OHLCVBar(
            symbol=symbol,
            exchange=Exchange.BINANCE,
            timeframe=Timeframe.M1,
            time=_utc(row.time),
            open=Decimal(str(row.open)),
            high=Decimal(str(row.high)),
            low=Decimal(str(row.low)),
            close=Decimal(str(row.close)),
            volume=Decimal(str(row.volume)),
        )
        for row in rows
    ]


def _persisted_slice_is_complete(
    session: Session,
    *,
    symbol: str,
    start_at: datetime,
    end_exclusive: datetime,
) -> bool:
    interval = TIMEFRAME_DELTAS["1m"]
    expected_count = int((_utc(end_exclusive) - _utc(start_at)) / interval)
    row = session.execute(
        select(
            func.count(),
            func.min(ohlcv_bars.c.time),
            func.max(ohlcv_bars.c.time),
        ).where(
            ohlcv_bars.c.symbol == symbol,
            ohlcv_bars.c.exchange == str(Exchange.BINANCE),
            ohlcv_bars.c.timeframe == "1m",
            ohlcv_bars.c.time >= start_at,
            ohlcv_bars.c.time < end_exclusive,
        )
    ).one()
    return (
        row[0] == expected_count
        and row[1] is not None
        and row[2] is not None
        and _utc(row[1]) == _utc(start_at)
        and _utc(row[2]) == _utc(end_exclusive) - interval
    )


def aggregate_database_timeframes(
    session: Session,
    *,
    symbol: str,
    start_at: datetime,
    end_exclusive: datetime,
    batch_size: int,
    progress: Callable[[dict[str, Any]], Any] | None = None,
) -> dict[str, AggregationAudit]:
    """Derive all higher timeframes in aligned 28-day chunks."""

    repository = DataRepository(session)
    results: dict[str, AggregationAudit] = {
        timeframe: {"rows_written": 0, "incomplete_bucket_starts": []} for timeframe in DERIVED_TIMEFRAMES
    }
    cursor = _utc(start_at)
    end_exclusive = _utc(end_exclusive)
    while cursor < end_exclusive:
        chunk_end = min(cursor + timedelta(days=28), end_exclusive)
        source_bars = _load_1m_bars(
            session,
            symbol=symbol,
            start_at=cursor,
            end_exclusive=chunk_end,
        )
        for timeframe in DERIVED_TIMEFRAMES:
            aggregated = aggregate_complete_bars(source_bars, timeframe=timeframe)
            if aggregated.incomplete_bucket_starts:
                first_incomplete = aggregated.incomplete_bucket_starts[0]
                raise IncompleteTimeSliceError(
                    f"persisted {symbol} research series has incomplete {timeframe} bucket "
                    f"at {first_incomplete.isoformat()}"
                )
            results[timeframe]["rows_written"] += store_bars_in_batches(
                repository,
                aggregated.bars,
                batch_size=batch_size,
            )
            results[timeframe]["incomplete_bucket_starts"].extend(
                value.isoformat() for value in aggregated.incomplete_bucket_starts
            )
        if progress is not None:
            progress(
                {
                    "stage": "aggregate",
                    "symbol": symbol,
                    "chunk_start": cursor.isoformat(),
                    "chunk_end_exclusive": chunk_end.isoformat(),
                    "source_1m_rows": len(source_bars),
                }
            )
        cursor = chunk_end
    return results


def initialize_timeseries_database(database_url: str) -> None:
    """Create only the existing market-data tables needed by the backfill."""

    from services.database import get_engine

    create_timeseries_schema(get_engine(database_url))


def _download_archives(
    specs: Sequence[ArchiveSliceSpec],
    *,
    cache_dir: Path,
    workers: int,
) -> tuple[list[DownloadedArchive], list[ArchiveSliceSpec]]:
    downloaded: list[DownloadedArchive] = []
    unavailable: list[ArchiveSliceSpec] = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(download_vision_archive, spec, cache_dir=cache_dir): spec for spec in specs}
        for future in as_completed(futures):
            spec = futures[future]
            try:
                downloaded.append(future.result())
            except ArchiveUnavailableError:
                unavailable.append(spec)
    return (
        sorted(downloaded, key=lambda item: item.spec.expected_start),
        sorted(unavailable, key=lambda item: item.expected_start),
    )


def _merge_ranges(ranges: Iterable[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    merged: list[tuple[datetime, datetime]] = []
    for start_at, end_exclusive in sorted(ranges):
        if merged and merged[-1][1] == start_at:
            merged[-1] = (merged[-1][0], end_exclusive)
        else:
            merged.append((start_at, end_exclusive))
    return merged


def _write_immutable_json(path: Path, payload: dict[str, Any]) -> None:
    if path.exists():
        raise FileExistsError(f"refusing to overwrite immutable provenance: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def run_history_backfill(
    *,
    database_url: str,
    start_at: datetime,
    end_exclusive: datetime,
    symbols: Sequence[str],
    cache_dir: Path,
    batch_size: int,
    download_workers: int,
    progress: Callable[[dict[str, Any]], Any] | None = None,
) -> dict[str, Any]:
    """Import verified 1m sources, fetch the unpublished tail, then aggregate."""

    from services.database import get_session_factory

    start_at = _utc(start_at)
    end_exclusive = _utc(end_exclusive)
    _validate_backfill_boundaries(start_at=start_at, end_exclusive=end_exclusive)
    initialize_timeseries_database(database_url)
    source_records: list[dict[str, Any]] = []
    aggregation_records: dict[str, Any] = {}
    with get_session_factory(database_url)() as session:
        repository = DataRepository(session)
        for symbol in symbols:
            specs = build_vision_archive_specs(
                symbol=symbol,
                start_at=start_at,
                end_exclusive=end_exclusive,
            )
            downloaded, unavailable = _download_archives(
                specs,
                cache_dir=cache_dir,
                workers=download_workers,
            )
            monthly_unavailable = [spec.source_url for spec in unavailable if spec.cadence == "monthly"]
            if monthly_unavailable:
                raise ArchiveUnavailableError(
                    "completed monthly Binance Vision archives unavailable: " + ", ".join(monthly_unavailable)
                )
            for item in downloaded:
                reused = _persisted_slice_is_complete(
                    session,
                    symbol=symbol,
                    start_at=item.spec.expected_start,
                    end_exclusive=item.spec.expected_end_exclusive,
                )
                if reused:
                    row_count = int(
                        (item.spec.expected_end_exclusive - item.spec.expected_start) / TIMEFRAME_DELTAS["1m"]
                    )
                else:
                    bars = parse_vision_archive(
                        payload=item.payload,
                        expected_sha256=item.sha256,
                        spec=item.spec,
                    )
                    store_bars_in_batches(repository, bars, batch_size=batch_size)
                    row_count = len(bars)
                record = {
                    "source": "BINANCE_VISION_USDT_M_KLINES",
                    "source_url": item.spec.source_url,
                    "sha256": item.sha256,
                    "symbol": symbol,
                    "timeframe": "1m",
                    "start_at": item.spec.expected_start.isoformat(),
                    "end_exclusive": item.spec.expected_end_exclusive.isoformat(),
                    "rows": row_count,
                    "storage_action": "reused" if reused else "upserted",
                }
                source_records.append(record)
                if progress is not None:
                    progress({"stage": "archive_import", **record})

            archive_end = max(
                (item.spec.expected_end_exclusive for item in downloaded),
                default=start_at,
            )
            rest_range_candidates = [(spec.expected_start, spec.expected_end_exclusive) for spec in unavailable]
            if archive_end < end_exclusive:
                rest_range_candidates.append((archive_end, end_exclusive))
            rest_ranges = _merge_ranges(rest_range_candidates)
            client = BinanceMainnetKlineClient()
            for range_start, range_end_exclusive in rest_ranges:
                backfiller = StrategyHistoryBackfiller(
                    data_repo=repository,
                    client=client,
                    commit=session.commit,
                    progress=progress,
                )
                result = backfiller.backfill_ohlcv_series(
                    symbol=symbol,
                    timeframe="1m",
                    start_at=range_start,
                    end_at=range_end_exclusive - timedelta(minutes=1),
                    chunk_span=timedelta(hours=16),
                    validate_complete=True,
                )
                rest_bars = _load_1m_bars(
                    session,
                    symbol=symbol,
                    start_at=range_start,
                    end_exclusive=range_end_exclusive,
                )
                source_records.append(
                    {
                        "source": "BINANCE_USDT_M_MAINNET_PUBLIC_REST",
                        "source_url": MAINNET_FAPI_BASE_URL,
                        "sha256": _canonical_bar_hash(rest_bars),
                        "symbol": symbol,
                        "timeframe": "1m",
                        "start_at": range_start.isoformat(),
                        "end_exclusive": range_end_exclusive.isoformat(),
                        "rows": result.rows_fetched,
                    }
                )

            funding = fetch_mainnet_funding_history(
                symbol=symbol,
                start_at=start_at,
                end_exclusive=end_exclusive,
            )
            repository.store_market_extras(funding)
            funding_record = {
                "source": "BINANCE_USDT_M_MAINNET_PUBLIC_FUNDING_REST",
                "source_url": MAINNET_FAPI_BASE_URL,
                "sha256": _canonical_funding_hash(funding),
                "symbol": symbol,
                "timeframe": "8h_funding_event",
                "start_at": start_at.isoformat(),
                "end_exclusive": end_exclusive.isoformat(),
                "rows": len(funding),
                "storage_action": "upserted",
            }
            source_records.append(funding_record)
            if progress is not None:
                progress({"stage": "funding_import", **funding_record})

            _validate_persisted_coverage(
                session,
                symbol=symbol,
                start_at=start_at,
                end_exclusive=end_exclusive,
            )
            aggregation_records[symbol] = aggregate_database_timeframes(
                session,
                symbol=symbol,
                start_at=start_at,
                end_exclusive=end_exclusive,
                batch_size=batch_size,
                progress=progress,
            )
    return {
        "schema_version": 1,
        "status": "COMPLETE",
        "database_url_redacted": f"sqlite:///{Path(database_url.removeprefix('sqlite:///')).name}",
        "start_at": start_at.isoformat(),
        "end_exclusive": end_exclusive.isoformat(),
        "symbols": list(symbols),
        "source_policy": {
            "primary": "Binance Vision USDT-M official archives with official SHA-256 checksums",
            "tail": "https://fapi.binance.com public klines; fixed origin; no Testnet fallback",
            "funding": "https://fapi.binance.com public funding events; fixed origin; content SHA-256 recorded",
            "derived": "1m first/max/min/last/sum aggregation; incomplete buckets omitted",
        },
        "sources": source_records,
        "aggregation": aggregation_records,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=DEFAULT_DATABASE_URL)
    parser.add_argument("--start-at", type=_parse_datetime, default=datetime(2023, 1, 1, tzinfo=UTC))
    parser.add_argument("--end-exclusive", type=_parse_datetime, default=None)
    parser.add_argument("--symbols", nargs="+", default=list(DEFAULT_SYMBOLS))
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--batch-size", type=int, default=10_000)
    parser.add_argument("--download-workers", type=int, default=8)
    parser.add_argument("--provenance-output", type=Path, default=None)
    args = parser.parse_args()
    unknown_symbols = sorted(set(args.symbols) - set(DEFAULT_SYMBOLS))
    if unknown_symbols:
        raise SystemExit(f"unsupported research symbols: {','.join(unknown_symbols)}")
    if args.download_workers < 1:
        raise SystemExit("download-workers must be positive")
    end_exclusive = args.end_exclusive or _floor_4h(datetime.now(UTC))
    if end_exclusive <= args.start_at:
        raise SystemExit("end-exclusive must be after start-at")
    os.environ["POSTGRES_URL"] = args.database_url
    result = run_history_backfill(
        database_url=args.database_url,
        start_at=args.start_at,
        end_exclusive=end_exclusive,
        symbols=args.symbols,
        cache_dir=args.cache_dir,
        batch_size=args.batch_size,
        download_workers=args.download_workers,
        progress=lambda payload: print(json.dumps(payload, sort_keys=True), flush=True),
    )
    if args.provenance_output is not None:
        _write_immutable_json(args.provenance_output, result)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
