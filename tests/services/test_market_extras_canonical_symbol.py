"""T0-01 RT-01/02/03: market_extras must persist and be queryable by canonical symbol.

Background: the funding/OI writer path went through
``spot_to_usdm_perp_symbol()`` and persisted ``BTC/USDT:USDT``, while every
V2 reader queries the canonical ``BTC/USDT`` with an exact-equality filter.
Result: ``funding_rate`` / ``open_interest`` were structurally unreachable and
``MarketContext`` reported them as permanently missing.

Contract under test (operator decision D-A):
- DB / internal SSOT stores canonical symbols only.
- The repository boundary accepts legacy-shaped input and normalizes it, so a
  single query runs against a single truth. This is a boundary converter, not
  a dual-query legacy fallback.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from services.data.repository import DataRepository
from shared.models import Exchange, MarketExtras, OHLCVBar, Timeframe

BASE = datetime(2026, 3, 1, tzinfo=UTC)


def _extra(symbol: str, at: datetime, *, funding: str = "0.0001", oi: str = "1234.5") -> MarketExtras:
    return MarketExtras(
        symbol=symbol,
        time=at,
        funding_rate=Decimal(funding),
        open_interest=Decimal(oi),
    )


def _bar(symbol: str, at: datetime, close: str, *, timeframe: str) -> OHLCVBar:
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


@pytest.mark.parametrize(
    ("exchange_shaped", "canonical"),
    [
        ("BTC/USDT:USDT", "BTC/USDT"),  # RT-01
        ("ETH/USDT:USDT", "ETH/USDT"),  # RT-02
    ],
)
def test_exchange_shaped_write_is_queryable_by_canonical_symbol(
    db_session, exchange_shaped: str, canonical: str
) -> None:
    """RT-01 / RT-02: writing the exchange form must be readable canonically."""
    repo = DataRepository(db_session)
    at = BASE + timedelta(minutes=5)

    assert repo.store_market_extras([_extra(exchange_shaped, at)]) == 1

    loaded = repo.list_market_extras(symbol=canonical, start_at=at, end_at=at)

    assert loaded, f"canonical query for {canonical} returned nothing"
    assert loaded[0].funding_rate == Decimal("0.0001")
    assert loaded[0].open_interest == Decimal("1234.5")
    # The persisted identity itself must be canonical — not merely findable.
    assert loaded[0].symbol == canonical


def test_persisted_symbol_is_canonical_in_storage(db_session) -> None:
    """The stored row must carry the canonical identity, so DB is single-truth."""
    from sqlalchemy import select

    from services.data.repository import market_extras

    repo = DataRepository(db_session)
    at = BASE + timedelta(minutes=10)
    repo.store_market_extras([_extra("BTC/USDT:USDT", at)])

    stored = db_session.execute(select(market_extras.c.symbol).where(market_extras.c.time == at)).scalars().all()

    assert stored, "no row persisted"
    assert all(not symbol.endswith(":USDT") for symbol in stored), stored


def test_legacy_shaped_reader_input_still_resolves(db_session) -> None:
    """Existing readers pass the legacy perp form and must keep working.

    ``services/data/market.py`` builds ``f"{symbol}:USDT"`` and
    ``CarryExecutionRequest.perp_symbol`` defaults to ``BTC/USDT:USDT``.
    Migration must not silently break them.
    """
    repo = DataRepository(db_session)
    at = BASE + timedelta(minutes=15)
    repo.store_market_extras([_extra("BTC/USDT", at)])

    via_legacy = repo.get_latest_market_extras(symbol="BTC/USDT:USDT")

    assert via_legacy is not None
    assert via_legacy.funding_rate == Decimal("0.0001")


def test_both_shapes_collapse_to_one_row(db_session) -> None:
    """Storing both shapes for one timestamp must not create duplicate rows."""
    from sqlalchemy import func, select

    from services.data.repository import market_extras

    repo = DataRepository(db_session)
    at = BASE + timedelta(minutes=20)

    repo.store_market_extras([_extra("BTC/USDT:USDT", at, funding="0.0002")])
    repo.store_market_extras([_extra("BTC/USDT", at, funding="0.0003")])

    # Count every row at this timestamp regardless of shape: before the fix the
    # two writes land as two separate identities, which is the defect itself.
    total = db_session.execute(
        select(func.count()).select_from(market_extras).where(market_extras.c.time == at)
    ).scalar_one()
    canonical = db_session.execute(
        select(func.count())
        .select_from(market_extras)
        .where(market_extras.c.time == at, market_extras.c.symbol == "BTC/USDT")
    ).scalar_one()

    assert total == 1, f"expected one row for this timestamp, found {total} (split identities)"
    assert canonical == 1, f"expected the surviving row to be canonical, found {canonical}"
    latest = repo.get_latest_market_extras(symbol="BTC/USDT")
    assert latest is not None
    assert latest.funding_rate == Decimal("0.0003"), "second write must upsert, not duplicate"


def test_market_context_exposes_derivatives_after_canonical_write(db_session) -> None:
    """RT-03: MarketContext.derivatives must be populated, not permanently missing."""
    from services.strategy_library.context import MarketContextBuilder

    repo = DataRepository(db_session)
    symbol = "BTC/USDT"
    decision_time = BASE + timedelta(hours=8)

    # Funding arrives from the exchange in the legacy perp shape.
    repo.store_market_extras([_extra("BTC/USDT:USDT", decision_time - timedelta(minutes=30))])

    bars_by_timeframe = {}
    for timeframe, delta in (
        ("1m", timedelta(minutes=1)),
        ("5m", timedelta(minutes=5)),
        ("15m", timedelta(minutes=15)),
        ("1h", timedelta(hours=1)),
        ("4h", timedelta(hours=4)),
    ):
        bars_by_timeframe[timeframe] = [
            _bar(symbol, decision_time - delta * (index + 1), "64000", timeframe=timeframe) for index in range(3)
        ]

    context = MarketContextBuilder().build(
        symbol=symbol,
        decision_time=decision_time,
        bars_by_timeframe=bars_by_timeframe,
        market_extras=repo.list_market_extras(symbol=symbol, end_at=decision_time, limit=1),
        source_ids=("test",),
    )

    assert context.derivatives.funding_rate == Decimal("0.0001")
    assert context.derivatives.open_interest == Decimal("1234.5")
    assert "funding_rate:missing" not in context.missing_features
    assert "open_interest:missing" not in context.missing_features
