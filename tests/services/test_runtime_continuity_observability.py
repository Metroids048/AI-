from __future__ import annotations

import os
from datetime import UTC, datetime

from services.execution.runtime_state import (
    derive_engine_health_status,
    load_external_scheduler_state,
    write_external_scheduler_state,
)


def test_runtime_state_exposes_distinct_supervisor_worker_and_cycle_liveness(monkeypatch, tmp_path) -> None:
    now = datetime.now(UTC)
    monkeypatch.setenv("LOCAL_SCHEDULER_STATE_PATH", str(tmp_path / "scheduler-state.json"))
    write_external_scheduler_state(
        {
            "running": True,
            "heartbeat_at": now.isoformat(),
            "last_auto_cycle_at": now.isoformat(),
            "scheduler_error": None,
            "supervisor_pid": os.getpid(),
            "worker_pid": os.getpid(),
            "data_fresh": True,
            "critical_jobs": {"automated_trading_v2_cycle": {"registered": True, "task_alive": True}},
            "recovery": {"state": "HEALTHY"},
        }
    )

    state = load_external_scheduler_state(now=now)

    assert state.supervisor_alive is True
    assert state.worker_alive is True
    assert state.critical_cycle_alive is True
    assert state.heartbeat_age_seconds == 0.0
    assert state.last_v2_cycle_age_seconds == 0.0
    assert state.market_data_fresh is True
    assert state.last_runtime_error is None
    assert state.recovery_state == "HEALTHY"
    assert state.engine_health_status == "HEALTHY"


def test_engine_health_status_distinguishes_runtime_liveness_from_entry_safety() -> None:
    def status(**overrides: bool | str | None) -> str:
        values: dict[str, bool | str | None] = {
            "running": True,
            "supervisor_alive": True,
            "worker_alive": True,
            "critical_cycle_alive": True,
            "market_data_fresh": True,
            "recovery_state": "HEALTHY",
            "last_runtime_error": None,
        }
        values.update(overrides)
        return derive_engine_health_status(
            running=bool(values["running"]),
            supervisor_alive=bool(values["supervisor_alive"]),
            worker_alive=bool(values["worker_alive"]),
            critical_cycle_alive=bool(values["critical_cycle_alive"]),
            market_data_fresh=bool(values["market_data_fresh"]),
            recovery_state=values["recovery_state"] if isinstance(values["recovery_state"], str) else None,
            last_runtime_error=(
                values["last_runtime_error"] if isinstance(values["last_runtime_error"], str) else None
            ),
        )

    assert status() == "HEALTHY"
    assert status(market_data_fresh=False) == "DEGRADED_EXTERNAL"
    assert status(recovery_state="ENTRY_HOLD") == "SAFETY_HOLD"
    assert status(recovery_state="RESTARTING") == "RECOVERING"
    assert status(worker_alive=False) == "OFFLINE"
    assert status(recovery_state="FATAL_BOOT_ERROR") == "FATAL"


def test_runtime_state_treats_windows_pid_probe_system_error_as_dead(monkeypatch, tmp_path) -> None:
    now = datetime.now(UTC)
    monkeypatch.setenv("LOCAL_SCHEDULER_STATE_PATH", str(tmp_path / "scheduler-state.json"))
    write_external_scheduler_state(
        {
            "running": True,
            "heartbeat_at": now.isoformat(),
            "supervisor_pid": 1234,
            "worker_pid": 5678,
            "critical_jobs": {},
        }
    )

    def raise_windows_probe_error(_pid: int, _sig: int) -> None:
        raise SystemError(87, "invalid parameter")

    monkeypatch.setattr(os, "kill", raise_windows_probe_error)
    state = load_external_scheduler_state(now=now)

    assert state.running is True
    assert state.supervisor_alive is False
    assert state.worker_alive is False
