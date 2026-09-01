"""In-process runtime scheduler for local Paper operation.

Celery remains the production/multi-process scheduler. This module gives the
one-click local console the same recurring calls without requiring Redis.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import uuid
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from services.automated_trading.application.production_strategy import (
    EntryAuthority,
    EntryAuthorityResolution,
    resolve_entry_authority,
)
from services.data.binance_clock import BinanceClockUnavailable, fetch_binance_server_time
from services.data.universe import AUTO_SIMULATION_EXECUTION_SYMBOLS, execution_baseline_keys
from services.execution.natural_testnet_mode import natural_testnet_mode_requested
from shared.config import settings

from .runtime_state import write_external_scheduler_state
from .scheduler_coordination import SchedulerCoordinator

Runner = Callable[[], Any]

V2_CYCLE_WATCHDOG_SECONDS = 25 * 60
V2_CRITICAL_JOB = "automated_trading_v2_cycle"
LIVENESS_RECOVERY_HOLD_PREFIX = "LIVENESS_RECOVERY_HOLD:"
ACTIVE_CONFIG_SNAPSHOT_CAPTURE_FAILED = "ACTIVE_CONFIG_SNAPSHOT_CAPTURE_FAILED"
ACTIVE_CONFIG_SNAPSHOT_SCOPE_INCONSISTENT = "ACTIVE_CONFIG_SNAPSHOT_SCOPE_INCONSISTENT"
ENTRY_AUTHORITY_SCOPE_INCONSISTENT = "ENTRY_AUTHORITY_SCOPE_INCONSISTENT"
V2_CYCLE_INCOMPLETE = "V2_CYCLE_INCOMPLETE"
MAX_INPROCESS_RECOVERY_ATTEMPTS = 3

RECOVERY_HEALTHY = "HEALTHY"
RECOVERY_CRASH_DETECTED = "CRASH_DETECTED"
RECOVERY_ENTRY_HOLD = "ENTRY_HOLD"
RECOVERY_MANAGEMENT = "MANAGEMENT_RECOVERY"
RECOVERY_RESTARTING = "RESTARTING"
RECOVERY_VERIFYING = "VERIFYING"
RECOVERY_RECOVERED = "RECOVERED"
RECOVERY_BLOCKED = "RECOVERY_BLOCKED"
RECOVERY_EXHAUSTED = "AUTO_RECOVERY_EXHAUSTED"


@dataclass
class CriticalJobLiveness:
    """Process-local evidence that a critical periodic task is still advancing."""

    registered: bool = False
    task_alive: bool = False
    last_started_at: datetime | None = None
    last_completed_at: datetime | None = None
    last_success_at: datetime | None = None
    last_failure_at: datetime | None = None
    consecutive_failures: int = 0
    last_exception: str | None = None
    currently_running: bool = False
    current_cycle_id: str | None = None


@dataclass
class RecoveryLiveness:
    """Durable-entry-hold state while management/reconciliation continues."""

    state: str = RECOVERY_HEALTHY
    reason: str | None = None
    attempt: int = 0
    entry_hold: bool = False
    worker_restart_count: int = 0
    last_recovery_at: datetime | None = None


@dataclass(frozen=True)
class ActiveStrategyAuthorizationState:
    """Read-only projection of the Active ConfigSnapshot authorization chain."""

    resolution: EntryAuthorityResolution
    active_config_snapshot_id: str | None = None
    active_config_hash: str | None = None
    pending_config_snapshot_id: str | None = None
    pending_config_hash: str | None = None
    active_snapshot_valid: bool = False
    production_authorized: bool = False
    production_authorization_reason: str = "NO_AUTHORIZED_PRODUCTION_STRATEGY"
    forward_authorized: bool = False
    forward_authorization_reason: str = "NO_FORWARD_VALIDATION_CANDIDATE"


def _aligned_run_delay_seconds(
    *,
    now: datetime,
    interval_seconds: float,
    offset_seconds: float,
) -> float:
    """Delay until the next UTC interval boundary plus a bounded exchange-data offset."""

    if interval_seconds <= 0:
        return 0.01
    if interval_seconds < 60:
        return interval_seconds
    bounded_offset = min(max(offset_seconds, 0.0), max(interval_seconds - 0.001, 0.0))
    timestamp = now.astimezone(UTC).timestamp()
    slot_start = int(timestamp / interval_seconds) * interval_seconds
    target = slot_start + bounded_offset
    if target <= timestamp:
        target += interval_seconds
    return max(target - timestamp, 0.01)


def _preload_celery_task_api() -> None:
    """Initialize Celery's lazy task exports before worker threads import task modules."""
    from celery import shared_task

    if not callable(shared_task):  # pragma: no cover - dependency contract guard
        raise RuntimeError("Celery shared_task API is unavailable")


@dataclass
class RuntimeSchedulerStatus:
    mode: str = "inprocess"
    running: bool = False
    started_at: datetime | None = None
    last_auto_cycle_at: datetime | None = None
    next_cycle_eta_seconds: int | None = None
    scheduler_error: str | None = None
    run_counts: dict[str, int] = field(default_factory=dict)
    failure_counts: dict[str, int] = field(default_factory=dict)
    last_results: dict[str, Any] = field(default_factory=dict)
    last_success_at: dict[str, datetime] = field(default_factory=dict)
    last_failure_at: dict[str, datetime] = field(default_factory=dict)
    scheduler_instance_id: str | None = None
    current_lock_owner: str | None = None
    last_scheduled_for: datetime | None = None
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
    entry_authorized: bool = False
    entry_authority: str = EntryAuthority.NONE.value
    entry_authority_reason: str = "NO_AUTHORIZED_PRODUCTION_STRATEGY"
    production_authorization_state: str = "PENDING"
    active_entry_strategy: str | None = None
    promotion_eligible: bool = False
    trading_state: str = "ENTRY_BLOCKED"
    startup_contract_errors: tuple[str, ...] = ()
    natural_testnet_enabled: bool = False
    critical_jobs: dict[str, CriticalJobLiveness] = field(default_factory=dict)
    recovery: RecoveryLiveness = field(default_factory=RecoveryLiveness)
    active_config_snapshot_id: str | None = None
    active_config_hash: str | None = None
    pending_config_snapshot_id: str | None = None
    pending_config_hash: str | None = None
    active_snapshot_valid: bool = False
    production_authorized: bool = False
    production_authorization_reason: str = "NO_AUTHORIZED_PRODUCTION_STRATEGY"
    forward_authorized: bool = False
    forward_authorization_reason: str = "NO_FORWARD_VALIDATION_CANDIDATE"
    entry_control_reason: str | None = None
    reconciliation_healthy: bool | None = None

    def model_dump(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "running": self.running,
            "started_at": self.started_at,
            "last_auto_cycle_at": self.last_auto_cycle_at,
            "next_cycle_eta_seconds": self.next_cycle_eta_seconds,
            "scheduler_error": self.scheduler_error,
            "run_counts": dict(self.run_counts),
            "failure_counts": dict(self.failure_counts),
            "last_results": dict(self.last_results),
            "last_success_at": dict(self.last_success_at),
            "last_failure_at": dict(self.last_failure_at),
            "scheduler_instance_id": self.scheduler_instance_id,
            "current_lock_owner": self.current_lock_owner,
            "last_scheduled_for": self.last_scheduled_for,
            "engine_activation": self.engine_activation,
            "execution_mode": self.execution_mode,
            "execution_strategy_id": self.execution_strategy_id,
            "registered_jobs": self.registered_jobs,
            "legacy_writer_enabled": self.legacy_writer_enabled,
            "entry_enabled": self.entry_enabled,
            "sampling_fallback_enabled": self.sampling_fallback_enabled,
            "external_baseline_captured": self.external_baseline_captured,
            "external_baseline_value": self.external_baseline_value,
            "external_baseline_source": self.external_baseline_source,
            "external_baseline_lifecycle": self.external_baseline_lifecycle,
            "external_baseline_drift_keys": self.external_baseline_drift_keys,
            "entry_authorized": self.entry_authorized,
            "entry_authority": self.entry_authority,
            "entry_authority_reason": self.entry_authority_reason,
            "production_authorization_state": self.production_authorization_state,
            "active_entry_strategy": self.active_entry_strategy,
            "promotion_eligible": self.promotion_eligible,
            "trading_state": self.trading_state,
            "startup_contract_errors": self.startup_contract_errors,
            "natural_testnet_enabled": self.natural_testnet_enabled,
            "critical_jobs": {
                name: {
                    "registered": job.registered,
                    "task_alive": job.task_alive,
                    "last_started_at": job.last_started_at,
                    "last_completed_at": job.last_completed_at,
                    "last_success_at": job.last_success_at,
                    "last_failure_at": job.last_failure_at,
                    "consecutive_failures": job.consecutive_failures,
                    "last_exception": job.last_exception,
                    "currently_running": job.currently_running,
                    "current_cycle_id": job.current_cycle_id,
                }
                for name, job in self.critical_jobs.items()
            },
            "recovery": {
                "state": self.recovery.state,
                "reason": self.recovery.reason,
                "attempt": self.recovery.attempt,
                "entry_hold": self.recovery.entry_hold,
                "worker_restart_count": self.recovery.worker_restart_count,
                "last_recovery_at": self.recovery.last_recovery_at,
            },
            "active_config_snapshot_id": self.active_config_snapshot_id,
            "active_config_hash": self.active_config_hash,
            "pending_config_snapshot_id": self.pending_config_snapshot_id,
            "pending_config_hash": self.pending_config_hash,
            "active_snapshot_valid": self.active_snapshot_valid,
            "production_authorized": self.production_authorized,
            "production_authorization_reason": self.production_authorization_reason,
            "forward_authorized": self.forward_authorized,
            "forward_authorization_reason": self.forward_authorization_reason,
            "entry_control_reason": self.entry_control_reason,
            "reconciliation_healthy": self.reconciliation_healthy,
        }


_ALLOWED_EXTERNAL_BASELINE_KEYS = execution_baseline_keys()


def _normalize_external_baseline(payload: object) -> dict[str, str] | None:
    # None means "not a valid capture record". An empty mapping is a valid
    # capture record proving zero unmanaged external exposure, so it must
    # normalize to {} rather than None.
    if not isinstance(payload, dict):
        return None
    normalized: dict[str, str] = {}
    try:
        for key, value in payload.items():
            quantity = Decimal(str(value))
            if (
                not isinstance(key, str)
                or key not in _ALLOWED_EXTERNAL_BASELINE_KEYS
                or not quantity.is_finite()
                or quantity <= 0
            ):
                return None
            normalized[key] = str(quantity)
    except (ArithmeticError, ValueError):
        return None
    return normalized


