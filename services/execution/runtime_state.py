"""Shared health state for the desktop API and its external scheduler."""

from __future__ import annotations

import json
import os
from argparse import ArgumentParser
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

V2_CYCLE_WATCHDOG_SECONDS = 25 * 60


def _state_path() -> Path:
    configured = os.getenv("LOCAL_SCHEDULER_STATE_PATH")
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[2] / "logs" / "scheduler-state.json"


@dataclass(frozen=True)
class ExternalSchedulerState:
    running: bool = False
    supervisor_alive: bool = False
    worker_alive: bool = False
    critical_cycle_alive: bool = False
    heartbeat_at: datetime | None = None
    heartbeat_age_seconds: float | None = None
    top20_coverage_count: int = 0
    execution_coverage_count: int = 0
    execution_symbols: tuple[str, ...] = ()
    exchange_info_ready: bool = False
    data_fresh: bool = False
    last_auto_cycle_at: datetime | None = None
    last_v2_cycle_at: datetime | None = None
    last_v2_cycle_age_seconds: float | None = None
    scheduler_error: str | None = None
    last_runtime_error: str | None = None
    market_data_fresh: bool = False
    recovery_state: str | None = None
    engine_health_status: str = "OFFLINE"
    reason: str | None = "scheduler_state_missing"
    task_run_counts: dict[str, int] = field(default_factory=dict)
    task_failure_counts: dict[str, int] = field(default_factory=dict)
    task_last_results: dict[str, Any] = field(default_factory=dict)
    engine_activation: str | None = None
    execution_mode: str | None = None
    execution_strategy_id: str | None = None
    registered_jobs: tuple[str, ...] = ()
    legacy_writer_enabled: bool | None = None
    entry_enabled: bool | None = None
    sampling_fallback_enabled: bool | None = None
    external_baseline_captured: bool | None = None
    external_baseline_value: dict[str, str] | None = None
    external_baseline_source: str | None = None
    external_baseline_lifecycle: str | None = None
    external_baseline_drift_keys: tuple[str, ...] = ()
    entry_authorized: bool | None = None
    entry_authority: str | None = None
    entry_authority_reason: str | None = None
    production_authorization_state: str | None = None
    active_entry_strategy: str | None = None
    promotion_eligible: bool | None = None
    trading_state: str | None = None
    startup_contract_errors: tuple[str, ...] = ()
    scheduler_started_at: datetime | None = None
    critical_jobs: dict[str, dict[str, Any]] = field(default_factory=dict)
    recovery: dict[str, Any] = field(default_factory=dict)


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _process_alive(value: object, *, fallback: bool) -> bool:
    if not isinstance(value, int) or value <= 0:
        return fallback
    if os.name == "nt":
        return _windows_process_alive(value)
    try:
        os.kill(value, 0)
    except (OSError, OverflowError, SystemError, ValueError):
        return False
    return True


def _windows_process_alive(pid: int) -> bool:
    """Read a Windows process handle without sending a terminating signal.

    ``os.kill(pid, 0)`` is not a liveness probe on Windows: CPython maps the
    non-console signal to ``TerminateProcess``.  Runtime Truth must never
    mutate the Supervisor or worker while it is only checking their state.
    """
    import ctypes

    process_query_limited_information = 0x1000
    still_active = 259
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        return False
    try:
        exit_code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            # An inaccessible exit code is not proof of death after a process
            # handle was opened successfully; retain fail-closed continuity.
            return True
        return exit_code.value == still_active
    finally:
        kernel32.CloseHandle(handle)


def derive_engine_health_status(
    *,
    running: bool,
    supervisor_alive: bool,
    worker_alive: bool,
    critical_cycle_alive: bool,
    market_data_fresh: bool,
    recovery_state: str | None,
    last_runtime_error: str | None,
) -> str:
    """Classify engine liveness without changing the runtime's safety decisions."""
    normalized_recovery = (recovery_state or "").upper()
    normalized_error = (last_runtime_error or "").upper()
    if normalized_recovery == "FATAL_BOOT_ERROR" or "FATAL_BOOT_ERROR" in normalized_error:
        return "FATAL"
    if not running or not supervisor_alive or not worker_alive or not critical_cycle_alive:
        return "OFFLINE"
    if normalized_recovery in {"CRASH_DETECTED", "RESTARTING", "VERIFYING"}:
        return "RECOVERING"
    if normalized_recovery in {"ENTRY_HOLD", "MANAGEMENT", "AUTO_RECOVERY_EXHAUSTED"}:
        return "SAFETY_HOLD"
    if "EXCHANGE_UNKNOWN" in normalized_error or "RECONCILIATION" in normalized_error:
        return "SAFETY_HOLD"
    if not market_data_fresh or normalized_error:
        return "DEGRADED_EXTERNAL"
    return "HEALTHY"


