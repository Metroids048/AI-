from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from services.data.heartbeat import MarketDataHeartbeatService
from services.data.repository import DataRepository
from shared.models import OHLCVBar, RiskEventType


def _bar(symbol: str, at: datetime) -> OHLCVBar:
    return OHLCVBar(
        symbol=symbol,
        exchange="binance",
        timeframe="1m",
        time=at,
        open=Decimal("100"),
        high=Decimal("100"),
        low=Decimal("100"),
        close=Decimal("100"),
        volume=Decimal("1"),
    )


def test_repeated_stale_checks_do_not_duplicate_risk_events(db_session) -> None:
    """Regression for the data_stale storm: one active event per symbol, not one per cycle.

    Before the fix, `check_symbol()` inserted a brand-new RiskEvent on every heartbeat
    cycle a symbol stayed stale, producing thousands of rows for a single ongoing outage.
    """
    repo = DataRepository(db_session)
    heartbeat = MarketDataHeartbeatService(data_repo=repo)
    stale_time = datetime(2024, 1, 1, tzinfo=UTC)
    repo.store_ohlcv_bars([_bar("BTC/USDT", stale_time)])

    for _ in range(5):
        result = heartbeat.check_symbol(symbol="BTC/USDT", timeframe="1m", max_delay_seconds=60)
        assert result["is_fresh"] is False

    active_events = repo.list_active_risk_events_by_type(event_type=RiskEventType.DATA_STALE)
    matching = [event for event in active_events if event.affected_scope == ["BTC/USDT"]]
    assert len(matching) == 1


def test_stale_event_resolves_once_data_is_fresh_again(db_session) -> None:
    repo = DataRepository(db_session)
    heartbeat = MarketDataHeartbeatService(data_repo=repo)
    stale_time = datetime(2024, 1, 1, tzinfo=UTC)
    repo.store_ohlcv_bars([_bar("ETH/USDT", stale_time)])

    heartbeat.check_symbol(symbol="ETH/USDT", timeframe="1m", max_delay_seconds=60)
    active_before = [
        event
        for event in repo.list_active_risk_events_by_type(event_type=RiskEventType.DATA_STALE)
        if event.affected_scope == ["ETH/USDT"]
    ]
    assert len(active_before) == 1

    repo.store_ohlcv_bars([_bar("ETH/USDT", datetime.now(UTC) - timedelta(minutes=1, seconds=5))])
    result = heartbeat.check_symbol(symbol="ETH/USDT", timeframe="1m", max_delay_seconds=60)
    assert result["is_fresh"] is True
    assert result["resolved_stale_event_count"] == 1

    active_after = [
        event
        for event in repo.list_active_risk_events_by_type(event_type=RiskEventType.DATA_STALE)
        if event.affected_scope == ["ETH/USDT"]
    ]
    assert active_after == []