def _external_baseline_capture() -> tuple[bool, dict[str, str] | None, str | None]:
    if os.getenv("V2_ALLOW_UNMANAGED_EXTERNAL_POSITIONS", "false").lower() != "true":
        return False, None, None
    raw = os.getenv("V2_EXTERNAL_BASELINE_JSON", "").strip()
    if not raw:
        return False, None, None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return False, None, None
    normalized = _normalize_external_baseline(payload)
    if normalized is None:
        return False, None, None
    configured_path = os.getenv("V2_EXTERNAL_BASELINE_PATH", "").strip()
    source = os.getenv("V2_EXTERNAL_BASELINE_SOURCE", "").strip()
    if not configured_path or not source:
        return False, None, None
    try:
        persistent_path = Path(configured_path).resolve(strict=True)
        persisted_record = json.loads(persistent_path.read_text(encoding="utf-8"))
        persisted = _normalize_external_baseline(
            persisted_record.get("positions") if isinstance(persisted_record, dict) else None
        )
    except (OSError, json.JSONDecodeError):
        return False, None, None
    expected_source = f"persistent_file:{persistent_path}"
    # The one-click ACTIVE launcher may temporarily provide an authoritative
    # bootstrap snapshot while it repairs an exact V2 projection gap.  New
    # entries are disabled for this phase; the scheduler must still run its
    # existing recovery/protection path.  The launcher replaces this source
    # with the persisted-file contract before re-enabling entries.
    if (
        source == "projection_gap_recovery_bootstrap"
        and os.getenv("V2_PROJECTION_RECOVERY_BOOTSTRAP", "false").lower() == "true"
    ):
        return True, normalized, source
    if (
        not isinstance(persisted_record, dict)
        or persisted_record.get("schema_version") != 1
        or tuple(persisted_record.get("captured_symbols", ())) != AUTO_SIMULATION_EXECUTION_SYMBOLS
        or persisted_record.get("source") != "binance_testnet_authoritative_snapshot"
        or persisted != normalized
        or source != expected_source
    ):
        return False, None, None
    return True, normalized, source


def _active_entry_authorization() -> tuple[bool, bool, tuple[str, ...]]:
    """Read existing V2 controls without creating or mutating runtime state."""
    from services.automated_trading.infrastructure.models import V2RuntimeControl
    from services.database import get_session_factory
    from services.strategy_library import ConfigSnapshotRepository, PaperRunRepository

    try:
        with get_session_factory()() as session:
            control = session.get(V2RuntimeControl, "global")
            entry_enabled = bool(control.entry_enabled) if control is not None else False
            running_directional = [
                run
                for run in PaperRunRepository(session).list_paper_runs()
                if run.paper_status == "running" and run.execution_profile.get("strategy_lane") == "directional"
            ]
            if len(running_directional) != 1:
                return entry_enabled, False, ("DIRECTIONAL_PROFILE_UNAVAILABLE",)
            run = running_directional[0]
            active = ConfigSnapshotRepository(session).get_active(run.paper_run_id or "")
            active_profile = active.config.get("execution_profile") if active is not None else None
            profile = active_profile if isinstance(active_profile, dict) else run.execution_profile
            sampling_enabled = bool(profile.get("simulation_sampling_fallback_enabled", False))
            return entry_enabled, sampling_enabled, ()
    except Exception:  # noqa: BLE001
        return False, False, ("ENTRY_CONTRACT_STATE_UNAVAILABLE",)


def _runtime_entry_control_state() -> tuple[bool | None, str | None]:
    from services.automated_trading.infrastructure.models import V2RuntimeControl
    from services.database import get_session_factory

    try:
        with get_session_factory()() as session:
            control = session.get(V2RuntimeControl, "global")
            if control is None:
                return None, None
            reason = str(control.reason) if control.reason else None
            return bool(control.entry_enabled), reason
    except Exception:  # noqa: BLE001
        return None, None


def _runtime_entry_control_reason() -> str | None:
    return _runtime_entry_control_state()[1]


def initialize_standard_runtime_entry_control() -> None:
    """Arm only an absent RuntimeControl row for the standard Testnet worker.

    Existing rows are durable operator/recovery state and are never changed by
    startup. Authority remains independently derived from the Active
    ConfigSnapshot, so this initializer cannot grant strategy permission.
    """
    from services.automated_trading.infrastructure.repository import AutomatedTradingRepository
    from services.database import get_session_factory

    with get_session_factory()() as session:
        repository = AutomatedTradingRepository(session)
        repository.ensure_runtime_control(
            scope="global",
            entry_enabled=True,
            reason="STANDARD_RUNTIME_ENABLED",
            updated_by="runtime-bootstrap",
        )
        repository.commit()


def _claim_liveness_recovery_hold(*, hold_reason: str) -> bool:
    """Atomically claim RuntimeControl without overwriting an operator-owned pause."""
    from services.automated_trading.infrastructure.repository import AutomatedTradingRepository
    from services.database import get_session_factory

    try:
        with get_session_factory()() as session:
            repository = AutomatedTradingRepository(session)
            control = repository.ensure_runtime_control(
                scope="global",
                entry_enabled=False,
                reason=hold_reason,
                updated_by="liveness-supervisor",
            )
            observed_enabled = bool(control.entry_enabled)
            observed_reason = str(control.reason) if control.reason else None
            observed_version = int(control.version)
            if not observed_enabled and not str(observed_reason or "").startswith(LIVENESS_RECOVERY_HOLD_PREFIX):
                return False
            if not observed_enabled and observed_reason == hold_reason:
                repository.commit()
                return True
            claimed = repository.compare_and_set_runtime_control(
                scope="global",
                expected_version=observed_version,
                expected_entry_enabled=observed_enabled,
                expected_reason=observed_reason,
                entry_enabled=False,
                reason=hold_reason,
                updated_by="liveness-supervisor",
            )
            if not claimed:
                session.rollback()
                return False
            repository.commit()
        return True
    except Exception:  # noqa: BLE001 - fail closed if the durable control is unavailable
        return False


def _clear_liveness_recovery_hold() -> tuple[bool, str | None]:
    """Atomically clear only the exact system-owned hold that was observed."""
    from services.automated_trading.infrastructure.repository import AutomatedTradingRepository
    from services.database import get_session_factory

    try:
        with get_session_factory()() as session:
            repository = AutomatedTradingRepository(session)
            control = repository.get_runtime_control("global")
            observed_enabled = bool(control.entry_enabled)
            observed_reason = str(control.reason) if control.reason else None
            observed_version = int(control.version)
            if observed_enabled or not str(observed_reason or "").startswith(LIVENESS_RECOVERY_HOLD_PREFIX):
                return False, observed_reason
            cleared = repository.compare_and_set_runtime_control(
                scope="global",
                expected_version=observed_version,
                expected_entry_enabled=False,
                expected_reason=observed_reason,
                entry_enabled=True,
                reason="LIVENESS_RECOVERY_RECOVERED",
                updated_by="liveness-supervisor",
            )
            if not cleared:
                session.rollback()
                return False, _runtime_entry_control_reason()
            repository.commit()
        return True, "LIVENESS_RECOVERY_RECOVERED"
    except Exception:  # noqa: BLE001 - fail closed if ownership changes or storage fails
        return False, _runtime_entry_control_reason()


def persist_liveness_recovery_hold(reason: str) -> bool:
    """Public helper for the process supervisor to disable only new entries."""
    normalized = str(reason or "unknown").strip()
    hold_reason = (
        normalized
        if normalized.startswith(LIVENESS_RECOVERY_HOLD_PREFIX)
        else f"{LIVENESS_RECOVERY_HOLD_PREFIX}{normalized}"
    )
    return _claim_liveness_recovery_hold(hold_reason=hold_reason)


def _active_execution_strategy_identity() -> tuple[str, bool]:
    """Expose only a manifest-approved candidate as the ACTIVE entry brain."""
    from services.automated_trading.application.production_strategy import resolve_production_authorization
    from services.database import get_session_factory
    from services.strategy_library import ConfigSnapshotRepository, PaperRunRepository

    try:
        with get_session_factory()() as session:
            runs = [
                run
                for run in PaperRunRepository(session).list_paper_runs()
                if run.paper_status == "running" and run.execution_profile.get("strategy_lane") == "directional"
            ]
            if len(runs) != 1 or runs[0].paper_run_id is None:
                return "production_strategy_pending", False
            snapshot = ConfigSnapshotRepository(session).get_active(runs[0].paper_run_id)
            if snapshot is None:
                return "production_strategy_pending", False
            authorizations = (
                resolve_production_authorization(
                    snapshot_config=snapshot.config,
                    snapshot_hash=snapshot.config_hash,
                    symbol=symbol,
                )
                for symbol in AUTO_SIMULATION_EXECUTION_SYMBOLS
            )
            resolved = tuple(authorizations)
            if not resolved or not all(item.authorized for item in resolved):
                return "production_strategy_pending", False
            candidate_id = resolved[0].candidate_id
            if not candidate_id or any(item.candidate_id != candidate_id for item in resolved):
                return "production_strategy_pending", False
            return candidate_id, True
    except Exception:  # noqa: BLE001
        return "production_strategy_pending", False


