from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest

from tests.runtime_safety import RuntimeSafetyGuard, UnsafeTestResourceError


@pytest.mark.parametrize("uri", [False, True])
def test_real_database_connection_is_denied_before_open(tmp_path, uri):
    # This nonexistent outside path must never be created, even through a file URI.
    outside = tmp_path.parent.parent.parent / "pytest-forbidden-sentinel.db"
    target = outside.as_uri() + "?mode=rwc" if uri else str(outside)
    with pytest.raises(UnsafeTestResourceError, match="PYTEST_BLOCKED_NON_TEST_DATABASE"):
        sqlite3.connect(target, uri=uri)
    assert not outside.exists()


def test_environment_leak_and_cache_reset_cannot_open_runtime_database(monkeypatch):
    from services.database import get_engine, reset_database_caches

    root = Path(__file__).resolve().parents[1]
    monkeypatch.setenv("POSTGRES_URL", f"sqlite:///{(root / '.local_paper_console.db').as_posix()}")
    reset_database_caches()
    with pytest.raises(UnsafeTestResourceError, match="PYTEST_BLOCKED_NON_TEST_DATABASE"):
        get_engine().connect()


def test_attached_database_cannot_bypass_connection_guard(tmp_path):
    outside = tmp_path.parent.parent.parent / f"pytest-forbidden-attach-{os.getpid()}.db"
    with sqlite3.connect(":memory:") as connection, pytest.raises(sqlite3.DatabaseError, match="not authorized"):
        connection.execute("ATTACH DATABASE ? AS runtime", (str(outside),))
    assert not outside.exists()


def test_guard_resolves_traversal_and_encoded_uri(tmp_path):
    owned = tmp_path / "owned"
    owned.mkdir()
    guard = RuntimeSafetyGuard([owned], [])
    for value in (owned / ".." / "outside.db", owned.as_uri() + "/%2e%2e/outside.db"):
        with pytest.raises(UnsafeTestResourceError):
            guard.check_database(value)


def test_guard_allows_real_temporary_database_and_memory(tmp_path):
    with sqlite3.connect(tmp_path / "allowed.db") as connection:
        connection.execute("CREATE TABLE proof (value TEXT)")
        connection.execute("INSERT INTO proof VALUES ('temporary')")
        assert connection.execute("SELECT value FROM proof").fetchone() == ("temporary",)
    with sqlite3.connect("file:test-memory?mode=memory&cache=shared", uri=True) as connection:
        assert connection.execute("PRAGMA database_list").fetchone()[2] == ""


def test_runtime_state_fallback_is_denied_before_temporary_write(monkeypatch):
    from services.execution.runtime_state import write_external_scheduler_state

    monkeypatch.delenv("LOCAL_SCHEDULER_STATE_PATH", raising=False)
    with pytest.raises(UnsafeTestResourceError, match="PYTEST_BLOCKED_RUNTIME_FILE"):
        write_external_scheduler_state({"running": False})


def test_state_writes_use_per_test_resource():
    from services.execution.runtime_state import write_external_scheduler_state

    target = Path(os.environ["LOCAL_SCHEDULER_STATE_PATH"])
    write_external_scheduler_state({"running": False, "source": "test"})
    assert json.loads(target.read_text()) == {"running": False, "source": "test"}


def test_atomic_replace_cannot_overwrite_protected_sentinel(tmp_path):
    protected = tmp_path / "runtime.json"
    protected.write_text("keep me")
    guard = RuntimeSafetyGuard([tmp_path], [protected])
    sys.addaudithook(lambda event, args: guard.audit(event, args) if event == "os.rename" else None)
    replacement = tmp_path / "replacement.json"
    replacement.write_text("overwrite")
    with pytest.raises(UnsafeTestResourceError, match="PYTEST_BLOCKED_RUNTIME_FILE"):
        replacement.replace(protected)
    # Clear this synthetic protection to let pytest remove its own temp directory.
    guard.protected_files.clear()
    assert protected.read_text() == "keep me"
