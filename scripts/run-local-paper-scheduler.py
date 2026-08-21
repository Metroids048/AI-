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
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RECOVERY_BACKOFFS = (5.0, 15.0, 30.0)
MAX_RECOVERY_ATTEMPTS = 3


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

    from services.execution.bootstrap import bootstrap_local_paper_runtime
    from services.execution.scheduler import RuntimeScheduler

    bootstrap_local_paper_runtime(seed_ohlcv=False)
    scheduler = RuntimeScheduler()
    scheduler.start()
    scheduler._publish_external_state()
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
    return subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "--worker", "--database-url", database_url, "--engine", engine],
        cwd=ROOT,
        env=environment,
        stdout=None,
        stderr=None,
    )


def _write_recovery_overlay(*, state: dict, reason: str, state_name: str, attempt: int, restart_count: int) -> None:
    recovery = dict(state.get("recovery") or {})
    recovery.update(
        {
            "state": state_name,
            "reason": reason,
            "attempt": attempt,
            "entry_hold": True,
            "worker_restart_count": restart_count,
            "last_recovery_at": datetime.now(UTC).isoformat(),
        }
    )
    state["recovery"] = recovery
    state["entry_enabled"] = False
    state["entry_authorized"] = False
    state["entry_authority"] = "NONE"
    state["entry_authority_reason"] = f"LIVENESS_RECOVERY_HOLD:{reason}"
    state["trading_state"] = "ENTRY_PAUSED"
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


def run_supervisor(database_url: str, engine: str = "v2_shadow", monitor_seconds: float = 5.0) -> int:
    """Own the worker process so a hung to_thread call cannot create a second writer."""
    from services.execution.scheduler import persist_liveness_recovery_hold

    worker = _spawn_worker(database_url, engine, restart_count=0)
    started_at = time.monotonic()
    attempt = 0
    restart_count = 0
    healthy_since: float | None = None
    try:
        while True:
            time.sleep(max(monitor_seconds, 0.1))
            state = _load_state()
            reason = _worker_health_reason(worker=worker, started_at=started_at)
            if reason is None:
                recovery_state = str((state.get("recovery") or {}).get("state") or "")
                if worker.poll() is None and recovery_state in {"HEALTHY", "RECOVERED"}:
                    healthy_since = healthy_since or time.monotonic()
                    if time.monotonic() - healthy_since >= max(30.0, monitor_seconds * 2):
                        attempt = 0
                else:
                    healthy_since = None
                continue

            healthy_since = None
            attempt += 1
            hold_reason = f"{reason}"
            persist_liveness_recovery_hold(hold_reason)
            _write_recovery_overlay(
                state=state,
                reason=hold_reason,
                state_name="ENTRY_HOLD" if attempt < MAX_RECOVERY_ATTEMPTS else "AUTO_RECOVERY_EXHAUSTED",
                attempt=attempt,
                restart_count=restart_count,
            )
            _terminate_worker(worker)
            _reclaim_stale_locks(database_url)
            if attempt >= MAX_RECOVERY_ATTEMPTS:
                return 2
            time.sleep(RECOVERY_BACKOFFS[min(attempt - 1, len(RECOVERY_BACKOFFS) - 1)])
            restart_count += 1
            worker = _spawn_worker(database_url, engine, restart_count=restart_count)
            started_at = time.monotonic()
    except KeyboardInterrupt:
        _terminate_worker(worker)
        return 0


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
