from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from services.data.candle_validation import CandleInput, CandleValidator, MarketDataValidationError
from shared.models import BlockCode


def _candle(open_time: datetime, *, minutes: int = 15, source_closed: bool | None = None) -> CandleInput:
    return CandleInput(
        open_time=open_time,
        close_time=open_time + timedelta(minutes=minutes),
        open_price=Decimal("100"),
        high_price=Decimal("102"),
        low_price=Decimal("99"),
        close_price=Decimal("101"),
        volume=Decimal("10"),
        received_at=open_time + timedelta(minutes=minutes, seconds=1),
        source_event_time=open_time + timedelta(minutes=minutes),
        source_closed=source_closed,
    )


def test_validator_selects_latest_confirmed_close_instead_of_fixed_index() -> None:
    start = datetime(2026, 7, 20, 6, 30, tzinfo=UTC)
    result = CandleValidator().validate(
        symbol="BTC/USDT:USDT",
        timeframe="15m",
        candles=[_candle(start), _candle(start + timedelta(minutes=15))],
        exchange_server_time=start + timedelta(minutes=29, seconds=59),
        max_age=timedelta(minutes=30),
    )

    assert len(result.candles) == 1
    assert result.aligned_close_time == start + timedelta(minutes=15)
    assert result.candles[-1].close_proof == "exchange_server_time"


def test_validator_rejects_gap_between_closed_candles() -> None:
    start = datetime(2026, 7, 20, 6, 0, tzinfo=UTC)

    with pytest.raises(MarketDataValidationError) as error:
        CandleValidator().validate(
            symbol="BTC/USDT:USDT",
            timeframe="15m",
            candles=[_candle(start), _candle(start + timedelta(minutes=30))],
            exchange_server_time=start + timedelta(minutes=46),
            max_age=timedelta(hours=1),
        )

    assert error.value.block_code is BlockCode.DATA_GAP


def test_cross_symbol_alignment_rejects_different_close_times() -> None:
    start = datetime(2026, 7, 20, 6, 0, tzinfo=UTC)
    validator = CandleValidator()
    btc = validator.validate(
        symbol="BTC/USDT:USDT",
        timeframe="15m",
        candles=[_candle(start)],
        exchange_server_time=start + timedelta(minutes=16),
        max_age=timedelta(hours=1),
    )
    eth = validator.validate(
        symbol="ETH/USDT:USDT",
        timeframe="15m",
        candles=[_candle(start + timedelta(minutes=15))],
        exchange_server_time=start + timedelta(minutes=31),
        max_age=timedelta(hours=1),
    )

    with pytest.raises(MarketDataValidationError) as error:
        validator.require_aligned([btc, eth])

    assert error.value.block_code is BlockCode.DATA_MISALIGNED
