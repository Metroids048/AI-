"""Shared SQLAlchemy engine/session helpers for service repositories."""

from __future__ import annotations

import os
from collections.abc import Generator
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from shared.config import settings
from services.strategy_library.models import Base


def resolve_database_url() -> str:
    return os.getenv("POSTGRES_URL", settings.postgres_url)


def _engine_kwargs(url: str) -> dict:
    if url.startswith("sqlite"):
        return {"connect_args": {"check_same_thread": False}}
    # Production-grade connection pool (previously unconfigured → PG default
    # pool_size=5 caused connection exhaustion under concurrent load).
    return {
        "pool_size": settings.db_pool_size,
        "pool_recycle": settings.db_pool_recycle_seconds,
        "pool_pre_ping": settings.db_pool_pre_ping,
    }


@lru_cache(maxsize=4)
def get_engine(url: str | None = None) -> Engine:
    db_url = url or resolve_database_url()
    return create_engine(db_url, future=True, **_engine_kwargs(db_url))


@lru_cache(maxsize=4)
def get_session_factory(url: str | None = None) -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(url), expire_on_commit=False, future=True)


def get_db_session() -> Generator[Session, None, None]:
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()


def create_relational_schema(url: str | None = None) -> None:
    Base.metadata.create_all(get_engine(url))


def reset_database_caches() -> None:
    get_engine.cache_clear()
    get_session_factory.cache_clear()
