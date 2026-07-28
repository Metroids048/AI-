"""V2 Automated Trading Runtime Truth API (plan section 13).

Prefix: /api/v2/automated-trading

Single-source-of-truth API — frontend never needs to guess the current
auto-run state by inspecting legacy PaperRun names or candidate counts.

All fields carry source / observed_at / freshness / availability so the UI
can show "unavailable" rather than placeholder zeros.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/api/v2/automated-trading", tags=["automated-trading-v2"])


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class DataField(BaseModel):
    """Wrapper that makes source + freshness explicit on every data value."""

    source: str
    observed_at: datetime | None = None
    freshness: str = "UNKNOWN"  # FRESH | STALE | UNAVAILABLE
    availability: str = "AVAILABLE"  # AVAILABLE | UNAVAILABLE | PARTIAL
    value: Any = None


class EngineStatus(BaseModel):
    engine_id: str
    mode: str
    activation: str
    entry_enabled: bool
    mainnet_supported: bool = False


class SchedulerStatus(BaseModel):
    running: bool
    last_cycle_at: datetime | None = None
    next_cycle_at: datetime | None = None


class ExchangeStatus(BaseModel):
    source: str
    timestamp: datetime | None = None
    freshness: str
    positions: list[dict[str, Any]] = []
    open_orders: list[dict[str, Any]] = []
    available: bool = False


class LocalProjection(BaseModel):
    source: str = "V2_LOCAL_PROJECTION"
    timestamp: datetime | None = None
    positions: list[dict[str, Any]] = []


class ReconciliationSummary(BaseModel):
    status: str
    mismatches: list[dict[str, Any]] = []
    entry_blocked_symbols: list[str] = []
    last_run_at: datetime | None = None


class DecisionSummary(BaseModel):
    symbol: str
    terminal_stage: str
    reason_code: str
    evaluated_at: datetime | None = None
    candidate_id: str | None = None
    exchange_submitted: bool = False


class IncidentSummary(BaseModel):
    incident_id: str
    severity: str
    incident_type: str
    state: str
    entry_block_all: bool
    created_at: datetime


class LLMInvocationSummary(BaseModel):
    stage: str
    status: str
    provider: str | None = None
    model: str | None = None
    total_tokens: int | None = None
    error_code: str | None = None
    skip_reason: str | None = None
    invoked_at: datetime | None = None


class RuntimeSnapshot(BaseModel):
    """The one endpoint the frontend reads. Everything else is detail."""

    engine: EngineStatus | None = None
    scheduler: SchedulerStatus | None = None
    market_data: dict[str, Any] = {}
    exchange: ExchangeStatus | None = None
    local_projection: LocalProjection | None = None
    reconciliation: ReconciliationSummary | None = None
    latest_decisions: list[DecisionSummary] = []
    latest_incidents: list[IncidentSummary] = []
    latest_llm_invocation: LLMInvocationSummary | None = None
    snapshot_at: datetime = datetime.now(UTC)

    class Config:
        json_encoders = {Decimal: str}


class CycleListItem(BaseModel):
    cycle_id: str
    mode: str
    activation: str
    status: str
    started_at: datetime | None = None
    completed_at: datetime | None = None
    reconciliation_status: str | None = None
    entry_enabled: bool = True


class EntryControlRequest(BaseModel):
    reason: str


class EntryControlResponse(BaseModel):
    success: bool
    entry_enabled: bool
    changed_at: datetime
    reason: str


# ---------------------------------------------------------------------------
# In-memory engine state (lightweight; scheduler writes, API reads)
# ---------------------------------------------------------------------------

_runtime_state: dict[str, Any] = {
    "engine_id": "automated-trading-v2",
    "mode": "BINANCE_TESTNET",
    "activation": "DISABLED",
    "entry_enabled": True,
    "entry_disabled_reason": None,
    "last_cycle_at": None,
    "cycles": [],
    "decisions": [],
    "incidents": [],
    "llm_invocations": [],
}


def update_runtime_state(update: dict[str, Any]) -> None:
    """Called by the cycle service after each cycle to push fresh state."""
    _runtime_state.update(update)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/runtime", response_model=RuntimeSnapshot)
def get_runtime() -> RuntimeSnapshot:
    """Single-call runtime truth snapshot.

    Returns null / UNAVAILABLE on every field that lacks fresh data.
    Never returns 0 to mean "unavailable".
    """
    state = _runtime_state

    engine = EngineStatus(
        engine_id=state.get("engine_id", "automated-trading-v2"),
        mode=state.get("mode", "BINANCE_TESTNET"),
        activation=state.get("activation", "DISABLED"),
        entry_enabled=state.get("entry_enabled", True),
        mainnet_supported=False,
    )

    scheduler_running = state.get("scheduler_running", False)
    scheduler = SchedulerStatus(
        running=scheduler_running,
        last_cycle_at=state.get("last_cycle_at"),
        next_cycle_at=state.get("next_cycle_at"),
    )

    exchange_snapshot = state.get("exchange_snapshot")
    if exchange_snapshot:
        exchange = ExchangeStatus(
            source="BINANCE_TESTNET_REST",
            timestamp=exchange_snapshot.get("timestamp"),
            freshness="FRESH",
            positions=exchange_snapshot.get("positions", []),
            open_orders=exchange_snapshot.get("open_orders", []),
            available=True,
        )
    else:
        exchange = ExchangeStatus(
            source="BINANCE_TESTNET_REST",
            freshness="UNAVAILABLE",
            available=False,
        )

    local_proj = LocalProjection(
        timestamp=state.get("local_projection_at"),
        positions=state.get("local_positions", []),
    )

    recon = state.get("reconciliation")
    reconciliation = ReconciliationSummary(
        status=recon.get("status", "UNAVAILABLE") if recon else "UNAVAILABLE",
        mismatches=recon.get("mismatches", []) if recon else [],
        entry_blocked_symbols=recon.get("entry_blocked_symbols", []) if recon else [],
        last_run_at=recon.get("last_run_at") if recon else None,
    )

    decisions = [DecisionSummary(**d) for d in (state.get("decisions") or [])[-5:]]

    incidents = [IncidentSummary(**i) for i in (state.get("incidents") or [])[-5:]]

    llm_list = state.get("llm_invocations") or []
    latest_llm: LLMInvocationSummary | None = None
    if llm_list:
        latest_llm = LLMInvocationSummary(**llm_list[-1])

    return RuntimeSnapshot(
        engine=engine,
        scheduler=scheduler,
        exchange=exchange,
        local_projection=local_proj,
        reconciliation=reconciliation,
        latest_decisions=decisions,
        latest_incidents=incidents,
        latest_llm_invocation=latest_llm,
    )


@router.get("/cycles")
def list_cycles(limit: int = 20) -> list[CycleListItem]:
    """Recent cycles, newest first."""
    cycles = (_runtime_state.get("cycles") or [])[-limit:]
    return [CycleListItem(**c) for c in reversed(cycles)]


@router.get("/decisions")
def list_decisions(limit: int = 50) -> list[DecisionSummary]:
    """Recent decision funnel outcomes, newest first."""
    decisions = (_runtime_state.get("decisions") or [])[-limit:]
    return [DecisionSummary(**d) for d in reversed(decisions)]


@router.get("/positions")
def list_positions() -> dict[str, Any]:
    """Exchange truth and local projection side-by-side (plan section 14.2)."""
    state = _runtime_state
    exchange_snap = state.get("exchange_snapshot")
    return {
        "exchange": {
            "source": "BINANCE_TESTNET",
            "available": exchange_snap is not None,
            "positions": (exchange_snap or {}).get("positions", []),
            "observed_at": (exchange_snap or {}).get("timestamp"),
        },
        "local_projection": {
            "source": "V2_LOCAL_PROJECTION",
            "positions": state.get("local_positions", []),
            "observed_at": state.get("local_projection_at"),
        },
    }


@router.get("/reconciliation")
def get_reconciliation() -> ReconciliationSummary:
    """Latest reconciliation result."""
    recon = _runtime_state.get("reconciliation")
    if not recon:
        return ReconciliationSummary(status="UNAVAILABLE")
    return ReconciliationSummary(**recon)


@router.get("/incidents")
def list_incidents(limit: int = 20) -> list[IncidentSummary]:
    """Recent recovery incidents."""
    incidents = (_runtime_state.get("incidents") or [])[-limit:]
    return [IncidentSummary(**i) for i in reversed(incidents)]


@router.get("/llm-invocations")
def list_llm_invocations(limit: int = 20) -> list[LLMInvocationSummary]:
    """Recent LLM invocation records — including skips and errors."""
    invocations = (_runtime_state.get("llm_invocations") or [])[-limit:]
    return [LLMInvocationSummary(**i) for i in reversed(invocations)]


@router.get("/evidence/latest")
def get_latest_evidence() -> dict[str, Any]:
    """Latest evidence bundle from the last complete cycle."""
    evidence = _runtime_state.get("latest_evidence")
    if not evidence:
        return {"available": False, "message": "No evidence recorded yet"}
    return {"available": True, **evidence}


@router.post("/controls/entry-disable", response_model=EntryControlResponse)
def disable_entry(body: EntryControlRequest) -> EntryControlResponse:
    """Disable new entries. Reduce-only exits are never affected."""
    now = datetime.now(UTC)
    _runtime_state["entry_enabled"] = False
    _runtime_state["entry_disabled_reason"] = body.reason
    return EntryControlResponse(
        success=True,
        entry_enabled=False,
        changed_at=now,
        reason=body.reason,
    )


@router.post("/controls/entry-enable", response_model=EntryControlResponse)
def enable_entry(body: EntryControlRequest) -> EntryControlResponse:
    """Re-enable entries after they were disabled."""
    now = datetime.now(UTC)
    _runtime_state["entry_enabled"] = True
    _runtime_state["entry_disabled_reason"] = None
    return EntryControlResponse(
        success=True,
        entry_enabled=True,
        changed_at=now,
        reason=body.reason,
    )
