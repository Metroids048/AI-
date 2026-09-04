from __future__ import annotations

from datetime import UTC, datetime, timedelta


class _SessionContext:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def test_entry_data_readiness_refreshes_only_stale_timeframes(monkeypatch) -> None:
    from services.execution import scheduler

    calls: list[tuple[str, str]] = []
    freshness = {
        (symbol, timeframe): {
            "is_fresh": timeframe != "1h",
            "latest_close_time": None if timeframe == "1h" else datetime(2026, 9, 3, tzinfo=UTC),
        }
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
            freshness[(symbols[0], timeframe)] = {
                "is_fresh": True,
                "latest_close_time": datetime(2026, 9, 3, tzinfo=UTC),
            }
            return {"status": "ok"}

    monkeypatch.setattr("services.data.repository.DataRepository", FakeRepository)
    monkeypatch.setattr("services.database.get_session_factory", lambda: lambda: _SessionContext())
    monkeypatch.setattr("services.data.tasks.market_data_heartbeat", FakeHeartbeat)

    result = scheduler.ensure_v2_entry_data_ready(now=datetime(2026, 9, 3, tzinfo=UTC))

    assert result["ready"] is True
    assert result["pending"] == []
    assert calls == [("BTC/USDT", "1h"), ("ETH/USDT", "1h")]


def test_entry_data_readiness_refreshes_a_fresh_but_previous_closed_bar(monkeypatch) -> None:
    """Freshness tolerance cannot substitute for the V2 exact closed-bar contract."""
    from services.execution import scheduler

    now = datetime(2026, 9, 3, 10, 17, tzinfo=UTC)
    expected_closed = {
        "15m": datetime(2026, 9, 3, 10, 15, tzinfo=UTC),
        "1h": datetime(2026, 9, 3, 10, tzinfo=UTC),
        "4h": datetime(2026, 9, 3, 8, tzinfo=UTC),
    }
    latest_close = {
        (symbol, timeframe): {
            "15m": now - timedelta(minutes=17),
            "1h": expected_closed["1h"],
            "4h": expected_closed["4h"],
        }[timeframe]
        for symbol in ("BTC/USDT", "ETH/USDT")
        for timeframe in ("15m", "1h", "4h")
    }
    calls: list[tuple[str, str]] = []

    class FakeRepository:
        def __init__(self, _session):
            pass

        def check_freshness(self, *, symbol, timeframe, **_kwargs):
            return {"is_fresh": True, "latest_close_time": latest_close[(symbol, timeframe)]}

    class FakeHeartbeat:
        @staticmethod
        def run(symbols, timeframe, *, include_secondary):
            assert include_secondary is False
            calls.append((symbols[0], timeframe))
            latest_close[(symbols[0], timeframe)] = expected_closed[timeframe]
            return {"status": "ok"}

    monkeypatch.setattr("services.data.repository.DataRepository", FakeRepository)
    monkeypatch.setattr("services.database.get_session_factory", lambda: lambda: _SessionContext())
    monkeypatch.setattr("services.data.tasks.market_data_heartbeat", FakeHeartbeat)

    result = scheduler.ensure_v2_entry_data_ready(now=now)

    assert result["ready"] is True
    assert result["pending"] == []
    assert calls == [("BTC/USDT", "15m"), ("ETH/USDT", "15m")]


def test_entry_data_readiness_marks_refresh_pending_until_closed_bar_is_observed(monkeypatch) -> None:
    """A nominal REST success is not proof that a closed entry bar was stored."""
    from services.execution import scheduler

    class FakeRepository:
        def __init__(self, _session):
            pass

        def check_freshness(self, **_kwargs):
            return {"is_fresh": True, "latest_close_time": None}

    class NoopHeartbeat:
        @staticmethod
        def run(*_args, **_kwargs):
            return {"status": "ok"}

    monkeypatch.setattr("services.data.repository.DataRepository", FakeRepository)
    monkeypatch.setattr("services.database.get_session_factory", lambda: lambda: _SessionContext())
    monkeypatch.setattr("services.data.tasks.market_data_heartbeat", NoopHeartbeat)

    result = scheduler.ensure_v2_entry_data_ready(now=datetime(2026, 9, 3, tzinfo=UTC))

    assert result["ready"] is False
    assert len(result["pending"]) == 6
    assert all(item["status"] == "pending" for item in result["details"].values())


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
