"""Fail-closed validation for the Linux V2 ACTIVE deployment environment."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path


class PreflightError(ValueError):
    """Raised when the cloud process must not be started."""


@dataclass(frozen=True)
class PreflightResult:
    engine: str
    execution_mode: str
    database_path: Path
    scheduler_state_path: Path
    memory: dict[str, int]
    resource_gate: str


def _required(environment: Mapping[str, str], key: str) -> str:
    value = environment.get(key, "").strip()
    if not value:
        raise PreflightError(f"{key} is required")
    return value


def _require_exact(environment: Mapping[str, str], key: str, expected: str) -> None:
    actual = environment.get(key, "").strip().lower()
    if actual != expected:
        raise PreflightError(f"{key} must be {expected}")


def _sqlite_database_path(database_url: str) -> Path:
    prefix = "sqlite:///"
    if not database_url.startswith(prefix):
        raise PreflightError("POSTGRES_URL must be a SQLite URL")
    path = Path(database_url[len(prefix) :])
    if not path.is_absolute():
        raise PreflightError("POSTGRES_URL must use an absolute persistent SQLite path")
    return path


def _require_writable_parent(path: Path, label: str) -> None:
    parent = path.parent
    if not parent.is_dir() or not os.access(parent, os.W_OK | os.X_OK):
        raise PreflightError(f"{label} parent is not writable: {parent}")


def _validate_external_baseline(environment: Mapping[str, str]) -> None:
    _require_exact(environment, "V2_ALLOW_UNMANAGED_EXTERNAL_POSITIONS", "true")
    raw_baseline = _required(environment, "V2_EXTERNAL_BASELINE_JSON")
    try:
        parsed_baseline = json.loads(raw_baseline)
    except json.JSONDecodeError as error:
        raise PreflightError("V2_EXTERNAL_BASELINE_JSON must be JSON") from error
    if not isinstance(parsed_baseline, dict):
        raise PreflightError("V2_EXTERNAL_BASELINE_JSON must be a JSON object")

    baseline_path = Path(_required(environment, "V2_EXTERNAL_BASELINE_PATH"))
    if not baseline_path.is_absolute() or not baseline_path.is_file():
        raise PreflightError("V2 external baseline file is missing")
    expected_source = f"persistent_file:{baseline_path.resolve()}"
    if _required(environment, "V2_EXTERNAL_BASELINE_SOURCE") != expected_source:
        raise PreflightError("V2_EXTERNAL_BASELINE_SOURCE must reference the persistent baseline file")

    try:
        persisted = json.loads(baseline_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise PreflightError("V2 external baseline file is invalid") from error
    if not isinstance(persisted, dict) or persisted.get("execution_mode") != "BINANCE_TESTNET":
        raise PreflightError("V2 external baseline file must be Binance Testnet")
    if persisted.get("positions") != parsed_baseline:
        raise PreflightError("V2 external baseline does not match its persisted file")


def _default_pid_is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def read_memory_snapshot() -> dict[str, int]:
    """Return host memory from procfs when the deployment host exposes it."""
    meminfo = Path("/proc/meminfo")
    if not meminfo.is_file():
        return {}

    values: dict[str, int] = {}
    for line in meminfo.read_text(encoding="utf-8").splitlines():
        key, separator, remainder = line.partition(":")
        if not separator:
            continue
        amount = remainder.strip().split(maxsplit=1)
        if amount and amount[0].isdigit():
            values[key] = int(amount[0]) // 1024
    result: dict[str, int] = {}
    if "MemTotal" in values:
        result["total_mb"] = values["MemTotal"]
    if "MemAvailable" in values:
        result["available_mb"] = values["MemAvailable"]
    return result


def validate_environment(
    environment: Mapping[str, str],
    *,
    scheduler_pid_path: Path | None = None,
    pid_is_alive: Callable[[int], bool] = _default_pid_is_alive,
    memory_snapshot: Callable[[], dict[str, int]] = read_memory_snapshot,
) -> PreflightResult:
    """Validate the deployment contract without contacting Binance or mutating state."""
    _require_exact(environment, "AUTOMATED_TRADING_ENGINE", "v2_active")
    _require_exact(environment, "BINANCE_USE_TESTNET", "true")
    _require_exact(environment, "LIVE_TRADING_ENABLED", "false")
    _require_exact(environment, "BINANCE_AUTO_EXECUTE", "true")
    _required(environment, "BINANCE_API_KEY")
    _required(environment, "BINANCE_API_SECRET")
    _validate_external_baseline(environment)

    database_path = _sqlite_database_path(_required(environment, "POSTGRES_URL"))
    scheduler_state_path = Path(_required(environment, "LOCAL_SCHEDULER_STATE_PATH"))
    if not scheduler_state_path.is_absolute():
        raise PreflightError("LOCAL_SCHEDULER_STATE_PATH must be absolute")
    _require_writable_parent(database_path, "SQLite database")
    _require_writable_parent(scheduler_state_path, "scheduler state")

    if scheduler_pid_path is not None and scheduler_pid_path.is_file():
        raw_pid = scheduler_pid_path.read_text(encoding="ascii").strip()
        if raw_pid.isdigit() and pid_is_alive(int(raw_pid)):
            raise PreflightError(f"scheduler PID already exists: {raw_pid}")

    return PreflightResult(
        engine="v2_active",
        execution_mode="BINANCE_TESTNET",
        database_path=database_path,
        scheduler_state_path=scheduler_state_path,
        memory=memory_snapshot(),
        resource_gate="UNKNOWN_EXTERNAL_VM",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the AI Quant Linux ACTIVE runtime contract.")
    parser.add_argument("--scheduler-pid-path", type=Path, default=None)
    args = parser.parse_args()
    try:
        scheduler_pid_path = args.scheduler_pid_path
        if scheduler_pid_path is None and os.getenv("SCHEDULER_PID_PATH"):
            scheduler_pid_path = Path(os.environ["SCHEDULER_PID_PATH"])
        result = validate_environment(os.environ, scheduler_pid_path=scheduler_pid_path)
    except PreflightError as error:
        print(f"CLOUD_PREFLIGHT_FAILED: {error}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "engine": result.engine,
                "execution_mode": result.execution_mode,
                "database_path": str(result.database_path),
                "scheduler_state_path": str(result.scheduler_state_path),
                "memory": result.memory,
                "resource_gate": result.resource_gate,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