def write_external_scheduler_state(payload: dict[str, Any]) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=True, default=str), encoding="utf-8")
    temporary.replace(path)


def load_external_scheduler_state(
    *,
    max_age_seconds: int = 120,
    now: datetime | None = None,
) -> ExternalSchedulerState:
    try:
        raw = json.loads(_state_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ExternalSchedulerState()
    if not isinstance(raw, dict):
        return ExternalSchedulerState(reason="scheduler_state_invalid")

    heartbeat_at = _parse_datetime(raw.get("heartbeat_at"))
    reference = now or datetime.now(UTC)
    if heartbeat_at is None:
        return ExternalSchedulerState(reason="scheduler_heartbeat_missing")
    if (reference - heartbeat_at).total_seconds() > max_age_seconds:
        return ExternalSchedulerState(
            heartbeat_at=heartbeat_at,
            reason="scheduler_heartbeat_stale",
        )

    execution_coverage_count = int(raw.get("execution_coverage_count", raw.get("top20_coverage_count", 0)) or 0)
    execution_symbols = tuple(str(value) for value in raw.get("execution_symbols", []) if value)
    registered_jobs = tuple(str(value) for value in raw.get("registered_jobs", []) if value)
    startup_contract_errors = tuple(str(value) for value in raw.get("startup_contract_errors", []) if value)
    running_value = raw.get("running")
    running = running_value if isinstance(running_value, bool) else False
    critical_jobs = {
        str(name): dict(value)
        for name, value in (raw.get("critical_jobs") or {}).items()
        if isinstance(name, str) and isinstance(value, dict)
    }
    critical_job = critical_jobs.get("automated_trading_v2_cycle", {})
    last_v2_cycle_at = _parse_datetime(critical_job.get("last_completed_at")) or _parse_datetime(
        raw.get("last_auto_cycle_at")
    )
    heartbeat_age_seconds = max(0.0, (reference - heartbeat_at).total_seconds())
    last_v2_cycle_age_seconds = (
        max(0.0, (reference - last_v2_cycle_at).total_seconds()) if last_v2_cycle_at is not None else None
    )
    recovery = dict(raw.get("recovery") or {}) if isinstance(raw.get("recovery"), dict) else {}
    recovery_state = recovery.get("state") if isinstance(recovery.get("state"), str) else None
    recovery_reason = recovery.get("reason") if isinstance(recovery.get("reason"), str) else None
    supervisor_alive = _process_alive(raw.get("supervisor_pid"), fallback=running)
    worker_alive = _process_alive(raw.get("worker_pid"), fallback=running)
    critical_cycle_alive = critical_job.get("task_alive") is True
    scheduler_error = raw.get("scheduler_error") if isinstance(raw.get("scheduler_error"), str) else None
    last_runtime_error = recovery_reason or scheduler_error
    return ExternalSchedulerState(
        running=running,
        supervisor_alive=supervisor_alive,
        worker_alive=worker_alive,
        critical_cycle_alive=critical_cycle_alive,
        heartbeat_at=heartbeat_at,
        heartbeat_age_seconds=heartbeat_age_seconds,
        top20_coverage_count=int(raw.get("top20_coverage_count", 0)),
        execution_coverage_count=execution_coverage_count,
        execution_symbols=execution_symbols,
        exchange_info_ready=bool(raw.get("exchange_info_ready")),
        data_fresh=bool(raw.get("data_fresh")),
        market_data_fresh=bool(raw.get("data_fresh")),
        last_auto_cycle_at=_parse_datetime(raw.get("last_auto_cycle_at")),
        last_v2_cycle_at=last_v2_cycle_at,
        last_v2_cycle_age_seconds=last_v2_cycle_age_seconds,
        scheduler_error=scheduler_error,
        last_runtime_error=last_runtime_error,
        reason=raw.get("reason") if isinstance(raw.get("reason"), str) else None,
        task_run_counts=dict(raw.get("task_run_counts") or {}),
        task_failure_counts=dict(raw.get("task_failure_counts") or {}),
        task_last_results=dict(raw.get("task_last_results") or {}),
        engine_activation=raw.get("engine_activation") if isinstance(raw.get("engine_activation"), str) else None,
        execution_mode=raw.get("execution_mode") if isinstance(raw.get("execution_mode"), str) else None,
        execution_strategy_id=(
            raw.get("execution_strategy_id") if isinstance(raw.get("execution_strategy_id"), str) else None
        ),
        registered_jobs=registered_jobs,
        legacy_writer_enabled=(
            raw.get("legacy_writer_enabled") if isinstance(raw.get("legacy_writer_enabled"), bool) else None
        ),
        entry_enabled=raw.get("entry_enabled") if isinstance(raw.get("entry_enabled"), bool) else None,
        sampling_fallback_enabled=(
            raw.get("sampling_fallback_enabled") if isinstance(raw.get("sampling_fallback_enabled"), bool) else None
        ),
        external_baseline_captured=(
            raw.get("external_baseline_captured") if isinstance(raw.get("external_baseline_captured"), bool) else None
        ),
        external_baseline_value=(
            {str(key): str(value) for key, value in raw.get("external_baseline_value", {}).items()}
            if isinstance(raw.get("external_baseline_value"), dict)
            else None
        ),
        external_baseline_source=(
            raw.get("external_baseline_source") if isinstance(raw.get("external_baseline_source"), str) else None
        ),
        external_baseline_lifecycle=(
            raw.get("external_baseline_lifecycle") if isinstance(raw.get("external_baseline_lifecycle"), str) else None
        ),
        external_baseline_drift_keys=tuple(
            str(value) for value in raw.get("external_baseline_drift_keys", []) if isinstance(value, str)
        ),
        entry_authorized=raw.get("entry_authorized") if isinstance(raw.get("entry_authorized"), bool) else None,
        entry_authority=raw.get("entry_authority") if isinstance(raw.get("entry_authority"), str) else None,
        entry_authority_reason=(
            raw.get("entry_authority_reason") if isinstance(raw.get("entry_authority_reason"), str) else None
        ),
        production_authorization_state=(
            raw.get("production_authorization_state")
            if isinstance(raw.get("production_authorization_state"), str)
            else None
        ),
        active_entry_strategy=(
            raw.get("active_entry_strategy") if isinstance(raw.get("active_entry_strategy"), str) else None
        ),
        promotion_eligible=(raw.get("promotion_eligible") if isinstance(raw.get("promotion_eligible"), bool) else None),
        trading_state=raw.get("trading_state") if isinstance(raw.get("trading_state"), str) else None,
        startup_contract_errors=startup_contract_errors,
        scheduler_started_at=_parse_datetime(raw.get("started_at")),
        critical_jobs=critical_jobs,
        recovery=recovery,
        recovery_state=recovery_state,
        engine_health_status=derive_engine_health_status(
            running=running,
            supervisor_alive=supervisor_alive,
            worker_alive=worker_alive,
            critical_cycle_alive=critical_cycle_alive,
            market_data_fresh=bool(raw.get("data_fresh")),
            recovery_state=recovery_state,
            last_runtime_error=last_runtime_error,
        ),
    )


def critical_task_liveness_errors(
    state: ExternalSchedulerState,
    *,
    now: datetime | None = None,
    watchdog_seconds: int = V2_CYCLE_WATCHDOG_SECONDS,
) -> tuple[str, ...]:
    """Return fail-closed liveness errors for the authoritative V2 cycle."""
    # Older state files remain readable for diagnostics.  The live scheduler
    # always publishes this map; launcher health rejects a missing map before
    # accepting STARTUP_READY.
    if not state.critical_jobs:
        return ()
    recovery_state = str(state.recovery.get("state") or "")
    if recovery_state == "AUTO_RECOVERY_EXHAUSTED":
        return ("V2_AUTO_RECOVERY_EXHAUSTED",)
    job = state.critical_jobs.get("automated_trading_v2_cycle")
    if not isinstance(job, dict) or job.get("registered") is not True:
        return ("V2_CRITICAL_TASK_NOT_REGISTERED",)
    errors: list[str] = []
    if job.get("task_alive") is not True:
        errors.append("V2_CRITICAL_TASK_NOT_ALIVE")
    if job.get("currently_running") is True and job.get("last_exception"):
        errors.append("V2_CRITICAL_TASK_FAILURE_UNRECOVERED")
    if job.get("last_exception") and int(job.get("consecutive_failures") or 0) > 0:
        errors.append("V2_CRITICAL_TASK_FAILURE_UNRECOVERED")
    completed = _parse_datetime(job.get("last_completed_at"))
    reference = now or datetime.now(UTC)
    started = state.scheduler_started_at or state.heartbeat_at
    if completed is None:
        if started is not None and (reference - started).total_seconds() > watchdog_seconds:
            errors.append("V2_DECISION_STREAM_STALLED")
    elif (reference - completed).total_seconds() > watchdog_seconds:
        errors.append("V2_DECISION_STREAM_STALLED")
    return tuple(dict.fromkeys(errors))


def active_startup_contract_errors(
    state: ExternalSchedulerState,
    *,
    requested_engine: str = "v2_active",
) -> tuple[str, ...]:
    """Return fail-closed errors for the one-click ACTIVE Testnet contract."""
    errors: list[str] = []
    if requested_engine != "v2_active":
        errors.append("REQUESTED_ENGINE_NOT_ACTIVE")
    if not state.running:
        errors.append("SCHEDULER_NOT_RUNNING")
    if state.engine_activation != "ACTIVE":
        errors.append("ENGINE_ACTIVATION_MISMATCH")
    if state.execution_mode != "BINANCE_TESTNET":
        errors.append("EXECUTION_MODE_MISMATCH")
    if state.execution_strategy_id in (None, "") or (
        state.execution_strategy_id == "testnet_sampling_v2" and state.entry_authority != "TESTNET_CANARY"
    ):
        errors.append("EXECUTION_STRATEGY_UNAUTHORIZED")
    from services.data.universe import AUTO_SIMULATION_EXECUTION_SYMBOLS

    expected_symbols = tuple(sorted(AUTO_SIMULATION_EXECUTION_SYMBOLS))
    if tuple(sorted(state.execution_symbols)) != expected_symbols:
        errors.append("EXECUTION_SCOPE_MISMATCH")
    if state.execution_coverage_count != len(expected_symbols):
        errors.append("EXECUTION_SCOPE_INCOMPLETE")
    if "automated_trading_v2_cycle" not in state.registered_jobs:
        errors.append("V2_CYCLE_NOT_REGISTERED")
    errors.extend(error for error in critical_task_liveness_errors(state) if error not in errors)
    if any(job in state.registered_jobs for job in ("paper_runtime_cycle", "paper_observation_cycle")):
        errors.append("LEGACY_JOB_REGISTERED")
    if state.legacy_writer_enabled is not False:
        errors.append("LEGACY_WRITER_ENABLED")
    if state.external_baseline_captured is not True:
        errors.append("EXTERNAL_BASELINE_NOT_CAPTURED")
    strategy_not_ready_pause = (
        state.production_authorization_state != "APPROVED"
        and state.entry_authority == "NONE"
        and state.entry_authorized is False
        and state.trading_state == "ENTRY_PAUSED"
    )
    if not strategy_not_ready_pause and state.entry_enabled is not True:
        errors.append("ENTRY_DISABLED")
    if not strategy_not_ready_pause and state.entry_authorized is not True:
        errors.append("ENTRY_NOT_AUTHORIZED")
    if not strategy_not_ready_pause and state.entry_authority not in {
        "TESTNET_CANARY",
        "TESTNET_FORWARD",
        "PRODUCTION",
    }:
        errors.append("ENTRY_AUTHORITY_INVALID")
    if state.entry_authority == "TESTNET_CANARY" and state.sampling_fallback_enabled is not True:
        errors.append("CANARY_SAMPLING_FALLBACK_DISABLED")
    if state.entry_authority == "PRODUCTION" and state.production_authorization_state != "APPROVED":
        errors.append("PRODUCTION_AUTHORIZATION_INVALID")
    if not strategy_not_ready_pause and state.trading_state != "TRADING":
        errors.append("TRADING_STATE_NOT_TRADING")
    errors.extend(error for error in state.startup_contract_errors if error not in errors)
    return tuple(errors)


def main() -> int:
    parser = ArgumentParser(description="Validate the actual ACTIVE Testnet scheduler state.")
    parser.add_argument("--state-path", default=None)
    parser.add_argument("--requested-engine", default="v2_active")
    parser.add_argument("--require-active-contract", action="store_true")
    args = parser.parse_args()
    if args.state_path:
        os.environ["LOCAL_SCHEDULER_STATE_PATH"] = args.state_path
    state = load_external_scheduler_state()
    if not args.require_active_contract:
        print(json.dumps(state.__dict__, default=str, ensure_ascii=True))
        return 0
    errors = active_startup_contract_errors(state, requested_engine=args.requested_engine)
    if errors:
        print(f"ACTIVE_STARTUP_CONTRACT_FAILED: {';'.join(errors)}")
        return 1
    print("ACTIVE_STARTUP_CONTRACT_PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
