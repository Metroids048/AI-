"""Shared pytest fixtures.

Integration tests that need a live DB/redis are marked `integration` and skipped
unless POSTGRES_URL points at a reachable database.
"""

from __future__ import annotations

import os
from collections.abc import Generator
from contextlib import suppress
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

TEST_DB_PATH = Path(f".pytest_ai_quant.{os.getpid()}.db").resolve()
os.environ["POSTGRES_URL"] = f"sqlite:///{TEST_DB_PATH.as_posix()}"


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "integration: needs a live DB/redis")


@pytest.fixture
def sample_ohlcv_bar() -> dict:
    return {
        "symbol": "BTC/USDT",
        "exchange": "binance",
        "timeframe": "1h",
        "time": datetime(2024, 1, 1, tzinfo=UTC),
        "open": Decimal("42000"),
        "high": Decimal("42500"),
        "low": Decimal("41800"),
        "close": Decimal("42300"),
        "volume": Decimal("123.45"),
    }


@pytest.fixture
def api_client():
    from fastapi.testclient import TestClient

    from apps.api.config import settings
    from apps.api.main import app

    return TestClient(app, headers={"Authorization": f"Bearer {settings.admin_api_token}"})


@pytest.fixture
def unauth_api_client():
    from fastapi.testclient import TestClient

    from apps.api.main import app

    return TestClient(app)


@pytest.fixture(scope="session", autouse=True)
def _bootstrap_sqlite_schema() -> Generator[None, None, None]:
    from services.data.repository import create_timeseries_schema
    from services.database import create_relational_schema, get_engine, reset_database_caches

    if TEST_DB_PATH.exists():
        TEST_DB_PATH.unlink()
    reset_database_caches()
    create_relational_schema()
    create_timeseries_schema(get_engine())
    yield
    get_engine().dispose()
    reset_database_caches()
    if TEST_DB_PATH.exists():
        with suppress(PermissionError):
            TEST_DB_PATH.unlink()


@pytest.fixture(autouse=True)
def _clear_all_tables() -> None:
    from services.data.repository import TIMESERIES_METADATA
    from services.database import get_engine
    from services.strategy_library.models import Base

    with get_engine().begin() as connection:
        for table in reversed(TIMESERIES_METADATA.sorted_tables):
            connection.execute(table.delete())
        for table in reversed(Base.metadata.sorted_tables):
            connection.execute(table.delete())


@pytest.fixture
def db_session() -> Generator[Session, None, None]:
    from services.database import get_session_factory

    session = get_session_factory()()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture(autouse=True)
def _skip_integration_without_db(request: pytest.FixtureRequest) -> None:
    if request.node.get_closest_marker("integration") and not os.getenv("POSTGRES_URL"):
        pytest.skip("integration test requires POSTGRES_URL")


@pytest.fixture(autouse=True)
def _disable_live_binance_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    from apps.api.config import settings

    monkeypatch.setattr(settings, "binance_live_ws_enabled", False)
    monkeypatch.setattr(settings, "binance_live_market_enabled", False)
    monkeypatch.setattr(settings, "binance_live_universe_enabled", False)
