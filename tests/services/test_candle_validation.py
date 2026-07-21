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


# ---------------------------------------------------------------------------
# DATA_NOT_CLOSED — current candle is still open
# ---------------------------------------------------------------------------


def test_current_bar_still_open_raises_data_not_closed() -> None:
    """Server time is before the close of the only candle → nothing confirmed closed."""
    start = datetime(2026, 7, 20, 6, 0, tzinfo=UTC)
    with pytest.raises(MarketDataValidationError) as exc:
        CandleValidator().validate(
            symbol="BTC/USDT:USDT",
            timeframe="15m",
            candles=[_candle(start)],
            exchange_server_time=start + timedelta(minutes=14, seconds=59),
            max_age=timedelta(hours=1),
        )
    assert exc.value.block_code is BlockCode.DATA_NOT_CLOSED


def test_candle_closes_exactly_at_server_time_is_accepted() -> None:
    """close_time == exchange_server_time satisfies the <= boundary."""
    start = datetime(2026, 7, 20, 6, 0, tzinfo=UTC)
    result = CandleValidator().validate(
        symbol="BTC/USDT:USDT",
        timeframe="15m",
        candles=[_candle(start)],
        exchange_server_time=start + timedelta(minutes=15),
        max_age=timedelta(hours=1),
    )
    assert len(result.candles) == 1


def test_empty_candle_list_raises_data_not_closed() -> None:
    with pytest.raises(MarketDataValidationError) as exc:
        CandleValidator().validate(
            symbol="BTC/USDT:USDT",
            timeframe="15m",
            candles=[],
            exchange_server_time=datetime(2026, 7, 20, 6, 15, tzinfo=UTC),
            max_age=timedelta(hours=1),
        )
    assert exc.value.block_code is BlockCode.DATA_NOT_CLOSED


# ---------------------------------------------------------------------------
# DATA_STALE — latest closed candle is too old
# ---------------------------------------------------------------------------


def test_stale_data_raises_data_stale() -> None:
    start = datetime(2026, 7, 20, 5, 0, tzinfo=UTC)
    with pytest.raises(MarketDataValidationError) as exc:
        CandleValidator().validate(
            symbol="BTC/USDT:USDT",
            timeframe="15m",
            candles=[_candle(start)],
            exchange_server_time=start + timedelta(hours=2),
            max_age=timedelta(minutes=30),
        )
    assert exc.value.block_code is BlockCode.DATA_STALE


# ---------------------------------------------------------------------------
# DATA_MISALIGNED — non-UTC timestamps
# ---------------------------------------------------------------------------


def test_non_utc_open_time_raises_data_misaligned() -> None:
    import pytz

    tz = pytz.timezone("Asia/Shanghai")
    naive = datetime(2026, 7, 20, 14, 0)
    aware_shanghai = tz.localize(naive)
    bad_candle = CandleInput(
        open_time=aware_shanghai,
        close_time=aware_shanghai + timedelta(minutes=15),
        open_price=Decimal("100"),
        high_price=Decimal("102"),
        low_price=Decimal("99"),
        close_price=Decimal("101"),
        volume=Decimal("10"),
        received_at=aware_shanghai + timedelta(minutes=15),
    )
    with pytest.raises(MarketDataValidationError) as exc:
        CandleValidator().validate(
            symbol="BTC/USDT:USDT",
            timeframe="15m",
            candles=[bad_candle],
            exchange_server_time=datetime(2026, 7, 20, 6, 15, tzinfo=UTC),
            max_age=timedelta(hours=1),
        )
    assert exc.value.block_code is BlockCode.DATA_MISALIGNED


def test_unsupported_timeframe_raises_data_misaligned() -> None:
    start = datetime(2026, 7, 20, 6, 0, tzinfo=UTC)
    with pytest.raises(MarketDataValidationError) as exc:
        CandleValidator().validate(
            symbol="BTC/USDT:USDT",
            timeframe="3m",
            candles=[_candle(start)],
            exchange_server_time=start + timedelta(minutes=5),
            max_age=timedelta(hours=1),
        )
    assert exc.value.block_code is BlockCode.DATA_MISALIGNED


# ---------------------------------------------------------------------------
# DATA_GAP — duplicate open_time
# ---------------------------------------------------------------------------


def test_duplicate_open_time_raises_data_gap() -> None:
    start = datetime(2026, 7, 20, 6, 0, tzinfo=UTC)
    with pytest.raises(MarketDataValidationError) as exc:
        CandleValidator().validate(
            symbol="BTC/USDT:USDT",
            timeframe="15m",
            candles=[_candle(start), _candle(start)],
            exchange_server_time=start + timedelta(minutes=16),
            max_age=timedelta(hours=1),
        )
    assert exc.value.block_code is BlockCode.DATA_GAP


# ---------------------------------------------------------------------------
# close_proof label
# ---------------------------------------------------------------------------


def test_source_closed_candle_gets_authoritative_proof_label() -> None:
    start = datetime(2026, 7, 20, 6, 0, tzinfo=UTC)
    result = CandleValidator().validate(
        symbol="BTC/USDT:USDT",
        timeframe="15m",
        candles=[_candle(start, source_closed=True)],
        exchange_server_time=start + timedelta(minutes=16),
        max_age=timedelta(hours=1),
    )
    assert result.candles[0].close_proof == "source_closed_and_exchange_server_time"


# ---------------------------------------------------------------------------
# require_aligned — same close times pass
# ---------------------------------------------------------------------------


def test_aligned_candle_sets_pass() -> None:
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
        candles=[_candle(start)],
        exchange_server_time=start + timedelta(minutes=16),
        max_age=timedelta(hours=1),
    )
    aligned_time = validator.require_aligned([btc, eth])
    assert aligned_time == start + timedelta(minutes=15)
