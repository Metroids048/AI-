from __future__ import annotations

from sqlalchemy import inspect, text

from scripts.prepare_database import prepare_database
from services.database import get_engine, reset_database_caches


def test_prepare_database_runs_migrations_and_local_runtime_schema(tmp_path) -> None:
    database_url = f"sqlite:///{(tmp_path / 'prepared.db').as_posix()}"
    try:
        prepare_database(database_url)
        tables = set(inspect(get_engine()).get_table_names())
        assert "strategies" in tables
        assert "ohlcv_bars" in tables
        assert "risk_events" in tables
        assert "strategy_roadmap_states" in tables
        position_columns = {column["name"] for column in inspect(get_engine()).get_columns("position_snapshots")}
        assert {"hedge_group_id", "is_hedge_leg"}.issubset(position_columns)
    finally:
        reset_database_caches()


def test_prepare_database_tolerates_preexisting_hedge_columns_at_0008(tmp_path) -> None:
    """Reproduce local console: alembic stuck at 0008 while hedge DDL already applied."""

    database_url = f"sqlite:///{(tmp_path / 'stale_hedge.db').as_posix()}"
    try:
        prepare_database(database_url)
        with get_engine().begin() as connection:
            # Schema already has hedge fields; stamp back so upgrade 0008→0009 must be idempotent.
            connection.execute(text("UPDATE alembic_version SET version_num = '0008'"))
        reset_database_caches()
        prepare_database(database_url)
        with get_engine().connect() as connection:
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0009"
        position_columns = {column["name"] for column in inspect(get_engine()).get_columns("position_snapshots")}
        assert {"hedge_group_id", "is_hedge_leg"}.issubset(position_columns)
    finally:
        reset_database_caches()
