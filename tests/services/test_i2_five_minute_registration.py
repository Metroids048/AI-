"""I-2: the 5m timeframe must be registered in the collection schedule.

RT-04 red state: `list_ohlcv_bars(symbol="BTC/USDT", timeframe="5m")` returned
nothing because 5m was declared in every other timeframe registry
(`binance.TIMEFRAME_TO_SECONDS`, `candle_validation._TIMEFRAME_DURATION`,
`repository._timeframe_to_delta`, `Timeframe.M5`) but never in the heartbeat
rotation that actually fetches bars.  Nothing collected it, so research that
needs 5m (and the 5m -> 15m aggregation cross-check) had no data at all.

These tests pin the registration itself plus the invariant that makes it safe:
the three heartbeat tables must agree on their key set, because
`_heartbeat_timeframes_to_refresh` indexes two dicts with a name taken from the
tuple and raises `KeyError` on any half-registered timeframe.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from services.data.binance import TIMEFRAME_TO_SECONDS
from services.data.candle_validation import _TIMEFRAME_DURATION
from services.data.repository import _timeframe_to_delta
from services.data.tasks import (
    _HEARTBEAT_MAX_AGE_SECONDS,
    _HEARTBEAT_TIMEFRAMES,
    _TIMEFRAME_WINDOW_SECONDS,
    _heartbeat_timeframes_to_refresh,
)
from shared.models.enums import Timeframe


def test_five_minute_is_registered_in_the_heartbeat_rotation() -> None:
    assert "5m" in _HEARTBEAT_TIMEFRAMES


def test_heartbeat_timeframe_tables_agree_on_their_key_set() -> None:
    """A timeframe present in one table but not the others raises KeyError at runtime.

    `_heartbeat_timeframes_to_refresh` looks up `_HEARTBEAT_MAX_AGE_SECONDS[candidate]`
    and `_TIMEFRAME_WINDOW_SECONDS[candidate]` for a candidate that came from
    `_HEARTBEAT_TIMEFRAMES`, so the three must stay in lockstep.
    """
    assert set(_HEARTBEAT_TIMEFRAMES) == set(_HEARTBEAT_MAX_AGE_SECONDS)
    assert set(_HEARTBEAT_TIMEFRAMES) == set(_TIMEFRAME_WINDOW_SECONDS)


def test_five_minute_window_matches_the_shared_timeframe_definition() -> None:
    """5m must mean 300 seconds everywhere, so bar identity stays comparable."""
    assert _TIMEFRAME_WINDOW_SECONDS["5m"] == 5 * 60
    assert TIMEFRAME_TO_SECONDS["5m"] == 5 * 60
    assert _TIMEFRAME_DURATION["5m"] == timedelta(minutes=5)
    assert _timeframe_to_delta("5m") == timedelta(minutes=5)
    assert Timeframe.M5.value == "5m"


def test_five_minute_staleness_tolerance_exceeds_one_window() -> None:
    """Tolerance below one window would mark a just-closed bar stale every cycle."""
    assert _HEARTBEAT_MAX_AGE_SECONDS["5m"] > _TIMEFRAME_WINDOW_SECONDS["5m"]


@pytest.mark.parametrize("timeframe", sorted(set(_HEARTBEAT_TIMEFRAMES)))
def test_every_registered_timeframe_can_be_refreshed_without_keyerror(timeframe: str) -> None:
    """Guards the half-registration failure mode directly, per timeframe."""

    class NeverFresh:
        def check_freshness(self, **_kwargs):
            return {"is_fresh": False}

        def get_latest_ohlcv_bar(self, **_kwargs):
            return None

    due = _heartbeat_timeframes_to_refresh(
        data_repo=NeverFresh(),
        symbol="BTC/USDT",
        primary_timeframe="1m",
        secondary_timeframe=timeframe,
        now=datetime(2026, 8, 9, 10, 0, 10, tzinfo=UTC),
    )
    assert due[0] == "1m"
    if timeframe != "1m":
        assert timeframe in due


def test_five_minute_refreshes_when_a_new_five_minute_window_opened() -> None:
    """5m close semantics must match the other frames: a new window is due even when
    the stored bar is still inside the freshness tolerance."""

    class FreshButPreviousWindow:
        def __init__(self, latest_at: datetime) -> None:
            self.latest_at = latest_at

        def check_freshness(self, **_kwargs):
            return {"is_fresh": True}

        def get_latest_ohlcv_bar(self, **_kwargs):
            return SimpleNamespace(timestamp=self.latest_at)

    now = datetime(2026, 8, 9, 10, 6, 10, tzinfo=UTC)

    # Previous window (10:00) closed, current window (10:05) not stored yet.
    assert _heartbeat_timeframes_to_refresh(
        data_repo=FreshButPreviousWindow(datetime(2026, 8, 9, 10, 0, tzinfo=UTC)),
        symbol="BTC/USDT",
        primary_timeframe="1m",
        secondary_timeframe="5m",
        now=now,
    ) == ["1m", "5m"]

    # Current window already stored -> no redundant fetch.
    assert _heartbeat_timeframes_to_refresh(
        data_repo=FreshButPreviousWindow(datetime(2026, 8, 9, 10, 5, tzinfo=UTC)),
        symbol="BTC/USDT",
        primary_timeframe="1m",
        secondary_timeframe="5m",
        now=now,
    ) == ["1m"]


def test_five_minute_registration_does_not_displace_the_decision_feed() -> None:
    """The 15m decision feed keeps priority; adding 5m must not starve it."""

    class PreviousDecisionWindow:
        def check_freshness(self, **_kwargs):
            return {"is_fresh": True}

        def get_latest_ohlcv_bar(self, *, timeframe: str, **_kwargs):
            if timeframe == "15m":
                return SimpleNamespace(timestamp=datetime(2026, 8, 9, 9, 45, tzinfo=UTC))
            return SimpleNamespace(timestamp=datetime(2026, 8, 9, 10, 0, tzinfo=UTC))

    due = _heartbeat_timeframes_to_refresh(
        data_repo=PreviousDecisionWindow(),
        symbol="BTC/USDT",
        primary_timeframe="1m",
        secondary_timeframe="5m",
        decision_timeframe="15m",
        now=datetime(2026, 8, 9, 10, 0, 10, tzinfo=UTC),
    )
    assert due[:2] == ["1m", "15m"]


def test_five_minute_is_seeded_at_bootstrap() -> None:
    """Bootstrap seeds the frames the gatekeeper needs; 5m joins them so a fresh
    boot does not wait for rotation to discover an empty 5m table."""
    import inspect

    from services.execution.bootstrap import bootstrap_seed_multi_timeframe_ohlcv

    source = inspect.getsource(bootstrap_seed_multi_timeframe_ohlcv)
    assert '"5m"' in source
