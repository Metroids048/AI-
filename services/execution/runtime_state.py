"""Shared health state for the desktop API and its external scheduler."""

from __future__ import annotations

import json
import os
from argparse import ArgumentParser
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _state_path() -> Path:
    configured = os.getenv("LOCAL_SCHEDULER_STATE_PATH")
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[2] / "logs" / "scheduler-state.json"


@dataclass(frozen=True)
class ExternalSchedulerState:
    running: bool = False
    heartbeat_at: datetime | None = None
    top20_coverage_count: int = 0
    execution_coverage_count: int = 0
    execution_symbols: tuple[str, ...] = ()
    exchange_info_ready: bool = False
    data_fresh: bool = False
    last_auto_cycle_at: datetime | None = None
    scheduler_error: str | None = None
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
    entry_authorized: bool | None = None
    entry_authority: str | None = None
    entry_authority_reason: str | None = None
    production_authorization_state: str | None = None
    active_entry_strategy: str | None = None
    promotion_eligible: bool | None = None
    trading_state: str | None = None
    startup_contract_errors: tuple[str, ...] = ()


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


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
    return ExternalSchedulerState(
        running=running,
        heartbeat_at=heartbeat_at,
        top20_coverage_count=int(raw.get("top20_coverage_count", 0)),
        execution_coverage_count=execution_coverage_count,
        execution_symbols=execution_symbols,
        exchange_info_ready=bool(raw.get("exchange_info_ready")),
        data_fresh=bool(raw.get("data_fresh")),
        last_auto_cycle_at=_parse_datetime(raw.get("last_auto_cycle_at")),
        scheduler_error=raw.get("scheduler_error") if isinstance(raw.get("scheduler_error"), str) else None,
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
    )


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
    if state.execution_strategy_id in (None, "", "testnet_sampling_v2"):
        errors.append("EXECUTION_STRATEGY_UNAUTHORIZED")
    from services.data.universe import AUTO_SIMULATION_EXECUTION_SYMBOLS

    expected_symbols = tuple(sorted(AUTO_SIMULATION_EXECUTION_SYMBOLS))
    if tuple(sorted(state.execution_symbols)) != expected_symbols:
        errors.append("EXECUTION_SCOPE_MISMATCH")
    if state.execution_coverage_count != len(expected_symbols):
        errors.append("EXECUTION_SCOPE_INCOMPLETE")
    if "automated_trading_v2_cycle" not in state.registered_jobs:
        errors.append("V2_CYCLE_NOT_REGISTERED")
    if any(job in state.registered_jobs for job in ("paper_runtime_cycle", "paper_observation_cycle")):
        errors.append("LEGACY_JOB_REGISTERED")
    if state.legacy_writer_enabled is not False:
        errors.append("LEGACY_WRITER_ENABLED")
    if state.external_baseline_captured is not True:
        errors.append("EXTERNAL_BASELINE_NOT_CAPTURED")
    if state.entry_enabled is not True:
        errors.append("ENTRY_DISABLED")
    if state.entry_authorized is not True:
        errors.append("ENTRY_NOT_AUTHORIZED")
    if state.entry_authority not in {"TESTNET_CANARY", "PRODUCTION"}:
        errors.append("ENTRY_AUTHORITY_INVALID")
    if state.entry_authority == "TESTNET_CANARY" and state.sampling_fallback_enabled is not True:
        errors.append("CANARY_SAMPLING_FALLBACK_DISABLED")
    if state.entry_authority == "PRODUCTION" and state.production_authorization_state != "APPROVED":
        errors.append("PRODUCTION_AUTHORIZATION_INVALID")
    if state.trading_state != "TRADING":
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
