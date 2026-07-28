"""Alembic environment. Reads POSTGRES_URL from the environment and targets the
Strategy Library metadata. Only relational tables are managed here — time-series
and event tables are owned by infra/timescale/init.sql."""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Imported for its side effect of registering V2 tables on Base.metadata.
import services.automated_trading.infrastructure.models  # noqa: F401,E402
from services.strategy_library.models import Base

config = context.config

if config.config_file_name is not None:
    # disable_existing_loggers=False: fileConfig's default silently disables any
    # logger not listed in alembic.ini's [loggers] section. In-process callers
    # like scripts/prepare_database.py run this after application logging is
    # already configured, so the default would kill unrelated app loggers
    # (e.g. services.execution.paper_signal) for the rest of the process.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# Resolve DB URL at runtime (env wins over alembic.ini).
db_url = os.getenv("POSTGRES_URL")
if db_url:
    config.set_main_option("sqlalchemy.url", db_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
