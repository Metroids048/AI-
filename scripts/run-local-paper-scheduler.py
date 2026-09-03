"""Run local Paper automation outside the desktop API process."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
RECOVERY_BACKOFFS = (5.0, 15.0, 30.0, 60.0, 120.0, 300.0)
FAST_RECOVERY_ATTEMPTS = 3
RECOVERY_BUDGET_EXHAUSTED_HOLD = "RECOVERY_BUDGET_EXHAUSTED_HOLD"
FATAL_BOOT_ERROR_PREFIX = "FATAL_BOOT_ERROR:"
SUPERVISOR_LEASE_TTL_SECONDS = 360.0

_EXTERNAL_FAILURE_MARKERS = (
    "BINANCE_SERVER_TIME",
    "BINANCE_REST",
    "CONNECTION",
    "PROXY",
    "DNS",
    "HTTP_429",
    "HTTP_5",
    "TIMEOUT",
    "MARKET_DATA",
    "EXCHANGE_INFO",
)
_SAFETY_HOLD_MARKERS = (
    "EXCHANGE_UNKNOWN",
    "UNRESOLVED",
    "RECONCILIATION",
    "PROJECTION",
)


def _state_path() -> Path:
    configured = os.environ.get("LOCAL_SCHEDULER_STATE_PATH")
    return Path(configured) if configured else ROOT / "logs" / "scheduler-state.json"


def configure_scheduler_environment(database_url: str, engine: str = "v2_shadow") -> None:
    """Configure the isolated scheduler with an explicit V2 engine mode."""
    if engine not in {"v2_shadow", "v2_active"}:
        raise ValueError("engine must be v2_shadow or v2_active")
    os.environ["POSTGRES_URL"] = database_url
    os.environ["APP_ENV"] = "development"
    # Keep the safe default visible in source and only opt into active mode
    # when the caller explicitly requests it.
    os.environ["AUTOMATED_TRADING_ENGINE"] = "v2_shadow"
    if engine == "v2_active":
        os.environ["AUTOMATED_TRADING_ENGINE"] = engine
    os.environ["BINANCE_USE_TESTNET"] = "true"
    os.environ["LIVE_TRADING_ENABLED"] = "false"
    if not os.environ.get("BINANCE_HTTPS_PROXY") and not os.environ.get("BINANCE_HTTP_PROXY"):
        os.environ["BINANCE_LIVE_WS_ENABLED"] = "false"


async def run_scheduler(database_url: str, engine: str = "v2_shadow") -> None:
    configure_scheduler_environment(database_url, engine)
    try:
        from services.execution.bootstrap import bootstrap_local_paper_runtime
        from services.execution.scheduler import RuntimeScheduler, initialize_standard_runtime_entry_control

        bootstrap_local_paper_runtime(seed_ohlcv=False)
        initialize_standard_runtime_entry_control()
        scheduler = RuntimeScheduler()
        scheduler.start()
        scheduler._publish_external_state()
    except Exception as exc:
        _write_recovery_overlay(
            state=_load_state(),
            reason=f"{FATAL_BOOT_ERROR_PREFIX}{type(exc).__name__}",
            state_name="FATAL_BOOT_ERROR",
            attempt=0,
            restart_count=int(os.environ.get("V2_WORKER_RESTART_COUNT", "0") or 0),
        )
        raise
    try:
        await asyncio.Event().wait()
    finally:
        await scheduler.stop()


def _sqlite_path(database_url: str) -> str | None:
    if not database_url.startswith("sqlite:///"):
        return None
    return database_url.removeprefix("sqlite:///")


def _terminate_worker(worker: subprocess.Popen[bytes]) -> None:
    if worker.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(worker.pid), "/T", "/F"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    else:
        worker.send_signal(signal.SIGTERM)
        try:
            worker.wait(timeout=5)
        except subprocess.TimeoutExpired:
            worker.kill()


def _spawn_worker(database_url: str, engine: str, restart_count: int) -> subprocess.Popen[bytes]:
    environment = os.environ.copy()
    environment["V2_WORKER_RESTART_COUNT"] = str(restart_count)
    environment["V2_SUPERVISOR_PID"] = str(os.getpid())
    return subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "--worker", "--database-url", database_url, "--engine", engine],
        cwd=ROOT,
        env=environment,
        stdout=None,
        stderr=None,
    )


def _write_recovery_overlay(
    *,
    state: dict,
    reason: str,
    state_name: str,
    attempt: int,
    restart_count: int,
    entry_hold_owned: bool = True,
) -> None:
    recovery = dict(state.get("recovery") or {})
    recovery.update(
        {
            "state": state_name,
            "reason": reason,
            "attempt": attempt,
            "entry_hold": entry_hold_owned,
            "worker_restart_count": restart_count,
            "last_recovery_at": datetime.now(UTC).isoformat(),
        }
    )
    state["recovery"] = recovery
    state["entry_enabled"] = False
    state["entry_authorized"] = False
    if entry_hold_owned:
        state["entry_control_reason"] = f"LIVENESS_RECOVERY_HOLD:{reason}"
    state["trading_state"] = "MANAGEMENT_ONLY"
    state_path = _state_path()
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, ensure_ascii=False, default=str), encoding="utf-8")


def _load_state() -> dict:
    try:
        return json.loads(_state_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _reclaim_stale_locks(database_url: str) -> None:
    sqlite_path = _sqlite_path(database_url)
    if not sqlite_path:
        return
    subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "reclaim_stale_scheduler_locks.py"), sqlite_path],
        cwd=ROOT,
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _worker_health_reason(*, worker: subprocess.Popen[bytes], started_at: float) -> str | None:
    """Return a recycle reason without cancelling anything inside the worker."""
    if worker.poll() is not None:
        return f"WORKER_EXITED:{worker.returncode}"
    if time.monotonic() - started_at <= 30:
        return None
    from services.execution.runtime_state import critical_task_liveness_errors, load_external_scheduler_state

    state = load_external_scheduler_state(
        max_age_seconds=365 * 24 * 60 * 60,
        now=datetime.now(UTC),
    )
    errors = critical_task_liveness_errors(state, now=datetime.now(UTC))
    return ";".join(errors) if errors else None


def _recovery_state_name(reason: str) -> str:
    normalized = str(reason or "").upper()
    if any(marker in normalized for marker in _SAFETY_HOLD_MARKERS):
        return "SAFETY_HOLD"
    if any(marker in normalized for marker in _EXTERNAL_FAILURE_MARKERS):
        return "DEGRADED_EXTERNAL"
    return "RECOVERING"


def _recovery_backoff_seconds(attempt: int) -> float:
    index = max(0, min(attempt - 1, len(RECOVERY_BACKOFFS) - 1))
    return RECOVERY_BACKOFFS[index]


@dataclass
class _SupervisorLease:
    coordinator: Any
    lease_name: str
    fencing_token: int


def _acquire_supervisor_lease(database_url: str, engine: str) -> _SupervisorLease | None:
    """Acquire the one-owner lease before a RuntimeScheduler worker is spawned."""
    from services.database import create_relational_schema, get_session_factory
    from services.execution.scheduler_coordination import SchedulerCoordinator, supervisor_lease_name

    # The launcher normally prepares this schema first; keeping this check here
    # makes direct supervisor invocation fail closed instead of racing schema setup.
    create_relational_schema(database_url)
    lease_name = supervisor_lease_name(database_url, engine)
    coordinator = SchedulerCoordinator(
        session_factory=get_session_factory(database_url),
        instance_id=f"supervisor:{uuid.uuid4()}",
    )
    acquired = coordinator.acquire_or_renew_lease(
        lease_name=lease_name,
        ttl_seconds=SUPERVISOR_LEASE_TTL_SECONDS,
    )
    if not acquired:
        return None
    fencing_token = coordinator.fencing_token(lease_name=lease_name)
    if fencing_token is None:
        coordinator.release_lease(lease_name=lease_name)
        raise RuntimeError("SUPERVISOR_LEASE_TOKEN_UNAVAILABLE")
    return _SupervisorLease(coordinator=coordinator, lease_name=lease_name, fencing_token=fencing_token)


def _renew_supervisor_lease(lease: _SupervisorLease) -> bool:
    """Heartbeat without allowing an expired/stale owner to reclaim the lease."""
    renewed = lease.coordinator.acquire_or_renew_lease(
        lease_name=lease.lease_name,
        ttl_seconds=SUPERVISOR_LEASE_TTL_SECONDS,
        fencing_token=lease.fencing_token,
    )
    current_token = lease.coordinator.fencing_token(lease_name=lease.lease_name)
    return bool(renewed and current_token == lease.fencing_token)


def _supervisor_monitor_interval(monitor_seconds: float) -> float:
    return max(0.1, min(float(monitor_seconds), SUPERVISOR_LEASE_TTL_SECONDS / 3))


def run_supervisor(database_url: str, engine: str = "v2_shadow", monitor_seconds: float = 5.0) -> int:
    """Own the worker process so a hung to_thread call cannot create a second writer."""
    from services.execution.scheduler import persist_liveness_recovery_hold

    lease = _acquire_supervisor_lease(database_url, engine)
    if lease is None:
        print("ALREADY_RUNNING: supervisor lease is held", file=sys.stderr)
        return 2
    monitor_interval = _supervisor_monitor_interval(monitor_seconds)
    worker: subprocess.Popen[bytes] | None = None
    started_at = time.monotonic()
    attempt = 0
    restart_count = 0
    healthy_since: float | None = None
    try:
        worker = _spawn_worker(database_url, engine, restart_count=0)
        started_at = time.monotonic()
        while True:
            time.sleep(monitor_interval)
            if worker is None:
                return 1
            try:
                lease_healthy = _renew_supervisor_lease(lease)
            except Exception as exc:  # noqa: BLE001
                _terminate_worker(worker)
                print(f"SUPERVISOR_LEASE_ERROR: {type(exc).__name__}", file=sys.stderr)
                return 1
            if not lease_healthy:
                _terminate_worker(worker)
                print("SUPERVISOR_LEASE_LOST: worker stopped", file=sys.stderr)
                return 1
            state = _load_state()
            reason = _worker_health_reason(worker=worker, started_at=started_at)
            if reason is None:
                recovery_state = str((state.get("recovery") or {}).get("state") or "")
                if worker.poll() is None and recovery_state in {"HEALTHY", "RECOVERED"}:
                    healthy_since = healthy_since or time.monotonic()
                    if time.monotonic() - healthy_since >= max(30.0, monitor_interval * 2):
                        attempt = 0
                else:
                    healthy_since = None
                continue

            healthy_since = None
            if reason.startswith(FATAL_BOOT_ERROR_PREFIX):
                _write_recovery_overlay(
                    state=state,
                    reason=reason,
                    state_name="FATAL_BOOT_ERROR",
                    attempt=0,
                    restart_count=restart_count,
                )
                _terminate_worker(worker)
                return 1

            attempt += 1
            hold_reason = str(reason)
            if attempt >= FAST_RECOVERY_ATTEMPTS:
                hold_reason = f"{RECOVERY_BUDGET_EXHAUSTED_HOLD}:{hold_reason}"
            entry_hold_owned = persist_liveness_recovery_hold(hold_reason)
            _write_recovery_overlay(
                state=state,
                reason=hold_reason,
                state_name=_recovery_state_name(hold_reason),
                attempt=attempt,
                restart_count=restart_count,
                entry_hold_owned=entry_hold_owned,
            )
            _terminate_worker(worker)
            _reclaim_stale_locks(database_url)
            time.sleep(_recovery_backoff_seconds(attempt))
            restart_count += 1
            worker = _spawn_worker(database_url, engine, restart_count=restart_count)
            started_at = time.monotonic()
    except KeyboardInterrupt:
        if worker is not None:
            _terminate_worker(worker)
        return 0
    finally:
        lease.coordinator.release_lease(lease_name=lease.lease_name, fencing_token=lease.fencing_token)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--engine", choices=("v2_shadow", "v2_active"), default="v2_shadow")
    parser.add_argument("--worker", action="store_true", help="run the RuntimeScheduler worker directly")
    parser.add_argument("--supervisor", action="store_true", help="own and recycle the RuntimeScheduler worker")
    parser.add_argument("--monitor-seconds", type=float, default=5.0)
    args = parser.parse_args()
    if args.supervisor:
        raise SystemExit(run_supervisor(args.database_url, args.engine, args.monitor_seconds))
    asyncio.run(run_scheduler(args.database_url, args.engine))


if __name__ == "__main__":
    main()
