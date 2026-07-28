from __future__ import annotations

import pytest
from sqlalchemy import inspect, text


def test_create_local_runtime_schema_includes_relational_and_runtime_tables(tmp_path) -> None:
    from services.database import create_local_runtime_schema, get_engine, reset_database_caches

    database_url = f"sqlite:///{(tmp_path / 'runtime.db').as_posix()}"
    try:
        create_local_runtime_schema(database_url)
        inspector = inspect(get_engine(database_url))
        assert inspector.has_table("strategies")
        assert inspector.has_table("risk_events")
    finally:
        get_engine(database_url).dispose()
        reset_database_caches()


def test_adopt_complete_legacy_sqlite_schema_for_alembic_without_losing_data(tmp_path) -> None:
    from services.database import (
        adopt_complete_legacy_sqlite_schema,
        create_relational_schema,
        get_engine,
        reset_database_caches,
    )

    database_url = f"sqlite:///{(tmp_path / 'legacy.db').as_posix()}"
    try:
        create_relational_schema(database_url)
        engine = get_engine(database_url)
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE legacy_marker (value VARCHAR(40) NOT NULL)"))
            connection.execute(text("INSERT INTO legacy_marker (value) VALUES ('keep me')"))

        assert adopt_complete_legacy_sqlite_schema(database_url, head_revision="0006") is True

        with engine.connect() as connection:
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0006"
            assert connection.execute(text("SELECT value FROM legacy_marker")).scalar_one() == "keep me"
    finally:
        get_engine(database_url).dispose()
        reset_database_caches()


def test_incomplete_legacy_sqlite_schema_is_not_adopted(tmp_path) -> None:
    from services.database import adopt_complete_legacy_sqlite_schema, get_engine, reset_database_caches

    database_url = f"sqlite:///{(tmp_path / 'incomplete.db').as_posix()}"
    try:
        engine = get_engine(database_url)
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE strategies (strategy_id VARCHAR(36) PRIMARY KEY)"))

        assert adopt_complete_legacy_sqlite_schema(database_url, head_revision="0006") is False
        assert inspect(engine).has_table("alembic_version") is False
    finally:
        get_engine(database_url).dispose()
        reset_database_caches()


def test_prepare_database_creates_v2_automated_trading_tables(tmp_path) -> None:
    """Migration 0016 must create all V2 tables when upgrading to head."""
    from scripts.prepare_database import prepare_database
    from services.database import get_engine, reset_database_caches

    database_url = f"sqlite:///{(tmp_path / 'v2_schema.db').as_posix()}"
    try:
        prepare_database(database_url)
        inspector = inspect(get_engine(database_url))
        expected_tables = {
            "v2_execution_cycles",
            "v2_execution_intents",
            "v2_exchange_orders",
            "v2_managed_positions",
            "v2_protection_records",
            "v2_execution_events",
            "v2_reconciliation_snapshots",
            "v2_execution_incidents",
        }
        assert expected_tables.issubset(set(inspector.get_table_names()))
    finally:
        get_engine(database_url).dispose()
        reset_database_caches()


def test_v2_managed_positions_enforces_one_open_position_per_symbol_direction_mode(
    tmp_path,
) -> None:
    """Partial unique index must reject a second open position for the same key."""
    from sqlalchemy.exc import IntegrityError

    from scripts.prepare_database import prepare_database
    from services.database import get_engine, reset_database_caches

    database_url = f"sqlite:///{(tmp_path / 'v2_unique_position.db').as_posix()}"
    try:
        prepare_database(database_url)
        engine = get_engine(database_url)
        insert_sql = text(
            """
            INSERT INTO v2_managed_positions
                (position_id, intent_id, order_record_id, symbol, direction,
                 execution_mode, quantity, entry_price, entry_fee, state, projected_at)
            VALUES (:position_id, :intent_id, :order_record_id, 'BTC/USDT', 'long',
                    'BINANCE_TESTNET', 0.001, 65000.0, 0.65, 'POSITION_PROJECTED',
                    CURRENT_TIMESTAMP)
            """
        )
        with engine.begin() as connection:
            connection.execute(
                insert_sql,
                {
                    "position_id": "pos-a",
                    "intent_id": "intent-a",
                    "order_record_id": "order-a",
                },
            )

        with (
            pytest.raises(IntegrityError),
            engine.begin() as connection,
        ):
            connection.execute(
                insert_sql,
                {
                    "position_id": "pos-b",
                    "intent_id": "intent-b",
                    "order_record_id": "order-b",
                },
            )
    finally:
        get_engine(database_url).dispose()
        reset_database_caches()
