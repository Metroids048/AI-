from __future__ import annotations

from sqlalchemy import inspect

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
