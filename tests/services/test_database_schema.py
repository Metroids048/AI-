from __future__ import annotations

from sqlalchemy import inspect


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
