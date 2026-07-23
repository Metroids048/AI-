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
        assert "trading_config_snapshots" in tables
        assert "decision_events" in tables
        assert "scheduler_leases" in tables
        assert "scheduler_cycles" in tables
        assert "position_records" in tables
        assert "protection_records" in tables
        paper_run_columns = {column["name"] for column in inspect(get_engine()).get_columns("paper_runs")}
        assert {
            "active_config_snapshot_id",
            "active_config_hash",
            "pending_config_snapshot_id",
            "pending_config_hash",
        }.issubset(paper_run_columns)
        order_columns = {column["name"] for column in inspect(get_engine()).get_columns("order_executions")}
        assert {
            "intent_id",
            "cycle_id",
            "decision_id",
            "config_snapshot_id",
            "config_hash",
            "normalized_order",
            "intent_type",
            "timeframe",
            "signal_candle_close_time",
            "order_origin",
            "run_mode",
            "scheduler_instance_id",
            "scheduled_for",
            "position_record_id",
        }.issubset(order_columns)
        with get_engine().connect() as connection:
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0012"
        position_columns = {column["name"] for column in inspect(get_engine()).get_columns("position_snapshots")}
        assert {"hedge_group_id", "is_hedge_leg", "position_record_id"}.issubset(position_columns)
        lease_columns = {column["name"] for column in inspect(get_engine()).get_columns("scheduler_leases")}
        assert "fencing_token" in lease_columns
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
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0012"
        position_columns = {column["name"] for column in inspect(get_engine()).get_columns("position_snapshots")}
        assert {"hedge_group_id", "is_hedge_leg"}.issubset(position_columns)
    finally:
        reset_database_caches()
