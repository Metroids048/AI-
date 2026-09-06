"""Fail closed before pytest can open runtime databases or state files.

This is an in-process accident guard, not an operating-system sandbox. SQLite
connections (including direct sqlite3 calls) must live in pytest-owned directories.
"""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit


class UnsafeTestResourceError(RuntimeError):
    """A test attempted to access resources outside its disposable workspace."""


def sqlite_database_path(database: str | bytes | os.PathLike[str]) -> Path | None:
    value = os.fsdecode(database)
    if value in ("", ":memory:"):
        return None
    if value.startswith("file:"):
        uri = urlsplit(value)
        # Named memory databases are allowed; every file URI is canonicalized.
        modes = parse_qs(uri.query).get("mode", [])
        if (uri.path == ":memory:" and not modes) or modes == ["memory"]:
            return None
        value = unquote(uri.path)
        if uri.netloc and uri.netloc != "localhost":
            value = f"//{uri.netloc}{value}"
        elif os.name == "nt" and len(value) > 2 and value[0] == "/" and value[2] == ":":
            value = value[1:]
    return Path(value).resolve()


class RuntimeSafetyGuard:
    def __init__(self, allowed_roots: list[Path], protected_files: list[Path]) -> None:
        self.allowed_roots = [path.resolve() for path in allowed_roots]
        self.protected_files = [path.expanduser().resolve() for path in protected_files]
        self.readonly_databases: set[Path] = set()

    def allow_root(self, path: Path) -> None:
        self.allowed_roots.append(path.resolve())

    def check_database(self, database: str | bytes | os.PathLike[str]) -> None:
        path = sqlite_database_path(database)
        value = os.fsdecode(database)
        if (
            path in self.readonly_databases
            and value.startswith("file:")
            and parse_qs(urlsplit(value).query).get("mode") == ["ro"]
        ):
            return
        if path is not None and not any(path.is_relative_to(root) for root in self.allowed_roots):
            raise UnsafeTestResourceError(f"PYTEST_BLOCKED_NON_TEST_DATABASE: {path}")

    def check_file(self, value: object) -> None:
        if not isinstance(value, (str, bytes, os.PathLike)):
            return
        path = Path(os.fsdecode(value)).resolve()
        for protected in self.protected_files:
            # Include journal, WAL, lock and atomic replacement sidecars.
            if path.parent == protected.parent and (
                path == protected.with_suffix(".tmp")
                or path.name == protected.name
                or path.name.startswith(protected.name + ".")
                or path.name.startswith(protected.name + "-")
            ):
                raise UnsafeTestResourceError(f"PYTEST_BLOCKED_RUNTIME_FILE: {path}")

    def audit(self, event: str, args: tuple) -> None:
        if event == "sqlite3.connect":
            self.check_database(args[0])
        elif event in {"open", "os.remove", "os.rmdir", "os.truncate"}:
            self.check_file(args[0])
        elif event in {"os.rename", "os.link", "os.symlink"}:
            self.check_file(args[0])
            self.check_file(args[1])

    def authorize_sql(self, action: int, arg1: str | None, *_args: object) -> int:
        if action == sqlite3.SQLITE_ATTACH:
            # SQLite reports no filename for parameterized ATTACH at prepare time.
            # Its destination cannot be verified, so refuse it before execution.
            if not arg1:
                return sqlite3.SQLITE_DENY
            try:
                self.check_database(arg1)
            except UnsafeTestResourceError:
                return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    def install(self) -> None:
        sys.addaudithook(self.audit)

    def guard_sqlite_attachments(self) -> None:
        connect = sqlite3.connect

        def guarded_connect(*args, **kwargs):
            connection = connect(*args, **kwargs)
            connection.set_authorizer(self.authorize_sql)
            return connection

        # SQLAlchemy uses dbapi2; direct sqlite3 callers need the same guard.
        sqlite3.connect = guarded_connect
        sqlite3.dbapi2.connect = guarded_connect
