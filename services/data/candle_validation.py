"""Exchange-time candle closure, freshness, continuity and alignment validation."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from shared.models import BlockCode, ValidatedCandle, ValidatedCandleSet


class MarketDataValidationError(ValueError):
    def __init__(self, block_code: BlockCode, message: str) -> None:
        super().__init__(message)
        self.block_code = block_code


@dataclass(frozen=True, slots=True)
class CandleInput:
    open_time: datetime
    close_time: datetime
    open_price: Decimal
    high_price: Decimal
    low_price: Decimal
    close_price: Decimal
    volume: Decimal
    received_at: datetime
    source_event_time: datetime | None = None
    source_closed: bool | None = None


_TIMEFRAME_DURATION = {
    "1m": timedelta(minutes=1),
    "5m": timedelta(minutes=5),
    "15m": timedelta(minutes=15),
    "1h": timedelta(hours=1),
    "4h": timedelta(hours=4),
    "1d": timedelta(days=1),
}


def _require_utc(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise MarketDataValidationError(BlockCode.DATA_MISALIGNED, f"{field_name} must be UTC")


class CandleValidator:
    def validate(
        self,
        *,
        symbol: str,
        timeframe: str,
        candles: Iterable[CandleInput],
        exchange_server_time: datetime,
        max_age: timedelta,
    ) -> ValidatedCandleSet:
        if timeframe not in _TIMEFRAME_DURATION:
            raise MarketDataValidationError(BlockCode.DATA_MISALIGNED, "unsupported timeframe")
        _require_utc(exchange_server_time, "exchange_server_time")
        duration = _TIMEFRAME_DURATION[timeframe]
        ordered = sorted(candles, key=lambda candle: candle.open_time)
        if not ordered:
            raise MarketDataValidationError(BlockCode.DATA_NOT_CLOSED, "no candles available")

        seen_open_times: set[datetime] = set()
        closed: list[CandleInput] = []
        for candle in ordered:
            _require_utc(candle.open_time, "open_time")
            _require_utc(candle.close_time, "close_time")
            if candle.open_time in seen_open_times:
                raise MarketDataValidationError(BlockCode.DATA_GAP, "duplicate candle open time")
            seen_open_times.add(candle.open_time)
            if candle.close_time - candle.open_time != duration:
                raise MarketDataValidationError(BlockCode.DATA_MISALIGNED, "candle duration mismatch")
            if candle.close_time <= exchange_server_time and candle.source_closed is not False:
                closed.append(candle)

        if not closed:
            raise MarketDataValidationError(BlockCode.DATA_NOT_CLOSED, "no exchange-time-confirmed candle")
        for previous, current in zip(closed, closed[1:], strict=False):
            if current.open_time - previous.open_time != duration:
                raise MarketDataValidationError(BlockCode.DATA_GAP, "closed candle sequence contains a gap")

        latest = closed[-1]
        if exchange_server_time - latest.close_time > max_age:
            raise MarketDataValidationError(BlockCode.DATA_STALE, "latest closed candle is stale")

        validated = tuple(
            ValidatedCandle(
                open_time=candle.open_time,
                close_time=candle.close_time,
                open_price=candle.open_price,
                high_price=candle.high_price,
                low_price=candle.low_price,
                close_price=candle.close_price,
                volume=candle.volume,
                received_at=candle.received_at,
                source_event_time=candle.source_event_time,
                closed=True,
                close_proof=(
                    "source_closed_and_exchange_server_time" if candle.source_closed is True else "exchange_server_time"
                ),
            )
            for candle in closed
        )
        return ValidatedCandleSet(
            symbol=symbol,
            timeframe=timeframe,
            candles=validated,
            validated_at=datetime.now(UTC),
            exchange_server_time=exchange_server_time,
            aligned_close_time=validated[-1].close_time,
        )

    def require_aligned(self, candle_sets: Iterable[ValidatedCandleSet]) -> datetime:
        sets = tuple(candle_sets)
        if not sets:
            raise MarketDataValidationError(BlockCode.DATA_MISALIGNED, "no candle sets to align")
        timeframes = {item.timeframe for item in sets}
        close_times = {item.aligned_close_time for item in sets}
        if len(timeframes) != 1 or len(close_times) != 1:
            raise MarketDataValidationError(BlockCode.DATA_MISALIGNED, "cross-symbol candles are not aligned")
        return sets[0].aligned_close_time
