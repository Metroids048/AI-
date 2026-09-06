"""Shared pytest fixtures.

Integration tests that need a live DB/redis are marked `integration` and skipped
unless POSTGRES_URL points at a reachable database.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Generator
from contextlib import suppress
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import inspect
from sqlalchemy.orm import Session

from tests.runtime_safety import RuntimeSafetyGuard, UnsafeTestResourceError

ROOT = Path(__file__).resolve().parents[1]
TEST_DB_DIR = ROOT / ".local" / "test-runtime" / f"pytest-{os.getpid()}"
TEST_DB_DIR.mkdir(parents=True, exist_ok=True)
TEST_DB_PATH = TEST_DB_DIR / f"pytest_ai_quant.{os.getpid()}.db"
TEST_DB_URL = f"sqlite:///{TEST_DB_PATH.as_posix()}"
RUNTIME_ENV = {
    "POSTGRES_URL": TEST_DB_URL,
    "LOCAL_SCHEDULER_STATE_PATH": str(TEST_DB_DIR / "scheduler-state.json"),
    "V2_ACCOUNT_WRITER_REGISTRY_PATH": str(TEST_DB_DIR / "account-writer-registry.json"),
}
registry_base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".local" / "share")
SAFETY_GUARD = RuntimeSafetyGuard(
    [TEST_DB_DIR],
    [
        ROOT / ".local_paper_console.db",
        ROOT / "logs" / "scheduler-state.json",
        registry_base / "AIQuant" / "account-writer-registry.json",
        *(Path(os.environ[key]) for key in RUNTIME_ENV if key.endswith("_PATH") and os.environ.get(key)),
    ],
)
SAFETY_GUARD.install()
SAFETY_GUARD.guard_sqlite_attachments()
os.environ.update(RUNTIME_ENV)


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
def _bootstrap_sqlite_schema(tmp_path_factory: pytest.TempPathFactory) -> Generator[None, None, None]:
    from services.data.repository import create_timeseries_schema
    from services.database import create_relational_schema, get_engine, reset_database_caches

    SAFETY_GUARD.allow_root(tmp_path_factory.getbasetemp())
    if TEST_DB_PATH.exists():
        TEST_DB_PATH.unlink()
    reset_database_caches()
    create_relational_schema(TEST_DB_URL)
    engine = get_engine(TEST_DB_URL)
    create_timeseries_schema(engine)
    yield
    engine.dispose()
    reset_database_caches()
    if TEST_DB_PATH.exists():
        with suppress(PermissionError):
            TEST_DB_PATH.unlink()


@pytest.fixture(autouse=True)
def _isolate_runtime_resources(_bootstrap_sqlite_schema, tmp_path) -> Generator[None, None, None]:
    from services.database import get_engine, reset_database_caches

    # Own the restoration even when command entry points write os.environ directly.
    with pytest.MonkeyPatch.context() as patch:
        for key, value in RUNTIME_ENV.items():
            patch.setenv(key, value)
        patch.setenv("LOCAL_SCHEDULER_STATE_PATH", str(tmp_path / "scheduler-state.json"))
        patch.setenv("V2_ACCOUNT_WRITER_REGISTRY_PATH", str(tmp_path / "account-writer-registry.json"))
        patch.setattr(tempfile, "tempdir", str(tmp_path))
        for key in ("TMP", "TEMP", "TMPDIR"):
            patch.setenv(key, str(tmp_path))
        for key in ("AUTOMATED_TRADING_ENGINE", "BINANCE_USE_TESTNET", "LIVE_TRADING_ENABLED"):
            if key in os.environ:
                patch.setenv(key, os.environ[key])
            else:
                patch.delenv(key, raising=False)
        reset_database_caches()
        engine = get_engine(TEST_DB_URL)
        try:
            yield
        finally:
            engine.dispose()
            reset_database_caches()


@pytest.fixture
def allow_readonly_database():
    previous = set(SAFETY_GUARD.readonly_databases)
    try:
        yield lambda path: SAFETY_GUARD.readonly_databases.add(path.resolve())
    finally:
        SAFETY_GUARD.readonly_databases = previous


@pytest.fixture(autouse=True)
def _clear_all_tables(_isolate_runtime_resources) -> None:
    from services.data.repository import TIMESERIES_METADATA
    from services.database import get_engine
    from services.strategy_library.models import Base

    engine = get_engine(TEST_DB_URL)
    if engine.dialect.name != "sqlite" or Path(engine.url.database or "").resolve() != TEST_DB_PATH:
        raise UnsafeTestResourceError("PYTEST_REFUSED_CLEANUP_ENGINE")
    with engine.begin() as connection:
        databases = connection.exec_driver_sql("PRAGMA database_list").all()
        if any(name != "temp" and Path(path).resolve() != TEST_DB_PATH for _, name, path in databases):
            raise UnsafeTestResourceError("PYTEST_REFUSED_CLEANUP_DATABASE")
        existing_tables = set(inspect(connection).get_table_names())
        for table in reversed(TIMESERIES_METADATA.sorted_tables):
            if table.name not in existing_tables:
                continue
            connection.execute(table.delete())
        for table in reversed(Base.metadata.sorted_tables):
            if table.name not in existing_tables:
                continue
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

    monkeypatch.setattr(settings, "binance_api_key", "")
    monkeypatch.setattr(settings, "binance_api_secret", "")
    monkeypatch.setattr(settings, "binance_auto_execute", False)
    monkeypatch.setattr(settings, "binance_live_ws_enabled", False)
    monkeypatch.setattr(settings, "binance_live_market_enabled", False)
    monkeypatch.setattr(settings, "binance_live_universe_enabled", False)
