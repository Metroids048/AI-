from __future__ import annotations

from datetime import UTC, datetime


class _SessionContext:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def test_entry_data_readiness_refreshes_only_stale_timeframes(monkeypatch) -> None:
    from services.execution import scheduler

    calls: list[tuple[str, str]] = []
    freshness = {
        (symbol, timeframe): {"is_fresh": timeframe != "1h"}
        for symbol in ("BTC/USDT", "ETH/USDT")
        for timeframe in ("15m", "1h", "4h")
    }

    class FakeRepository:
        def __init__(self, _session):
            pass

        def check_freshness(self, *, symbol, timeframe, **_kwargs):
            return freshness[(symbol, timeframe)]

    class FakeHeartbeat:
        @staticmethod
        def run(symbols, timeframe, *, include_secondary):
            assert include_secondary is False
            calls.append((symbols[0], timeframe))
            return {"status": "ok"}

    monkeypatch.setattr("services.data.repository.DataRepository", FakeRepository)
    monkeypatch.setattr("services.database.get_session_factory", lambda: lambda: _SessionContext())
    monkeypatch.setattr("services.data.tasks.market_data_heartbeat", FakeHeartbeat)

    result = scheduler.ensure_v2_entry_data_ready(now=datetime(2026, 9, 3, tzinfo=UTC))

    assert result["ready"] is True
    assert result["pending"] == []
    assert calls == [("BTC/USDT", "1h"), ("ETH/USDT", "1h")]


def test_entry_data_refresh_failure_is_pending_without_raising(monkeypatch) -> None:
    from services.execution import scheduler

    class FakeRepository:
        def __init__(self, _session):
            pass

        def check_freshness(self, **_kwargs):
            return {"is_fresh": False}

    class FailingHeartbeat:
        @staticmethod
        def run(*_args, **_kwargs):
            raise TimeoutError("timeout")

    monkeypatch.setattr("services.data.repository.DataRepository", FakeRepository)
    monkeypatch.setattr("services.database.get_session_factory", lambda: lambda: _SessionContext())
    monkeypatch.setattr("services.data.tasks.market_data_heartbeat", FailingHeartbeat)

    result = scheduler.ensure_v2_entry_data_ready(now=datetime(2026, 9, 3, tzinfo=UTC))

    assert result["ready"] is False
    assert len(result["pending"]) == 6
    assert all(item["status"] == "pending" for item in result["details"].values())
