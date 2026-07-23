"""Prepare a relational database and the local SQLite runtime tables."""

from __future__ import annotations

import argparse
import os

from alembic import command
from alembic.config import Config


def prepare_database(database_url: str, *, head_revision: str = "0012") -> None:
    os.environ["POSTGRES_URL"] = database_url
    from services.database import (
        adopt_complete_legacy_sqlite_schema,
        create_local_runtime_schema,
        reset_database_caches,
    )

    reset_database_caches()
    if database_url.startswith("sqlite"):
        adopt_complete_legacy_sqlite_schema(database_url, head_revision=head_revision)
    command.upgrade(Config("alembic.ini"), "head")
    if database_url.startswith("sqlite"):
        create_local_runtime_schema(database_url)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--head-revision", default="0012")
    args = parser.parse_args()
    prepare_database(args.database_url, head_revision=args.head_revision)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
