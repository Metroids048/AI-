from __future__ import annotations

from scripts.reclaim_stale_scheduler_locks import _alive


def test_alive_treats_windows_invalid_pid_system_error_as_dead(monkeypatch) -> None:
    def _raise_system_error(_pid: int, _signal: int) -> None:
        raise SystemError(87, "invalid parameter")

    monkeypatch.setattr("scripts.reclaim_stale_scheduler_locks.os.kill", _raise_system_error)
    assert _alive(999999) is False