def _active_strategy_authorization_state(*, execution_mode: str) -> ActiveStrategyAuthorizationState:
    """Resolve Production/Forward from one immutable Active ConfigSnapshot.

    The scheduler consumes the same exact-scope authorization facts as the V2
    cycle.  RuntimeControl and launcher flags are deliberately absent here.
    """
    from services.automated_trading.application.production_strategy import (
        resolve_production_authorization,
        resolve_testnet_forward_authorization,
    )
    from services.database import get_session_factory
    from services.strategy_library import ConfigSnapshotRepository, PaperRunRepository
    from shared.models.trading import canonical_config_hash

    unavailable = EntryAuthorityResolution(
        EntryAuthority.NONE,
        "ENTRY_CONTRACT_STATE_UNAVAILABLE",
        None,
        False,
    )
    try:
        with get_session_factory()() as session:
            runs = [
                run
                for run in PaperRunRepository(session).list_paper_runs()
                if run.paper_status == "running" and run.execution_profile.get("strategy_lane") == "directional"
            ]
            if len(runs) != 1 or runs[0].paper_run_id is None:
                return ActiveStrategyAuthorizationState(
                    resolution=EntryAuthorityResolution(
                        EntryAuthority.NONE,
                        "DIRECTIONAL_PROFILE_UNAVAILABLE",
                        None,
                        False,
                    )
                )
            run = runs[0]
            run_id = run.paper_run_id
            assert run_id is not None
            snapshots = ConfigSnapshotRepository(session)
            active = snapshots.get_active(run_id)
            pending = snapshots.get_pending(run_id)
            if active is None:
                return ActiveStrategyAuthorizationState(
                    resolution=EntryAuthorityResolution(
                        EntryAuthority.NONE,
                        "NO_ACTIVE_CONFIG_SNAPSHOT",
                        None,
                        False,
                    ),
                    pending_config_snapshot_id=(pending.config_snapshot_id if pending is not None else None),
                    pending_config_hash=(pending.config_hash if pending is not None else None),
                )
            snapshot_valid = canonical_config_hash(active.config) == active.config_hash
            if not snapshot_valid:
                return ActiveStrategyAuthorizationState(
                    resolution=EntryAuthorityResolution(
                        EntryAuthority.NONE,
                        "CONFIG_SNAPSHOT_HASH_MISMATCH",
                        None,
                        False,
                    ),
                    active_config_snapshot_id=active.config_snapshot_id,
                    active_config_hash=active.config_hash,
                    pending_config_snapshot_id=(pending.config_snapshot_id if pending is not None else None),
                    pending_config_hash=(pending.config_hash if pending is not None else None),
                    active_snapshot_valid=False,
                    production_authorization_reason="CONFIG_SNAPSHOT_HASH_MISMATCH",
                    forward_authorization_reason="CONFIG_SNAPSHOT_HASH_MISMATCH",
                )
            production = tuple(
                resolve_production_authorization(
                    snapshot_config=active.config,
                    snapshot_hash=active.config_hash,
                    symbol=symbol,
                )
                for symbol in AUTO_SIMULATION_EXECUTION_SYMBOLS
            )
            forward = tuple(
                resolve_testnet_forward_authorization(
                    snapshot_config=active.config,
                    snapshot_hash=active.config_hash,
                    symbol=symbol,
                    execution_mode=execution_mode,
                )
                for symbol in AUTO_SIMULATION_EXECUTION_SYMBOLS
            )

            production_ids = {item.candidate_id for item in production if item.authorized and item.candidate_id}
            forward_ids = {item.candidate_id for item in forward if item.authorized and item.candidate_id}
            production_authorized = (
                bool(production) and all(item.authorized for item in production) and len(production_ids) == 1
            )
            forward_authorized = bool(forward) and all(item.authorized for item in forward) and len(forward_ids) == 1
            production_reason = (
                "AUTHORIZED"
                if production_authorized
                else next((item.reason for item in production if not item.authorized), "PRODUCTION_SCOPE_INCONSISTENT")
            )
            forward_reason = (
                "TESTNET_FORWARD_AUTHORIZED"
                if forward_authorized
                else next((item.reason for item in forward if not item.authorized), "FORWARD_SCOPE_INCONSISTENT")
            )
            resolution = resolve_entry_authority(
                production_authorized=production_authorized,
                production_strategy_id=next(iter(production_ids), None),
                forward_authorized=forward_authorized,
                forward_strategy_id=next(iter(forward_ids), None),
                execution_mode=execution_mode,
                operator_testnet_canary_enabled=False,
                explicit_testnet_canary=False,
            )
            if resolution.authority is EntryAuthority.NONE:
                resolution = EntryAuthorityResolution(EntryAuthority.NONE, forward_reason, None, False)
            return ActiveStrategyAuthorizationState(
                resolution=resolution,
                active_config_snapshot_id=active.config_snapshot_id,
                active_config_hash=active.config_hash,
                pending_config_snapshot_id=(pending.config_snapshot_id if pending is not None else None),
                pending_config_hash=(pending.config_hash if pending is not None else None),
                active_snapshot_valid=snapshot_valid,
                production_authorized=production_authorized,
                production_authorization_reason=production_reason,
                forward_authorized=forward_authorized,
                forward_authorization_reason=forward_reason,
            )
    except Exception:  # noqa: BLE001 - runtime authorization must fail closed
        return ActiveStrategyAuthorizationState(resolution=unavailable)


