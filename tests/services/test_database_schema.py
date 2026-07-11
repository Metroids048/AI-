from __future__ import annotations

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
