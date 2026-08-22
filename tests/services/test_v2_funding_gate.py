from contextlib import nullcontext
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