class RuntimeScheduler:
    """Run local recurring jobs inside the FastAPI event loop."""

    def __init__(
        self,
        *,
        paper_cycle_seconds: float | None = None,
        heartbeat_seconds: float | None = None,
        notification_seconds: float | None = None,
        news_poll_seconds: float = 180.0,
        macro_poll_seconds: float = 900.0,
        social_poll_seconds: float = 300.0,
        risk_sweep_seconds: float = 60.0,
        edge_stats_refresh_seconds: float = 7 * 24 * 60 * 60,
        volatility_risk_refresh_seconds: float = 7 * 24 * 60 * 60,
        daily_review_check_seconds: float = 60.0,
        market_review_seconds: float | None = None,
        paper_cycle_runner: Runner | None = None,
        heartbeat_runner: Runner | None = None,
        exchange_info_refresh_runner: Runner | None = None,
        news_poll_runner: Runner | None = None,
        macro_poll_runner: Runner | None = None,
        social_poll_runner: Runner | None = None,
        risk_sweep_runner: Runner | None = None,
        edge_stats_refresh_runner: Runner | None = None,
        volatility_risk_refresh_runner: Runner | None = None,
        notification_runner: Runner | None = None,
        market_review_runner: Runner | None = None,
        daily_review_runner: Callable[[str | None], Any] | None = None,
        coordinator: SchedulerCoordinator | None = None,
        scheduler_instance_id: str | None = None,
    ) -> None:
        self.paper_cycle_seconds = float(paper_cycle_seconds or settings.paper_runtime_cycle_seconds)
        self.paper_cycle_offset_seconds = float(settings.paper_runtime_cycle_offset_seconds)
        self.paper_observation_offset_seconds = float(settings.paper_observation_cycle_offset_seconds)
        self.heartbeat_seconds = float(heartbeat_seconds or settings.market_data_heartbeat_seconds)
        self.notification_seconds = float(notification_seconds or settings.notification_dispatch_seconds)
        self.news_poll_seconds = float(news_poll_seconds)
        self.macro_poll_seconds = float(macro_poll_seconds)
        self.social_poll_seconds = float(social_poll_seconds)
        self.risk_sweep_seconds = float(risk_sweep_seconds)
        self.edge_stats_refresh_seconds = float(edge_stats_refresh_seconds)
        self.volatility_risk_refresh_seconds = float(volatility_risk_refresh_seconds)
        self.daily_review_check_seconds = float(daily_review_check_seconds)
        self.market_review_seconds = float(
            market_review_seconds if market_review_seconds is not None else settings.market_review_seconds
        )
        self.paper_cycle_runner = paper_cycle_runner or _default_paper_cycle_runner
        self.heartbeat_runner = heartbeat_runner or _default_heartbeat_runner
        self.exchange_info_refresh_runner = exchange_info_refresh_runner or _default_exchange_info_refresh_runner
        self.news_poll_runner = news_poll_runner or _default_news_poll_runner
        self.macro_poll_runner = macro_poll_runner or _default_macro_poll_runner
        self.social_poll_runner = social_poll_runner or _default_social_poll_runner
        self.risk_sweep_runner = risk_sweep_runner or _default_risk_sweep_runner
        self.edge_stats_refresh_runner = edge_stats_refresh_runner or _default_edge_stats_refresh_runner
        self.volatility_risk_refresh_runner = volatility_risk_refresh_runner or _default_volatility_risk_refresh_runner
        self.notification_runner = notification_runner or _default_notification_runner
        self.market_review_runner = market_review_runner or _default_market_review_runner
        self.daily_review_runner = daily_review_runner or _default_daily_review_runner
        generated_instance_id = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:12]}"
        self.scheduler_instance_id = scheduler_instance_id or generated_instance_id
        self.status = RuntimeSchedulerStatus(
            mode="inprocess",
            scheduler_instance_id=self.scheduler_instance_id,
        )
        self.coordinator = coordinator
        self._tasks: list[asyncio.Task] = []
        self._stop_event: asyncio.Event | None = None
        self._last_daily_review_date: date | None = None
        self._next_cycle_at: datetime | None = None
        self._scheduler_errors: dict[str, str] = {}
        self._critical_tasks: dict[str, asyncio.Task] = {}
        self._critical_task_backoffs = (5.0, 15.0, 30.0)

    def _refresh_snapshot_lifecycle_from_store(self) -> bool:
        """Publish current Active/Pending IDs after a background config stage."""
        lifecycle = _active_strategy_authorization_state(
            execution_mode=str(self.status.execution_mode or "BINANCE_TESTNET"),
        )
        if lifecycle.active_config_snapshot_id is None or lifecycle.active_config_hash is None:
            return False
        self.status.active_config_snapshot_id = lifecycle.active_config_snapshot_id
        self.status.active_config_hash = lifecycle.active_config_hash
        self.status.active_snapshot_valid = lifecycle.active_snapshot_valid
        self.status.pending_config_snapshot_id = lifecycle.pending_config_snapshot_id
        self.status.pending_config_hash = lifecycle.pending_config_hash
        return True

    def _set_recovery_hold(self, reason: str) -> None:
        """Freeze new exposure durably while leaving the V2 management loop alive."""
        normalized = str(reason or "unknown").strip()
        hold_reason = (
            normalized
            if normalized.startswith(LIVENESS_RECOVERY_HOLD_PREFIX)
            else f"{LIVENESS_RECOVERY_HOLD_PREFIX}{normalized}"
        )
        persisted = persist_liveness_recovery_hold(hold_reason)
        current_enabled, current_reason = _runtime_entry_control_state()
        manual_pause_preserved = current_enabled is False and not str(current_reason or "").startswith(
            LIVENESS_RECOVERY_HOLD_PREFIX
        )
        recovery = self.status.recovery
        recovery.state = (
            RECOVERY_ENTRY_HOLD if persisted else RECOVERY_HEALTHY if manual_pause_preserved else RECOVERY_BLOCKED
        )
        recovery.reason = (
            hold_reason
            if persisted
            else str(current_reason)
            if manual_pause_preserved
            else f"{hold_reason}:PERSIST_FAILED"
        )
        recovery.entry_hold = persisted
        recovery.last_recovery_at = datetime.now(UTC)
        self.status.entry_enabled = False
        self.status.entry_authorized = False
        if manual_pause_preserved:
            self.status.entry_control_reason = str(current_reason)
        # RuntimeControl owns only the temporary new-entry switch.  Keep the
        # strategy/snapshot authority visible while management and
        # reconciliation continue under the hold.
        self.status.trading_state = "MANAGEMENT_ONLY"
        self._publish_external_state()

    def _maybe_clear_recovery_hold(self, cycle_result: object) -> bool:
        """Clear only a supervisor-created hold after a fresh healthy V2 cycle."""
        recovery = self.status.recovery
        if not recovery.entry_hold or recovery.state not in {
            RECOVERY_ENTRY_HOLD,
            RECOVERY_MANAGEMENT,
            RECOVERY_VERIFYING,
        }:
            return False
        if not isinstance(cycle_result, dict) or cycle_result.get("status") != "completed":
            return False
        results = cycle_result.get("results")
        if (
            not isinstance(results, list)
            or not results
            or any(
                not isinstance(item, dict) or str(item.get("reconciliation_status") or "").upper() != "HEALTHY"
                for item in results
            )
        ):
            return False
        preflight = self._safe_v2_recovery_check()
        if not preflight.get("safe_to_restart"):
            recovery.state = RECOVERY_MANAGEMENT
            recovery.reason = str(preflight.get("reason") or recovery.reason or "recovery_pending")
            self._publish_external_state()
            return False
        if (
            self.status.trading_state == "DEGRADED"
            or self.status.reconciliation_healthy is not True
            or (
                self.status.entry_authority in {EntryAuthority.TESTNET_FORWARD.value, EntryAuthority.PRODUCTION.value}
                and (
                    self.status.active_snapshot_valid is not True
                    or not self.status.active_config_snapshot_id
                    or not self.status.active_config_hash
                    or not self.status.active_entry_strategy
                )
            )
        ):
            recovery.state = RECOVERY_MANAGEMENT
            recovery.reason = "recovery_readiness_incomplete"
            self._publish_external_state()
            return False
        cleared, control_reason = _clear_liveness_recovery_hold()
        if not cleared and not str(control_reason or "").startswith(LIVENESS_RECOVERY_HOLD_PREFIX):
            # A manual/operator pause owns the switch and must never be overridden.
            recovery.entry_hold = False
            recovery.state = RECOVERY_HEALTHY
            recovery.reason = str(control_reason or "operator_control")
            self._publish_external_state()
            return False
        if not cleared:
            recovery.state = RECOVERY_MANAGEMENT
            recovery.reason = "hold_clear_failed"
            self._publish_external_state()
            return False
        recovery.state = RECOVERY_RECOVERED
        recovery.reason = "LIVENESS_RECOVERY_RECOVERED"
        recovery.entry_hold = False
        recovery.attempt = 0
        recovery.last_recovery_at = datetime.now(UTC)
        self.status.entry_enabled = True
        self.status.entry_authorized = self.status.entry_authority != EntryAuthority.NONE.value
        self.status.entry_control_reason = "LIVENESS_RECOVERY_RECOVERED"
        self.status.trading_state = "TRADING_READY" if self.status.entry_authorized else "ENTRY_BLOCKED"
        self._publish_external_state()
        return True

    def start(self) -> None:
        if self.status.running:
            return
        _preload_celery_task_api()
        if self.coordinator is None:
            from services.database import get_session_factory

            self.coordinator = SchedulerCoordinator(
                session_factory=get_session_factory(),
                instance_id=self.scheduler_instance_id,
            )
        self._stop_event = asyncio.Event()
        self.status.running = True
        self.status.started_at = datetime.now(UTC)
        self.status.scheduler_error = None
        self._scheduler_errors.clear()
        from services.automated_trading.infrastructure.runtime_lock import resolve_engine_activation

        v2_activation = resolve_engine_activation(settings)
        scheduled_jobs = _resolve_runtime_scheduler_jobs(v2_activation)
        self.status.engine_activation = v2_activation.v2_activation.value
        self.status.execution_mode = v2_activation.execution_mode.value
        self.status.execution_strategy_id = "production_strategy_pending"
        self.status.registered_jobs = tuple(sorted(scheduled_jobs))
        self.status.critical_jobs = {V2_CRITICAL_JOB: CriticalJobLiveness(registered=V2_CRITICAL_JOB in scheduled_jobs)}
        self.status.legacy_writer_enabled = v2_activation.allow_legacy_writer
        self.status.startup_contract_errors = ()
        self.status.entry_enabled = None
        self.status.sampling_fallback_enabled = None
        self.status.external_baseline_captured = None
        self.status.external_baseline_value = None
        self.status.external_baseline_source = None
        self.status.external_baseline_lifecycle = None
        self.status.external_baseline_drift_keys = ()
        self.status.entry_authorized = False
        self.status.entry_authority = EntryAuthority.NONE.value
        self.status.entry_authority_reason = "NO_AUTHORIZED_PRODUCTION_STRATEGY"
        self.status.production_authorization_state = "PENDING"
        self.status.active_entry_strategy = None
        self.status.promotion_eligible = False
        self.status.trading_state = "ENTRY_BLOCKED"
        self.status.natural_testnet_enabled = natural_testnet_mode_requested()
        self.status.active_config_snapshot_id = None
        self.status.active_config_hash = None
        self.status.pending_config_snapshot_id = None
        self.status.pending_config_hash = None
        self.status.active_snapshot_valid = False
        self.status.production_authorized = False
        self.status.production_authorization_reason = "NO_AUTHORIZED_PRODUCTION_STRATEGY"
        self.status.forward_authorized = False
        self.status.forward_authorization_reason = "NO_FORWARD_VALIDATION_CANDIDATE"
        self.status.entry_control_reason = None
        self.status.reconciliation_healthy = None
        if v2_activation.v2_activation.value == "ACTIVE":
            entry_enabled, sampling_enabled, contract_errors = _active_entry_authorization()
            control_reason = _runtime_entry_control_reason()
            recovery_hold_active = str(control_reason or "").startswith(LIVENESS_RECOVERY_HOLD_PREFIX)
            authorization = _active_strategy_authorization_state(
                execution_mode=v2_activation.execution_mode.value,
            )
            authority = authorization.resolution
            if (
                authority.authority is EntryAuthority.NONE
                and v2_activation.execution_mode.value == "BINANCE_TESTNET"
                and self.status.natural_testnet_enabled
                and sampling_enabled
            ):
                authority = resolve_entry_authority(
                    production_authorized=authorization.production_authorized,
                    production_strategy_id=None,
                    forward_authorized=authorization.forward_authorized,
                    forward_strategy_id=None,
                    execution_mode=v2_activation.execution_mode.value,
                    operator_testnet_canary_enabled=self.status.natural_testnet_enabled,
                    explicit_testnet_canary=True,
                )
            if recovery_hold_active:
                entry_enabled = False
            baseline_captured, baseline_value, baseline_source = _external_baseline_capture()
            errors = list(contract_errors)
            if v2_activation.execution_mode.value != "BINANCE_TESTNET":
                errors.append("EXECUTION_MODE_MISMATCH")
            if "automated_trading_v2_cycle" not in scheduled_jobs:
                errors.append("V2_CYCLE_NOT_REGISTERED")
            if v2_activation.allow_legacy_writer:
                errors.append("LEGACY_WRITER_ENABLED")
            if not baseline_captured:
                errors.append("EXTERNAL_BASELINE_NOT_CAPTURED")
            self.status.entry_enabled = entry_enabled
            if recovery_hold_active:
                self.status.recovery.state = RECOVERY_ENTRY_HOLD
                self.status.recovery.reason = control_reason
                self.status.recovery.entry_hold = True
                self.status.recovery.worker_restart_count = int(os.getenv("V2_WORKER_RESTART_COUNT", "0") or 0)
            self.status.execution_strategy_id = authority.active_strategy_id or "production_strategy_pending"
            self.status.sampling_fallback_enabled = sampling_enabled
            self.status.entry_control_reason = control_reason
            self.status.active_config_snapshot_id = authorization.active_config_snapshot_id
            self.status.active_config_hash = authorization.active_config_hash
            self.status.pending_config_snapshot_id = authorization.pending_config_snapshot_id
            self.status.pending_config_hash = authorization.pending_config_hash
            self.status.active_snapshot_valid = authorization.active_snapshot_valid
            self.status.production_authorized = authorization.production_authorized
            self.status.production_authorization_reason = authorization.production_authorization_reason
            self.status.forward_authorized = authorization.forward_authorized
            self.status.forward_authorization_reason = authorization.forward_authorization_reason
            self.status.external_baseline_captured = baseline_captured
            self.status.external_baseline_value = baseline_value
            self.status.external_baseline_source = baseline_source
            lifecycle = os.getenv("V2_EXTERNAL_BASELINE_LIFECYCLE", "").strip() or None
            raw_drift_keys = os.getenv("V2_EXTERNAL_BASELINE_DRIFT_KEYS", "").strip()
            try:
                parsed_drift_keys = json.loads(raw_drift_keys) if raw_drift_keys else []
            except json.JSONDecodeError:
                parsed_drift_keys = []
            self.status.external_baseline_lifecycle = lifecycle
            self.status.external_baseline_drift_keys = tuple(
                str(key) for key in parsed_drift_keys if isinstance(key, str)
            )
            self.status.startup_contract_errors = tuple(dict.fromkeys(errors))
            # ACTIVE can keep reconciling/protecting existing V2 positions with
            # entries paused.  Candidate-level authorization is checked inside
            # each cycle against the immutable ConfigSnapshot and manifest.
            self.status.entry_authorized = bool(
                entry_enabled
                and authority.authority is not EntryAuthority.NONE
                and self.status.reconciliation_healthy is True
            )
            self.status.entry_authority = authority.authority.value
            self.status.entry_authority_reason = authority.reason
            self.status.production_authorization_state = (
                "APPROVED" if authorization.production_authorized else "PENDING"
            )
            self.status.active_entry_strategy = authority.active_strategy_id
            self.status.promotion_eligible = authority.promotion_eligible
            self.status.trading_state = (
                "MANAGEMENT_ONLY"
                if not entry_enabled
                else "TRADING_READY"
                if self.status.entry_authorized
                else "ENTRY_BLOCKED"
            )
            if self.status.startup_contract_errors:
                self.status.running = False
                self.status.scheduler_error = "ACTIVE_STARTUP_CONTRACT_FAILED: " + ";".join(
                    self.status.startup_contract_errors
                )
                self._publish_external_state()
                raise RuntimeError(self.status.scheduler_error)
        self._tasks = []
        if "paper_runtime_cycle" in scheduled_jobs:
            self._tasks.append(
                asyncio.create_task(
                    self._run_periodic(
                        name="paper_runtime_cycle",
                        interval_seconds=self.paper_cycle_seconds,
                        runner=self.paper_cycle_runner,
                        records_auto_cycle=True,
                        run_immediately=False,
                        coordinated=True,
                        align_to_interval=True,
                        interval_offset_seconds=self.paper_cycle_offset_seconds,
                    )
                )
            )
        if "paper_observation_cycle" in scheduled_jobs:
            self._tasks.append(
                asyncio.create_task(
                    self._run_periodic(
                        name="paper_observation_cycle",
                        interval_seconds=self.paper_cycle_seconds,
                        runner=_default_observation_cycle_runner,
                        records_auto_cycle=False,
                        run_immediately=False,
                        coordinated=True,
                        align_to_interval=True,
                        interval_offset_seconds=self.paper_observation_offset_seconds,
                    )
                )
            )
        if "automated_trading_v2_cycle" in scheduled_jobs:
            # Publish the created task as alive immediately.  The process
            # supervisor can inspect state before the event loop first schedules
            # _supervise_critical_task; leaving the initialized value False in
            # that window creates a false V2_CRITICAL_TASK_NOT_ALIVE hold.
            critical_job = self.status.critical_jobs[V2_CRITICAL_JOB]
            critical_job.task_alive = True
            self._tasks.append(
                asyncio.create_task(
                    self._supervise_critical_task(
                        name=V2_CRITICAL_JOB,
                        factory=lambda: self._run_periodic(
                            name=V2_CRITICAL_JOB,
                            interval_seconds=self.paper_cycle_seconds,
                            runner=_default_v2_automated_trading_runner,
                            records_auto_cycle=True,
                            run_immediately=(
                                self.status.recovery.entry_hold
                                or self.status.entry_authority != EntryAuthority.NONE.value
                                or self.status.pending_config_snapshot_id is not None
                            ),
                            coordinated=True,
                            align_to_interval=True,
                            interval_offset_seconds=self.paper_cycle_offset_seconds,
                        ),
                    )
                )
            )
        self._tasks.extend(
            [
                asyncio.create_task(
                    self._run_periodic(
                        name="market_data_heartbeat",
                        interval_seconds=self.heartbeat_seconds,
                        runner=self.heartbeat_runner,
                    )
                ),
                asyncio.create_task(
                    self._run_periodic(
                        name="exchange_info_refresh",
                        interval_seconds=max(self.heartbeat_seconds, 60.0),
                        runner=self.exchange_info_refresh_runner,
                    )
                ),
                asyncio.create_task(
                    self._run_periodic(
                        name="poll_news_feeds",
                        interval_seconds=self.news_poll_seconds,
                        runner=self.news_poll_runner,
                        affects_scheduler_health=False,
                    )
                ),
                asyncio.create_task(
                    self._run_periodic(
                        name="poll_macro_calendar",
                        interval_seconds=self.macro_poll_seconds,
                        runner=self.macro_poll_runner,
                        affects_scheduler_health=False,
                    )
                ),
                asyncio.create_task(
                    self._run_periodic(
                        name="poll_social_watchlist",
                        interval_seconds=self.social_poll_seconds,
                        runner=self.social_poll_runner,
                        affects_scheduler_health=False,
                    )
                ),
                asyncio.create_task(
                    self._run_periodic(
                        name="risk_profile_sweep",
                        interval_seconds=self.risk_sweep_seconds,
                        runner=self.risk_sweep_runner,
                    )
                ),
                asyncio.create_task(
                    self._run_periodic(
                        name="refresh_signal_edge_stats",
                        interval_seconds=self.edge_stats_refresh_seconds,
                        runner=self.edge_stats_refresh_runner,
                        affects_scheduler_health=False,
                        run_immediately=False,
                    )
                ),
                asyncio.create_task(
                    self._run_periodic(
                        name="refresh_volatility_asset_risk_tiers",
                        interval_seconds=self.volatility_risk_refresh_seconds,
                        runner=self.volatility_risk_refresh_runner,
                        affects_scheduler_health=False,
                        run_immediately=False,
                    )
                ),
                asyncio.create_task(
                    self._run_periodic(
                        name="notification_dispatch",
                        interval_seconds=self.notification_seconds,
                        runner=self.notification_runner,
                        affects_scheduler_health=False,
                    )
                ),
                asyncio.create_task(self._run_daily_review_loop()),
            ]
        )
        if settings.market_review_enabled and self.market_review_seconds > 0:
            self._tasks.append(
                asyncio.create_task(
                    self._run_periodic(
                        name="market_review",
                        interval_seconds=self.market_review_seconds,
                        runner=self.market_review_runner,
                        affects_scheduler_health=False,
                        run_immediately=True,
                    )
                )
            )
        if settings.binance_live_ws_enabled:
            self._tasks.extend(self._live_collector_tasks())

    async def stop(self) -> None:
        if not self.status.running:
            return
        if self._stop_event is not None:
            self._stop_event.set()
        if self._tasks:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*self._tasks, return_exceptions=True),
                    timeout=5.0,
                )
            except TimeoutError:
                for task in self._tasks:
                    task.cancel()
                await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []
        self.status.running = False
        self.status.next_cycle_eta_seconds = None
        self._publish_external_state()

    async def _run_periodic(
        self,
        *,
        name: str,
        interval_seconds: float,
        runner: Runner,
        records_auto_cycle: bool = False,
        affects_scheduler_health: bool = True,
        run_immediately: bool = True,
        coordinated: bool = False,
        align_to_interval: bool = False,
        interval_offset_seconds: float = 0.0,
    ) -> None:
        assert self._stop_event is not None
        critical = self.status.critical_jobs.get(name)
        if critical is not None:
            critical.task_alive = True
        try:
            if not run_immediately:
                initial_wait = (
                    _aligned_run_delay_seconds(
                        now=datetime.now(UTC),
                        interval_seconds=interval_seconds,
                        offset_seconds=interval_offset_seconds,
                    )
                    if align_to_interval
                    else max(interval_seconds, 0.01)
                )
                with suppress(TimeoutError):
                    await asyncio.wait_for(self._stop_event.wait(), timeout=initial_wait)
            while not self._stop_event.is_set():
                started = datetime.now(UTC)
                if critical is not None:
                    critical.last_started_at = started
                    critical.currently_running = True
                if records_auto_cycle:
                    self._next_cycle_at = started
                    self.status.next_cycle_eta_seconds = 0
                result = (
                    await self._run_coordinated_once(
                        name=name,
                        interval_seconds=interval_seconds,
                        runner=runner,
                        affects_scheduler_health=affects_scheduler_health,
                        observed_at=started,
                    )
                    if coordinated
                    else await self._run_once(
                        name=name,
                        runner=runner,
                        affects_scheduler_health=affects_scheduler_health,
                    )
                )
                if critical is not None:
                    critical.currently_running = False
                    critical.last_completed_at = datetime.now(UTC)
                    if isinstance(result, dict) and result.get("status") in {"error", "partial_failure"}:
                        critical.consecutive_failures += 1
                        critical.last_failure_at = critical.last_completed_at
                        critical.last_exception = str(result.get("error") or "cycle_partial_failure")
                    else:
                        critical.consecutive_failures = 0
                        critical.last_success_at = critical.last_completed_at
                        critical.last_exception = None
                    if isinstance(result, dict):
                        critical.current_cycle_id = (
                            str(result.get("scheduler_cycle_id")) if result.get("scheduler_cycle_id") else None
                        )
                    if name == V2_CRITICAL_JOB:
                        durable_recovery_hold = str(_runtime_entry_control_reason() or "").startswith(
                            LIVENESS_RECOVERY_HOLD_PREFIX
                        )
                        if durable_recovery_hold and not self.status.recovery.entry_hold:
                            # The process supervisor can persist a hold after
                            # this worker has started. Hydrate that durable
                            # safety state before deciding whether it may be
                            # cleared by an authoritative healthy cycle.
                            self.status.recovery.entry_hold = True
                            self.status.recovery.state = RECOVERY_ENTRY_HOLD
                        if self.status.recovery.entry_hold:
                            self.status.recovery.state = RECOVERY_MANAGEMENT
                            self._maybe_clear_recovery_hold(result)
                        elif isinstance(result, dict) and result.get("status") == "completed":
                            if self.status.recovery.state in {RECOVERY_VERIFYING, RECOVERY_RESTARTING}:
                                self.status.recovery.state = RECOVERY_HEALTHY
                                self.status.recovery.reason = None
                                self.status.recovery.attempt = 0
                non_execution_statuses = {"standby_not_leader", "duplicate_slot_skipped"}
                cycle_executed = not (isinstance(result, dict) and result.get("status") in non_execution_statuses)
                if records_auto_cycle and cycle_executed:
                    self.status.last_auto_cycle_at = datetime.now(UTC)
                    self._next_cycle_at = self.status.last_auto_cycle_at
                retry_after_seconds = float(result.get("retry_after_seconds", 0)) if isinstance(result, dict) else 0.0
                scheduled_wait = (
                    _aligned_run_delay_seconds(
                        now=datetime.now(UTC),
                        interval_seconds=interval_seconds,
                        offset_seconds=interval_offset_seconds,
                    )
                    if align_to_interval
                    else interval_seconds
                )
                wait_seconds = max(scheduled_wait, retry_after_seconds, 0.01)
                with suppress(TimeoutError):
                    await asyncio.wait_for(self._stop_event.wait(), timeout=wait_seconds)
                if records_auto_cycle and self._next_cycle_at is not None:
                    elapsed = (datetime.now(UTC) - self._next_cycle_at).total_seconds()
                    self.status.next_cycle_eta_seconds = max(0, int(interval_seconds - elapsed))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if critical is not None:
                critical.task_alive = False
                critical.currently_running = False
                critical.last_failure_at = datetime.now(UTC)
                critical.consecutive_failures += 1
                critical.last_exception = str(exc)
            raise
        finally:
            if critical is not None and self._stop_event.is_set():
                critical.task_alive = False
                critical.currently_running = False

    async def _supervise_critical_task(self, *, name: str, factory: Callable[[], Any]) -> None:
        """Keep the V2 periodic task alive without restarting through ambiguity."""
        assert self._stop_event is not None
        backoff_index = 0
        while not self._stop_event.is_set():
            current_task = asyncio.current_task()
            assert current_task is not None
            self._critical_tasks[name] = current_task
            job = self.status.critical_jobs.setdefault(name, CriticalJobLiveness())
            job.registered = True
            job.task_alive = True
            try:
                await factory()
                if self._stop_event.is_set():
                    return
                error = "critical task exited unexpectedly"
            except asyncio.CancelledError:
                if self._stop_event.is_set():
                    return
                raise
            except Exception as exc:  # noqa: BLE001
                error = str(exc)
            job.task_alive = False
            job.currently_running = False
            job.last_failure_at = datetime.now(UTC)
            job.consecutive_failures += 1
            job.last_exception = error
            self._scheduler_errors[name] = error
            self.status.scheduler_error = "; ".join(
                f"{task_name}: {message}" for task_name, message in sorted(self._scheduler_errors.items())
            )
            self.status.recovery.attempt += 1
            self.status.recovery.state = RECOVERY_CRASH_DETECTED
            self.status.recovery.reason = error
            self.status.recovery.last_recovery_at = datetime.now(UTC)
            if self.status.recovery.attempt >= MAX_INPROCESS_RECOVERY_ATTEMPTS:
                self._set_recovery_hold(f"{error}:AUTO_RECOVERY_EXHAUSTED")
                self.status.recovery.state = RECOVERY_EXHAUSTED
                self.status.recovery.reason = error
                self._publish_external_state()
                return
            self._publish_external_state()
            recovery = await asyncio.to_thread(self._safe_v2_recovery_check)
            management_only_recovery = False
            if not recovery.get("safe_to_restart"):
                reason = str(recovery.get("reason") or "v2_recovery_blocked")
                self._set_recovery_hold(reason)
                # Do not stop the scheduler: the next V2 cycle remains a
                # management/reconciliation-only pass until the hold clears.
                management_only_recovery = True
                self.status.recovery.state = RECOVERY_MANAGEMENT
                self.status.recovery.reason = reason
                self._publish_external_state()
            else:
                self.status.recovery.state = RECOVERY_RESTARTING
            delay = self._critical_task_backoffs[min(backoff_index, len(self._critical_task_backoffs) - 1)]
            backoff_index += 1
            with suppress(TimeoutError):
                await asyncio.wait_for(self._stop_event.wait(), timeout=delay)
            # A normal stop can wake the backoff wait. Preserve the recovery
            # state for shutdown/inspection instead of reporting verification
            # that never actually ran.
            if not self._stop_event.is_set() and not management_only_recovery:
                self.status.recovery.state = RECOVERY_VERIFYING

    def _safe_v2_recovery_check(self) -> dict[str, Any]:
        """Fail closed on known ambiguous V2 outcomes before a task restart."""
        last = self.status.last_results.get(V2_CRITICAL_JOB)
        if not isinstance(last, dict):
            result = {"safe_to_restart": True, "reason": "no_completed_cycle"}
        else:
            result = {"safe_to_restart": True, "reason": "reconciliation_preflight_clean"}
            for item in last.get("results", []):
                if not isinstance(item, dict):
                    continue
                reconciliation = str(item.get("reconciliation_status") or "").upper()
                if reconciliation not in {"", "HEALTHY"}:
                    return {"safe_to_restart": False, "reason": f"reconciliation_{reconciliation}"}
                error_text = str(item.get("error") or "")
                if "UNKNOWN" in error_text.upper():
                    return {"safe_to_restart": False, "reason": "EXCHANGE_UNKNOWN"}

        # The live scheduler has a coordinator and therefore a database-backed
        # preflight.  Unit tests without a coordinator retain the deterministic
        # in-memory check above.
        if self.coordinator is None:
            return result
        try:
            from sqlalchemy import select

            from services.automated_trading.infrastructure.models import (
                V2ExecutionIntent,
                V2ReconciliationSnapshot,
            )
            from services.database import get_session_factory

            with get_session_factory()() as session:
                unresolved = session.scalars(
                    select(V2ExecutionIntent.state).where(
                        V2ExecutionIntent.execution_mode == "BINANCE_TESTNET",
                        V2ExecutionIntent.state.in_(
                            ("INTENT_CREATED", "EXCHANGE_SUBMITTING", "EXCHANGE_UNKNOWN", "EXCHANGE_ACKNOWLEDGED")
                        ),
                    )
                ).all()
                if unresolved:
                    return {"safe_to_restart": False, "reason": "ACTIVE_EXECUTION_INTENT_REQUIRES_RECONCILIATION"}
                latest_reconciliation = session.scalars(
                    select(V2ReconciliationSnapshot)
                    .where(V2ReconciliationSnapshot.execution_mode == "BINANCE_TESTNET")
                    .order_by(V2ReconciliationSnapshot.captured_at.desc())
                    .limit(1)
                ).first()
                if latest_reconciliation is not None and latest_reconciliation.status != "HEALTHY":
                    return {
                        "safe_to_restart": False,
                        "reason": f"reconciliation_{latest_reconciliation.status}",
                    }
        except Exception as exc:  # noqa: BLE001
            return {"safe_to_restart": False, "reason": f"recovery_preflight_unavailable:{exc}"}
        return result

    async def _run_coordinated_once(
        self,
        *,
        name: str,
        interval_seconds: float,
        runner: Runner,
        affects_scheduler_health: bool,
        observed_at: datetime,
    ) -> Any:
        assert self.coordinator is not None
        # V2 Testnet cycles must use one exchange-clock reference for the
        # outer claim and the inner decision cycle.  Using the local clock
        # here while v2_scheduler_entry.py reads Binance time creates two
        # different slots and can strand the scheduler behind stale claims.
        if name == "automated_trading_v2_cycle" and settings.binance_use_testnet:
            try:
                observed_at = await asyncio.to_thread(fetch_binance_server_time)
            except BinanceClockUnavailable as exc:
                result = {
                    "status": "error",
                    "error": "BINANCE_SERVER_TIME_UNAVAILABLE",
                    "detail": str(exc),
                }
                self.status.failure_counts[name] = self.status.failure_counts.get(name, 0) + 1
                self.status.last_results[name] = result
                self.status.last_failure_at[name] = datetime.now(UTC)
                self._scheduler_errors[name] = result["error"]
                self.status.scheduler_error = "; ".join(
                    f"{task_name}: {error}" for task_name, error in sorted(self._scheduler_errors.items())
                )
                self._publish_external_state()
                return result
        lease_ttl = max(90.0, interval_seconds * 3)
        if not self.coordinator.acquire_or_renew_lease(
            lease_name=name,
            now=observed_at,
            ttl_seconds=lease_ttl,
        ):
            self.status.current_lock_owner = None
            result = {"status": "standby_not_leader"}
            self.status.last_results[name] = result
            self._publish_external_state()
            return result
        self.status.current_lock_owner = self.scheduler_instance_id
        fencing_token = self.coordinator.fencing_token(lease_name=name)
        if fencing_token is None:
            result = {"status": "lease_lost/fenced"}
            self.status.last_results[name] = result
            self._publish_external_state()
            return result
        scheduled_for = _slot_start(observed_at, interval_seconds)
        self.status.last_scheduled_for = scheduled_for
        claim = self.coordinator.claim_cycle(
            job_name=name,
            scheduled_for=scheduled_for,
            fencing_token=fencing_token,
        )
        if not claim.claimed or claim.scheduler_cycle_id is None:
            self.coordinator.release_lease(lease_name=name)
            self.status.current_lock_owner = None
            result = {"status": "duplicate_slot_skipped", "scheduled_for": scheduled_for.isoformat()}
            self.status.last_results[name] = result
            self._publish_external_state()
            return result

        metadata = {
            "scheduled_for": scheduled_for.isoformat(),
            "scheduler_instance_id": self.scheduler_instance_id,
            "cycle_source": "runtime_scheduler",
            "run_mode": "paper",
            "deployment_sha": settings.app_build_id,
            "process_id": os.getpid(),
            "worker_id": os.getenv("WORKER_ID"),
            "container_id": os.getenv("CONTAINER_ID") or socket.gethostname(),
            "scheduler_cycle_id": claim.scheduler_cycle_id,
            "lease_name": name,
            "fencing_token": fencing_token,
            "observed_at": observed_at.astimezone(UTC).isoformat(),
        }
        # Runner 类型是 Callable[[], Any]；默认 runner 额外接受 provenance，直接绑定 metadata。
        if runner is _default_paper_cycle_runner:

            def _paper_with_context() -> Any:
                return _default_paper_cycle_runner(metadata)

            runner_with_context: Runner = _paper_with_context
        elif runner is _default_v2_automated_trading_runner:

            def _v2_with_context() -> Any:
                return _default_v2_automated_trading_runner(metadata)

            runner_with_context = _v2_with_context
        elif runner is _default_observation_cycle_runner:

            def _observation_with_context() -> Any:
                return _default_observation_cycle_runner(metadata)

            runner_with_context = _observation_with_context
        else:
            runner_with_context = runner
        failure_count = self.status.failure_counts.get(name, 0)
        lease_done = asyncio.Event()
        renewal_task = asyncio.create_task(
            self._renew_lease_until_done(
                lease_name=name,
                lease_ttl=lease_ttl,
                fencing_token=fencing_token,
                done=lease_done,
            )
        )
        run_task = asyncio.create_task(
            self._run_once(
                name=name,
                runner=runner_with_context,
                affects_scheduler_health=affects_scheduler_health,
            )
        )
        cancelled = False
        try:
            result = await asyncio.shield(run_task)
        except asyncio.CancelledError:
            cancelled = True
            result = await asyncio.shield(run_task)
        lease_done.set()
        await renewal_task
        failed = self.status.failure_counts.get(name, 0) > failure_count
        self.coordinator.finish_cycle(
            claim.scheduler_cycle_id,
            status="failed" if failed else "completed",
            failure_reason=str(result.get("error")) if failed and isinstance(result, dict) else None,
        )
        self.coordinator.release_lease(lease_name=name)
        self.status.current_lock_owner = None
        if cancelled:
            raise asyncio.CancelledError
        return result

    async def _renew_lease_until_done(
        self,
        *,
        lease_name: str,
        lease_ttl: float,
        fencing_token: int,
        done: asyncio.Event,
    ) -> None:
        assert self.coordinator is not None
        heartbeat_seconds = max(1.0, min(30.0, lease_ttl / 3))
        while not done.is_set():
            try:
                await asyncio.wait_for(done.wait(), timeout=heartbeat_seconds)
            except TimeoutError:
                renewed = await asyncio.to_thread(
                    self.coordinator.acquire_or_renew_lease,
                    lease_name=lease_name,
                    ttl_seconds=lease_ttl,
                    fencing_token=fencing_token,
                )
                if not renewed:
                    self._scheduler_errors[lease_name] = "scheduler leadership lease was lost"
                    return

    async def _run_once(
        self,
        *,
        name: str,
        runner: Runner,
        affects_scheduler_health: bool = True,
    ) -> Any:
        try:
            result = await asyncio.to_thread(runner)
            self.status.run_counts[name] = self.status.run_counts.get(name, 0) + 1
            self.status.last_results[name] = result
            self.status.last_success_at[name] = datetime.now(UTC)
            if affects_scheduler_health:
                self._scheduler_errors.pop(name, None)
        except Exception as exc:  # pragma: no cover - defensive runtime guard
            self.status.failure_counts[name] = self.status.failure_counts.get(name, 0) + 1
            self.status.last_results[name] = {"status": "error", "error": str(exc)}
            self.status.last_failure_at[name] = datetime.now(UTC)
            if affects_scheduler_health:
                self._scheduler_errors[name] = str(exc)
        self.status.scheduler_error = (
            "; ".join(f"{task_name}: {error}" for task_name, error in sorted(self._scheduler_errors.items())) or None
        )
        if name == "exchange_info_refresh" and isinstance(self.status.last_results[name], dict):
            self._refresh_snapshot_lifecycle_from_store()
        if name == "automated_trading_v2_cycle" and isinstance(self.status.last_results[name], dict):
            self._refresh_entry_authority_from_v2_cycle(self.status.last_results[name])
        self._publish_external_state()
        return self.status.last_results[name]

    def _refresh_entry_authority_from_v2_cycle(self, cycle_result: dict[str, Any]) -> None:
        """Publish the authority actually resolved by the just-completed V2 cycle."""
        all_results = [item for item in cycle_result.get("results", []) if isinstance(item, dict)]
        completed_symbols = {
            str(item.get("symbol")) for item in all_results if item.get("status") == "completed" and item.get("symbol")
        }
        if cycle_result.get("status") != "completed" or completed_symbols != set(AUTO_SIMULATION_EXECUTION_SYMBOLS):
            error_codes = {
                str(item.get("error_code"))
                for item in all_results
                if item.get("status") == "error" and item.get("error_code")
            }
            failure_reason = error_codes.pop() if len(error_codes) == 1 else V2_CYCLE_INCOMPLETE
            self.status.reconciliation_healthy = False
            self.status.entry_authorized = False
            self.status.scheduler_error = failure_reason
            if failure_reason == ACTIVE_CONFIG_SNAPSHOT_CAPTURE_FAILED:
                self.status.active_snapshot_valid = False
                self.status.forward_authorized = False
                self.status.forward_authorization_reason = failure_reason
            self.status.trading_state = "DEGRADED"
            return
        resolved = [item for item in all_results if isinstance(item, dict) and item.get("status") == "completed"]
        if not resolved:
            return
        reconciliation = [
            str(item.get("reconciliation_status") or "").upper()
            for item in resolved
            if item.get("reconciliation_status") is not None
        ]
        if reconciliation:
            self.status.reconciliation_healthy = all(value == "HEALTHY" for value in reconciliation)
        authorities = {str(item.get("entry_authority") or "NONE") for item in resolved}
        if len(authorities) != 1:
            self.status.entry_authority = EntryAuthority.NONE.value
            self.status.entry_authorized = False
            self.status.entry_authority_reason = ENTRY_AUTHORITY_SCOPE_INCONSISTENT
            self.status.active_entry_strategy = None
            self.status.promotion_eligible = False
            self.status.active_snapshot_valid = False
            self.status.production_authorized = False
            self.status.forward_authorized = False
            self.status.scheduler_error = ENTRY_AUTHORITY_SCOPE_INCONSISTENT
            self.status.trading_state = "DEGRADED"
            return
        authority = authorities.pop()
        first = resolved[0]
        self.status.entry_authority = authority
        self.status.entry_authorized = bool(first.get("entry_authorized"))
        self.status.entry_authority_reason = str(first.get("entry_authority_reason") or "unknown")
        self.status.production_authorization_state = str(
            first.get("production_authorization_state") or self.status.production_authorization_state
        )
        self.status.active_entry_strategy = first.get("active_entry_strategy")
        self.status.promotion_eligible = bool(first.get("promotion_eligible"))
        snapshot_ids = {
            str(item.get("active_config_snapshot_id")) for item in resolved if item.get("active_config_snapshot_id")
        }
        snapshot_hashes = {str(item.get("active_config_hash")) for item in resolved if item.get("active_config_hash")}
        active_strategies = {
            str(item.get("active_entry_strategy")) for item in resolved if item.get("active_entry_strategy")
        }
        authorized_authority = authority in {EntryAuthority.TESTNET_FORWARD.value, EntryAuthority.PRODUCTION.value}
        complete_snapshot_binding = (
            len(snapshot_ids) == 1
            and len(snapshot_hashes) == 1
            and all(item.get("active_config_snapshot_id") and item.get("active_config_hash") for item in resolved)
        )
        complete_strategy_binding = len(active_strategies) == 1 and all(
            item.get("active_entry_strategy") for item in resolved
        )
        snapshot_bindings = {
            (item.get("active_config_snapshot_id"), item.get("active_config_hash")) for item in resolved
        }
        if len(snapshot_bindings) != 1:
            self.status.active_snapshot_valid = False
            self.status.forward_authorized = False
            self.status.entry_authorized = False
            self.status.active_config_snapshot_id = None
            self.status.active_config_hash = None
            self.status.entry_authority_reason = ACTIVE_CONFIG_SNAPSHOT_SCOPE_INCONSISTENT
            self.status.forward_authorization_reason = ACTIVE_CONFIG_SNAPSHOT_SCOPE_INCONSISTENT
            self.status.scheduler_error = ACTIVE_CONFIG_SNAPSHOT_SCOPE_INCONSISTENT
            self.status.trading_state = "DEGRADED"
            return
        if authorized_authority and (not complete_snapshot_binding or not complete_strategy_binding):
            self.status.active_snapshot_valid = False
            self.status.forward_authorized = False
            self.status.entry_authorized = False
            self.status.active_config_snapshot_id = None
            self.status.active_config_hash = None
            self.status.entry_authority_reason = ACTIVE_CONFIG_SNAPSHOT_SCOPE_INCONSISTENT
            self.status.forward_authorization_reason = ACTIVE_CONFIG_SNAPSHOT_SCOPE_INCONSISTENT
            self.status.scheduler_error = ACTIVE_CONFIG_SNAPSHOT_SCOPE_INCONSISTENT
            self.status.trading_state = "DEGRADED"
            return
        if complete_snapshot_binding:
            active_snapshot_id = snapshot_ids.pop()
            active_snapshot_hash = snapshot_hashes.pop()
            self.status.active_config_snapshot_id = active_snapshot_id
            self.status.active_config_hash = active_snapshot_hash
            self.status.active_snapshot_valid = True
            if self.status.pending_config_snapshot_id == active_snapshot_id:
                self.status.pending_config_snapshot_id = None
                self.status.pending_config_hash = None
        else:
            # A data-preparation cycle may have no snapshot payload even while
            # the durable Active ConfigSnapshot remains valid.  Preserve that
            # source-of-truth fact for an unauthorized runtime; only clear
            # genuinely stale facts when the store has no active snapshot.
            if not self._refresh_snapshot_lifecycle_from_store():
                self.status.active_config_snapshot_id = None
                self.status.active_config_hash = None
                self.status.active_snapshot_valid = False
        self.status.production_authorized = authority == EntryAuthority.PRODUCTION.value
        self.status.forward_authorized = authority == EntryAuthority.TESTNET_FORWARD.value
        if self.status.forward_authorized:
            self.status.forward_authorization_reason = "TESTNET_FORWARD_AUTHORIZED"
        # RuntimeControl remains the only new-entry switch.  Refreshing the
        # authority/snapshot facts above is required during a recovery cycle,
        # but those facts must not turn a paused scheduler back into a writer.
        if self.status.entry_enabled is False:
            self.status.entry_authorized = False
            self.status.trading_state = "DEGRADED" if self.status.reconciliation_healthy is False else "MANAGEMENT_ONLY"
            return
        if self.status.reconciliation_healthy is False:
            self.status.entry_authorized = False
            self.status.trading_state = "DEGRADED"
        elif authority == EntryAuthority.NONE.value:
            self.status.entry_authorized = False
            self.status.trading_state = "ENTRY_BLOCKED"
        else:
            cycle_trading_state = str(first.get("trading_state") or "ENTRY_BLOCKED")
            self.status.trading_state = (
                cycle_trading_state
                if cycle_trading_state in {"TRADING_READY", "MANAGEMENT_ONLY", "ENTRY_BLOCKED", "DEGRADED"}
                else "DEGRADED"
            )

    def _publish_external_state(self) -> None:
        """Persist scheduler health for the separately hosted desktop API."""
        heartbeat = self.status.last_results.get("market_data_heartbeat", {})
        checked_symbols = heartbeat.get("checked_symbols", []) if isinstance(heartbeat, dict) else []
        stale_symbols = heartbeat.get("stale_symbols", []) if isinstance(heartbeat, dict) else []
        # Execution scope is a fixed runtime contract, not an observation
        # derived from the first asynchronous market heartbeat.  Publishing an
        # empty scope while that heartbeat is still pending makes the launcher
        # reject a healthy ACTIVE scheduler during its startup window.
        execution_symbols = list(AUTO_SIMULATION_EXECUTION_SYMBOLS)
        execution_scope_observed = set(AUTO_SIMULATION_EXECUTION_SYMBOLS).issubset(checked_symbols)
        execution_stale_symbols = set(stale_symbols) & set(AUTO_SIMULATION_EXECUTION_SYMBOLS)
        exchange_info = self.status.last_results.get("exchange_info_refresh", {})
        write_external_scheduler_state(
            {
                "running": self.status.running,
                "supervisor_pid": int(os.getenv("V2_SUPERVISOR_PID", "0") or 0),
                "worker_pid": os.getpid(),
                "heartbeat_at": datetime.now(UTC).isoformat(),
                "started_at": self.status.started_at.isoformat() if self.status.started_at else None,
                "top20_coverage_count": len(checked_symbols),
                "execution_coverage_count": len(execution_symbols),
                "execution_symbols": execution_symbols,
                "exchange_info_ready": bool(exchange_info.get("ready")) if isinstance(exchange_info, dict) else False,
                "data_fresh": execution_scope_observed and not execution_stale_symbols,
                "last_auto_cycle_at": self.status.last_auto_cycle_at.isoformat()
                if self.status.last_auto_cycle_at
                else None,
                "scheduler_error": self.status.scheduler_error,
                "task_run_counts": self.status.run_counts,
                "task_failure_counts": self.status.failure_counts,
                "task_last_results": self.status.last_results,
                "scheduler_instance_id": self.scheduler_instance_id,
                "launch_instance_id": os.getenv("V2_LAUNCH_INSTANCE_ID") or None,
                "current_lock_owner": self.status.current_lock_owner,
                "last_scheduled_for": self.status.last_scheduled_for.isoformat()
                if self.status.last_scheduled_for
                else None,
                "engine_activation": self.status.engine_activation,
                "execution_mode": self.status.execution_mode,
                "execution_strategy_id": self.status.execution_strategy_id,
                "registered_jobs": list(self.status.registered_jobs),
                "legacy_writer_enabled": self.status.legacy_writer_enabled,
                "entry_enabled": self.status.entry_enabled,
                "sampling_fallback_enabled": self.status.sampling_fallback_enabled,
                "external_baseline_captured": self.status.external_baseline_captured,
                "external_baseline_value": self.status.external_baseline_value,
                "external_baseline_source": self.status.external_baseline_source,
                "external_baseline_lifecycle": self.status.external_baseline_lifecycle,
                "external_baseline_drift_keys": list(self.status.external_baseline_drift_keys),
                "entry_authorized": self.status.entry_authorized,
                "entry_authority": self.status.entry_authority,
                "entry_authority_reason": self.status.entry_authority_reason,
                "production_authorization_state": self.status.production_authorization_state,
                "active_entry_strategy": self.status.active_entry_strategy,
                "promotion_eligible": self.status.promotion_eligible,
                "trading_state": self.status.trading_state,
                "startup_contract_errors": list(self.status.startup_contract_errors),
                "natural_testnet_enabled": self.status.natural_testnet_enabled,
                "active_config_snapshot_id": self.status.active_config_snapshot_id,
                "active_config_hash": self.status.active_config_hash,
                "pending_config_snapshot_id": self.status.pending_config_snapshot_id,
                "pending_config_hash": self.status.pending_config_hash,
                "active_snapshot_valid": self.status.active_snapshot_valid,
                "production_authorized": self.status.production_authorized,
                "production_authorization_reason": self.status.production_authorization_reason,
                "forward_authorized": self.status.forward_authorized,
                "forward_authorization_reason": self.status.forward_authorization_reason,
                "entry_control_reason": self.status.entry_control_reason,
                "reconciliation_healthy": self.status.reconciliation_healthy,
                "critical_jobs": {
                    name: {
                        "registered": job.registered,
                        "task_alive": job.task_alive,
                        "last_started_at": job.last_started_at,
                        "last_completed_at": job.last_completed_at,
                        "last_success_at": job.last_success_at,
                        "last_failure_at": job.last_failure_at,
                        "consecutive_failures": job.consecutive_failures,
                        "last_exception": job.last_exception,
                        "currently_running": job.currently_running,
                        "current_cycle_id": job.current_cycle_id,
                    }
                    for name, job in self.status.critical_jobs.items()
                },
                "recovery": {
                    "state": self.status.recovery.state,
                    "reason": self.status.recovery.reason,
                    "attempt": self.status.recovery.attempt,
                    "entry_hold": self.status.recovery.entry_hold,
                    "worker_restart_count": self.status.recovery.worker_restart_count,
                    "last_recovery_at": self.status.recovery.last_recovery_at,
                },
            }
        )

    async def _run_daily_review_loop(self) -> None:
        assert self._stop_event is not None
        while not self._stop_event.is_set():
            now = datetime.now(UTC)
            due = now.hour > settings.daily_review_hour_utc or (
                now.hour == settings.daily_review_hour_utc and now.minute >= settings.daily_review_minute_utc
            )
            if due and self._last_daily_review_date != now.date():
                await self._run_once(name="daily_review", runner=lambda: self.daily_review_runner(None))
                self._last_daily_review_date = now.date()
            with suppress(TimeoutError):
                await asyncio.wait_for(self._stop_event.wait(), timeout=max(self.daily_review_check_seconds, 0.01))

    def _live_collector_tasks(self) -> list[asyncio.Task]:
        from services.data.service import resolve_binance_live_ws_symbols

        symbols = resolve_binance_live_ws_symbols()
        return [
            asyncio.create_task(self._run_live_collector(symbol=symbol, timeframe=settings.binance_live_ws_timeframe))
            for symbol in symbols
        ]

    async def _run_live_collector(self, *, symbol: str, timeframe: str) -> None:
        from services.data import DataRepository, live_feed_bus
        from services.data.binance import (
            BinanceLiveMarketCollector,
            run_live_collector_forever,
            spot_to_usdm_perp_symbol,
        )
        from services.database import get_session_factory

        def collector_factory() -> BinanceLiveMarketCollector:
            session = get_session_factory()()

            async def publish(bar) -> None:
                await live_feed_bus.publish_candle(bar)

            return BinanceLiveMarketCollector(
                data_repo=DataRepository(session),
                on_candle=publish,
                on_close=session.close,
            )

        async def report_reconnect_error(exc: Exception) -> None:
            await live_feed_bus.set_error(symbol=symbol, timeframe=timeframe, error=str(exc))

        await run_live_collector_forever(
            collector_factory=collector_factory,
            symbol=symbol,
            perp_symbol=spot_to_usdm_perp_symbol(symbol),
            timeframe=timeframe,
            reconnect_error_handler=report_reconnect_error,
        )


