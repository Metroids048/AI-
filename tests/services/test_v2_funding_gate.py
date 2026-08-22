from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

from services.execution import v2_scheduler_entry


def test_scheduler_reads_latest_market_extras_as_funding_bps(monkeypatch) -> None:
    class FakeRepository:
        def __init__(self, _session) -> None:
            pass

        def get_latest_market_extras(self, *, symbol: str):
            assert symbol == "BTC/USDT"
            return SimpleNamespace(funding_rate=Decimal("0.0005"))

    monkeypatch.setattr(v2_scheduler_entry, "DataRepository", FakeRepository)
    monkeypatch.setattr(v2_scheduler_entry, "get_session_factory", lambda: lambda: nullcontext(object()))

    assert v2_scheduler_entry._load_latest_funding_bps(symbol="BTC/USDT") == Decimal("5.0000")


def test_scheduler_marks_missing_market_funding_as_unavailable(monkeypatch) -> None:
    class FakeRepository:
        def __init__(self, _session) -> None:
            pass

        def get_latest_market_extras(self, *, symbol: str):
            return SimpleNamespace(funding_rate=None)

    monkeypatch.setattr(v2_scheduler_entry, "DataRepository", FakeRepository)
    monkeypatch.setattr(v2_scheduler_entry, "get_session_factory", lambda: lambda: nullcontext(object()))

    assert v2_scheduler_entry._load_latest_funding_bps(symbol="BTC/USDT") is None


def test_funding_loader_reports_freshness_and_observed_timestamp(monkeypatch) -> None:
    observed_at = datetime(2026, 8, 22, 12, 0, 0, tzinfo=UTC)

    class FakeRepository:
        def __init__(self, _session) -> None:
            pass

        def get_latest_market_extras(self, *, symbol: str):
            return SimpleNamespace(funding_rate=Decimal("0.0005"), timestamp=observed_at)

    monkeypatch.setattr(v2_scheduler_entry, "DataRepository", FakeRepository)
    monkeypatch.setattr(v2_scheduler_entry, "get_session_factory", lambda: lambda: nullcontext(object()))

    observation = v2_scheduler_entry._load_funding_observation(
        symbol="BTC/USDT",
        now=observed_at + timedelta(seconds=5),
    )

    assert observation.raw_funding_bps == Decimal("5.0000")
    assert observation.observed_at == observed_at
    assert observation.age_seconds == 5
    assert observation.status == "FRESH"


def test_funding_loader_marks_stale_history_without_reading_future(monkeypatch) -> None:
    now = datetime(2026, 8, 22, 12, 0, 0, tzinfo=UTC)

    class FakeRepository:
        def __init__(self, _session) -> None:
            pass

        def get_latest_market_extras(self, *, symbol: str):
            return SimpleNamespace(
                funding_rate=Decimal("0.0005"),
                timestamp=now - timedelta(seconds=v2_scheduler_entry.FUNDING_FRESHNESS_TOLERANCE_SECONDS + 1),
            )

    monkeypatch.setattr(v2_scheduler_entry, "DataRepository", FakeRepository)
    monkeypatch.setattr(v2_scheduler_entry, "get_session_factory", lambda: lambda: nullcontext(object()))
    stale = v2_scheduler_entry._load_funding_observation(symbol="BTC/USDT", now=now)
    assert stale.status == "STALE"
    assert stale.raw_funding_bps == Decimal("5.0000")

    class FutureRepository(FakeRepository):
        def get_latest_market_extras(self, *, symbol: str):
            return SimpleNamespace(funding_rate=Decimal("0.0005"), timestamp=now + timedelta(seconds=1))

    monkeypatch.setattr(v2_scheduler_entry, "DataRepository", FutureRepository)
    future = v2_scheduler_entry._load_funding_observation(symbol="BTC/USDT", now=now)
    assert future.status == "MISSING"
    assert future.raw_funding_bps is None


def test_current_funding_refresh_writes_same_canonical_market_extras(monkeypatch) -> None:
    observed_at = datetime(2026, 8, 22, 12, 0, 0, tzinfo=UTC)
    writes = []

    class FakeClient:
        def fetch_premium_index(self, *, symbol: str):
            assert symbol == "BTC/USDT"
            return SimpleNamespace(timestamp=observed_at, funding_rate=Decimal("0.0005"))

    class FakeRepository:
        def __init__(self, _session) -> None:
            pass

        def store_market_extras(self, extras):
            writes.extend(extras)
            return 1

    monkeypatch.setattr("services.data.binance.BinanceCcxtClient", FakeClient)
    monkeypatch.setattr(v2_scheduler_entry, "DataRepository", FakeRepository)
    monkeypatch.setattr(v2_scheduler_entry, "get_session_factory", lambda: lambda: nullcontext(object()))

    assert v2_scheduler_entry._refresh_current_funding(symbol="BTC/USDT", now=observed_at) is True
    assert writes and writes[0].funding_rate == Decimal("0.0005")
