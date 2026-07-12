"""Shared health state for the desktop API and its external scheduler."""

from __future__ import annotations

import json
import os
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
    exchange_info_ready: bool = False
    data_fresh: bool = False
    last_auto_cycle_at: datetime | None = None
    scheduler_error: str | None = None
    reason: str | None = "scheduler_state_missing"
    task_run_counts: dict[str, int] = field(default_factory=dict)
    task_failure_counts: dict[str, int] = field(default_factory=dict)
    task_last_results: dict[str, Any] = field(default_factory=dict)


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

    return ExternalSchedulerState(
        running=bool(raw.get("running")),
        heartbeat_at=heartbeat_at,
        top20_coverage_count=int(raw.get("top20_coverage_count", 0)),
        exchange_info_ready=bool(raw.get("exchange_info_ready")),
        data_fresh=bool(raw.get("data_fresh")),
        last_auto_cycle_at=_parse_datetime(raw.get("last_auto_cycle_at")),
        scheduler_error=raw.get("scheduler_error") if isinstance(raw.get("scheduler_error"), str) else None,
        reason=raw.get("reason") if isinstance(raw.get("reason"), str) else None,
        task_run_counts=dict(raw.get("task_run_counts") or {}),
        task_failure_counts=dict(raw.get("task_failure_counts") or {}),
        task_last_results=dict(raw.get("task_last_results") or {}),
    )