_runtime_scheduler: RuntimeScheduler | None = None


def get_runtime_scheduler() -> RuntimeScheduler | None:
    return _runtime_scheduler


def set_runtime_scheduler(scheduler: RuntimeScheduler | None) -> None:
    global _runtime_scheduler
    _runtime_scheduler = scheduler


def runtime_scheduler_status() -> RuntimeSchedulerStatus:
    if _runtime_scheduler is None:
        return RuntimeSchedulerStatus(mode=settings.runtime_scheduler_mode, running=False)
    return _runtime_scheduler.status


def _resolve_runtime_scheduler_jobs(v2_activation_config) -> frozenset[str]:
    """Register legacy paper writers and/or V2 automated_trading cycles by activation.

    Engine modes from settings.automated_trading_engine:
    - legacy: paper writers only
    - v2_shadow: legacy writers + AutomatedTrading V2 shadow cycle
    - v2_active: AutomatedTrading V2 active cycle only (no legacy exchange writer)
    """
    from services.execution.v2_scheduler_entry import resolve_scheduler_v2_jobs

    return resolve_scheduler_v2_jobs(v2_activation_config)


def _default_v2_automated_trading_runner(provenance: dict[str, Any] | None = None) -> dict:
    from services.execution.tasks import run_v2_automated_trading_cycles

    payload = {
        "timeframe": "15m",
        "scheduler_instance_id": (provenance or {}).get("scheduler_instance_id"),
        **(provenance or {}),
    }
    return run_v2_automated_trading_cycles.run(payload)


