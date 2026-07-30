"""Backfill bounded BTC/ETH OHLCV chunks through the existing data layer."""

from __future__ import annotations

import argparse
import json
import os
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from services.data.binance import BinanceCcxtClient, spot_to_usdm_perp_symbol
from services.data.repository import DataRepository, create_timeseries_schema
from shared.models import OHLCVBar

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


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def iter_series_chunks(
    *,
    start_at: datetime,
    end_at: datetime,
    timeframe: str,
    chunk_span: timedelta,
):  # noqa: ANN201
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


class StrategyHistoryBackfiller:
    """Fetch, normalize, and commit one bounded series chunk at a time."""

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


def _parse_datetime(value: str) -> datetime:
    return _utc(datetime.fromisoformat(value.replace("Z", "+00:00")))


def initialize_timeseries_database(database_url: str) -> None:
    """Create only the existing market-data tables needed by the backfill."""

    from services.database import get_engine

    create_timeseries_schema(get_engine(database_url))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--start-at", type=_parse_datetime, required=True)
    parser.add_argument("--end-at", type=_parse_datetime, default=datetime.now(UTC))
    parser.add_argument("--symbols", nargs="+", default=["BTC/USDT", "ETH/USDT"])
    parser.add_argument("--timeframes", nargs="+", default=list(TIMEFRAME_DELTAS))
    args = parser.parse_args()
    unknown_timeframes = sorted(set(args.timeframes) - set(TIMEFRAME_DELTAS))
    if unknown_timeframes:
        raise SystemExit(f"unsupported timeframes: {','.join(unknown_timeframes)}")

    os.environ["POSTGRES_URL"] = args.database_url
    from services.database import get_session_factory

    initialize_timeseries_database(args.database_url)
    client = BinanceCcxtClient()
    results: list[SeriesBackfillResult] = []
    try:
        with get_session_factory()() as session:
            backfiller = StrategyHistoryBackfiller(
                data_repo=DataRepository(session),
                client=client,
                commit=session.commit,
                progress=lambda payload: print(json.dumps(payload, sort_keys=True)),
            )
            for symbol in args.symbols:
                for timeframe in args.timeframes:
                    results.append(
                        backfiller.backfill_ohlcv_series(
                            symbol=symbol,
                            timeframe=timeframe,
                            start_at=args.start_at,
                            end_at=args.end_at,
                            chunk_span=DEFAULT_CHUNK_SPANS[timeframe],
                        )
                    )
    finally:
        client.close()
    print(
        json.dumps(
            {
                "status": "completed",
                "results": [
                    {
                        **asdict(result),
                        "start_at": result.start_at.isoformat(),
                        "end_at": result.end_at.isoformat(),
                    }
                    for result in results
                ],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