def _default_paper_cycle_runner(provenance: dict[str, Any] | None = None) -> dict:
    from services.data.universe import AUTO_PAPER_RESEARCH_SYMBOLS
    from services.execution.tasks import run_all_paper_runtime_cycles

    return run_all_paper_runtime_cycles.run(
        {
            "timeframe": "1m",
            "max_symbols": len(AUTO_PAPER_RESEARCH_SYMBOLS),
            "enable_decision_veto": settings.paper_runtime_enable_decision_veto,
            **(provenance or {}),
        }
    )


def _default_observation_cycle_runner(provenance: dict[str, Any] | None = None) -> dict:
    from services.data.universe import AUTO_PAPER_RESEARCH_SYMBOLS
    from services.execution.tasks import run_observation_paper_runtime_cycles

    return run_observation_paper_runtime_cycles.run(
        {
            "timeframe": "1m",
            "max_symbols": len(AUTO_PAPER_RESEARCH_SYMBOLS),
            "enable_decision_veto": settings.paper_runtime_enable_decision_veto,
            **(provenance or {}),
        }
    )


def _default_market_review_runner(provenance: dict[str, Any] | None = None) -> dict:
    from services.execution.tasks import run_market_review

    del provenance
    return run_market_review.run()


def _slot_start(observed_at: datetime, interval_seconds: float) -> datetime:
    """Normalize all scheduler instances onto the same UTC execution slot."""
    timestamp = observed_at.astimezone(UTC).timestamp()
    slot = int(timestamp / interval_seconds) * interval_seconds
    return datetime.fromtimestamp(slot, tz=UTC)


def _default_heartbeat_runner() -> dict:
    from services.data.tasks import market_data_heartbeat
    from services.data.universe import AUTO_SIMULATION_EXECUTION_SYMBOLS

    return market_data_heartbeat.run(list(AUTO_SIMULATION_EXECUTION_SYMBOLS), "1m")


def _default_exchange_info_refresh_runner() -> dict:
    from services.data.binance import fetch_usdm_exchange_info_symbols, resolve_usdm_public_rest_base
    from services.data.universe import fixed_top20_assets
    from services.execution.bootstrap import refresh_fixed_top20_runtime_universe

    base_url = resolve_usdm_public_rest_base()
    symbols = fetch_usdm_exchange_info_symbols()
    assets = fixed_top20_assets(symbols)
    ready = bool(assets) and all(
        asset.tradable_status == "trading" and asset.precision and asset.min_notional is not None for asset in assets
    )
    updated_runs = refresh_fixed_top20_runtime_universe(symbols) if ready else 0
    return {
        "ready": ready,
        "base_url": base_url,
        "symbol_count": len(symbols),
        "updated_runs": updated_runs,
        "assets": [asset.model_dump(mode="json") for asset in assets],
    }


def _default_news_poll_runner() -> dict:
    from services.data.tasks import poll_news_feeds

    return poll_news_feeds.run()


def _default_macro_poll_runner() -> dict:
    from services.data.tasks import poll_macro_calendar

    return poll_macro_calendar.run()


def _default_social_poll_runner() -> dict:
    from services.data.tasks import poll_social_watchlist

    return poll_social_watchlist.run()


def _default_risk_sweep_runner() -> dict:
    from services.execution.tasks import risk_profile_sweep

    return risk_profile_sweep.run()


def _default_edge_stats_refresh_runner() -> dict:
    """Keep the local scheduler aligned with Celery's weekly edge-stat refresh."""
    from services.execution.tasks import refresh_signal_edge_stats

    return refresh_signal_edge_stats.run()


def _default_volatility_risk_refresh_runner() -> dict:
    """Refresh volatility tiers using the existing task without touching trading cycles."""
    from services.execution.tasks import refresh_volatility_asset_risk_tiers

    return refresh_volatility_asset_risk_tiers.run(lookback_days=30)


def _default_notification_runner() -> dict:
    from services.notifications_tasks import dispatch_notification_outbox

    return dispatch_notification_outbox.run()


def _default_daily_review_runner(report_date: str | None = None) -> dict:
    from services.review.tasks import generate_daily_review

    return generate_daily_review.run(report_date)
