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

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from apps.api.config import settings as api_settings
from services.automated_trading.domain.enums import V2ExecutionMode
from services.automated_trading.infrastructure.models import (
    V2ExecutionCycle,
    V2ExecutionDecision,
    V2ExecutionIncident,
    V2ManagedPosition,
    V2ReconciliationSnapshot,
)
from services.automated_trading.infrastructure.repository import AutomatedTradingRepository
from services.automated_trading.infrastructure.runtime_lock import resolve_engine_activation
from services.database import get_db_session
from services.strategy_library import LlmInvocationRepository
from shared.models import LlmInvocation

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
    entry_enabled: bool = False


class EntryControlRequest(BaseModel):
    reason: str


class EntryControlResponse(BaseModel):
    success: bool
    entry_enabled: bool
    changed_at: datetime
    reason: str


# ---------------------------------------------------------------------------
# DB-backed helpers
# ---------------------------------------------------------------------------


def _repo(db: Session) -> AutomatedTradingRepository:
    return AutomatedTradingRepository(db)


def _engine_activation() -> str:
    try:
        return resolve_engine_activation(api_settings).v2_activation.value
    except ValueError:
        return "DISABLED"


def _engine_mode() -> str:
    try:
        return resolve_engine_activation(api_settings).execution_mode.value
    except ValueError:
        return V2ExecutionMode.BINANCE_TESTNET.value


def _position_to_dict(position: V2ManagedPosition) -> dict[str, Any]:
    return {
        "symbol": position.symbol,
        "direction": position.direction,
        "quantity": str(position.quantity),
        "qty": str(position.quantity),
        "entry_price": str(position.entry_price),
        "state": position.state,
    }


def _extract_exchange_payload(
    snapshot: V2ReconciliationSnapshot | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], datetime | None]:
    if snapshot is None:
        return [], [], None
    exchange_positions = snapshot.exchange_positions
    exchange_open_orders = snapshot.exchange_open_orders
    positions: list[dict[str, Any]]
    if isinstance(exchange_positions, dict):
        raw_positions = exchange_positions.get("positions", [])
        positions = raw_positions if isinstance(raw_positions, list) else []
    elif isinstance(exchange_positions, list):
        positions = exchange_positions
    else:
        positions = []
    if isinstance(exchange_open_orders, dict):
        raw_orders = exchange_open_orders.get("open_orders", exchange_open_orders.get("orders", []))
        open_orders = raw_orders if isinstance(raw_orders, list) else []
    elif isinstance(exchange_open_orders, list):
        open_orders = exchange_open_orders
    else:
        open_orders = []
    return positions, open_orders, snapshot.captured_at


def _reconciliation_from_snapshot(snapshot: V2ReconciliationSnapshot | None) -> ReconciliationSummary:
    if snapshot is None:
        return ReconciliationSummary(status="UNAVAILABLE")
    discrepancies = snapshot.discrepancies or {}
    mismatches: list[dict[str, Any]]
    entry_blocked_symbols: list[str]
    if isinstance(discrepancies, list):
        mismatches = discrepancies
        entry_blocked_symbols = []
    else:
        raw_mismatches = discrepancies.get("mismatches", [])
        mismatches = raw_mismatches if isinstance(raw_mismatches, list) else []
        raw_blocked = discrepancies.get("entry_blocked_symbols", [])
        entry_blocked_symbols = raw_blocked if isinstance(raw_blocked, list) else []
    return ReconciliationSummary(
        status=snapshot.status,
        mismatches=mismatches,
        entry_blocked_symbols=entry_blocked_symbols,
        last_run_at=snapshot.captured_at,
    )


def _decision_to_summary(decision: V2ExecutionDecision, cycle: V2ExecutionCycle) -> DecisionSummary:
    payload = decision.payload or {}
    return DecisionSummary(
        symbol=cycle.symbol,
        terminal_stage=cycle.decision_terminal or str(payload.get("terminal_stage") or "UNKNOWN"),
        reason_code=decision.terminal_reason or str(payload.get("reason_code") or "UNKNOWN"),
        evaluated_at=decision.created_at,
        candidate_id=decision.candidate_key,
        exchange_submitted=bool(payload.get("exchange_submitted", False)),
    )


def _incident_to_summary(incident: V2ExecutionIncident) -> IncidentSummary:
    context = incident.context or {}
    return IncidentSummary(
        incident_id=incident.incident_id,
        severity=incident.severity,
        incident_type=incident.incident_type,
        state="RESOLVED" if incident.resolved else "OPEN",
        entry_block_all=bool(context.get("entry_block_all", False)),
        created_at=incident.created_at,
    )


def _llm_to_summary(invocation: LlmInvocation) -> LLMInvocationSummary:
    return LLMInvocationSummary(
        stage=invocation.stage.value,
        status=invocation.status,
        provider=invocation.provider,
        model=invocation.model,
        total_tokens=invocation.total_tokens or None,
        error_code=invocation.error,
        skip_reason=invocation.skip_reason,
        invoked_at=invocation.created_at,
    )


def _cycle_to_item(
    cycle: V2ExecutionCycle,
    *,
    activation: str,
    entry_enabled: bool,
    reconciliation_status: str | None,
) -> CycleListItem:
    return CycleListItem(
        cycle_id=cycle.cycle_id,
        mode=cycle.execution_mode,
        activation=activation,
        status="COMPLETED" if cycle.completed_at else "RUNNING",
        started_at=cycle.started_at,
        completed_at=cycle.completed_at,
        reconciliation_status=reconciliation_status,
        entry_enabled=entry_enabled,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/runtime", response_model=RuntimeSnapshot)
def get_runtime(db: Session = Depends(get_db_session)) -> RuntimeSnapshot:
    """Single-call runtime truth snapshot.

    Returns null / UNAVAILABLE on every field that lacks fresh data.
    Never returns 0 to mean "unavailable".
    """
    repo = _repo(db)
    control = repo.get_runtime_control("global")
    activation = _engine_activation()
    mode = _engine_mode()
    latest_cycle = repo.get_latest_cycle()
    latest_recon = repo.get_latest_reconciliation_snapshot()
    execution_mode = V2ExecutionMode(mode)
    local_positions = repo.get_open_positions(execution_mode)
    exchange_positions, exchange_open_orders, exchange_observed_at = _extract_exchange_payload(latest_recon)
    decisions = [_decision_to_summary(decision, cycle) for decision, cycle in repo.list_recent_decisions(limit=5)]
    incidents = [_incident_to_summary(incident) for incident in repo.list_recent_incidents(limit=5)]
    llm_items = LlmInvocationRepository(db).list_invocations(limit=1)
    latest_llm = _llm_to_summary(llm_items[0]) if llm_items else None

    engine = EngineStatus(
        engine_id="automated-trading-v2",
        mode=mode,
        activation=activation,
        entry_enabled=control.entry_enabled,
        mainnet_supported=False,
    )

    scheduler = SchedulerStatus(
        running=activation in {"ACTIVE", "SHADOW"},
        last_cycle_at=(latest_cycle.completed_at or latest_cycle.started_at) if latest_cycle else None,
        next_cycle_at=None,
    )

    if latest_recon is not None:
        exchange = ExchangeStatus(
            source="BINANCE_TESTNET_REST",
            timestamp=exchange_observed_at,
            freshness="FRESH",
            positions=exchange_positions,
            open_orders=exchange_open_orders,
            available=True,
        )
    else:
        exchange = ExchangeStatus(
            source="BINANCE_TESTNET_REST",
            freshness="UNAVAILABLE",
            available=False,
        )

    local_proj = LocalProjection(
        timestamp=local_positions[0].projected_at if local_positions else None,
        positions=[_position_to_dict(position) for position in local_positions],
    )

    return RuntimeSnapshot(
        engine=engine,
        scheduler=scheduler,
        exchange=exchange,
        local_projection=local_proj,
        reconciliation=_reconciliation_from_snapshot(latest_recon),
        latest_decisions=decisions,
        latest_incidents=incidents,
        latest_llm_invocation=latest_llm,
    )


@router.get("/cycles")
def list_cycles(limit: int = 20, db: Session = Depends(get_db_session)) -> list[CycleListItem]:
    """Recent cycles, newest first."""
    repo = _repo(db)
    activation = _engine_activation()
    entry_enabled = repo.get_runtime_control("global").entry_enabled
    latest_recon = repo.get_latest_reconciliation_snapshot()
    recon_status = latest_recon.status if latest_recon else None
    return [
        _cycle_to_item(
            cycle,
            activation=activation,
            entry_enabled=entry_enabled,
            reconciliation_status=recon_status,
        )
        for cycle in repo.list_recent_cycles(limit=limit)
    ]


@router.get("/decisions")
def list_decisions(limit: int = 50, db: Session = Depends(get_db_session)) -> list[DecisionSummary]:
    """Recent decision funnel outcomes, newest first."""
    repo = _repo(db)
    return [_decision_to_summary(decision, cycle) for decision, cycle in repo.list_recent_decisions(limit=limit)]


@router.get("/positions")
def list_positions(db: Session = Depends(get_db_session)) -> dict[str, Any]:
    """Exchange truth and local projection side-by-side (plan section 14.2)."""
    repo = _repo(db)
    latest_recon = repo.get_latest_reconciliation_snapshot()
    exchange_positions, _, exchange_observed_at = _extract_exchange_payload(latest_recon)
    mode = _engine_mode()
    local_positions = repo.get_open_positions(V2ExecutionMode(mode))
    return {
        "exchange": {
            "source": "BINANCE_TESTNET",
            "available": latest_recon is not None,
            "positions": exchange_positions,
            "observed_at": exchange_observed_at,
        },
        "local_projection": {
            "source": "V2_LOCAL_PROJECTION",
            "positions": [_position_to_dict(position) for position in local_positions],
            "observed_at": local_positions[0].projected_at if local_positions else None,
        },
    }


@router.get("/reconciliation")
def get_reconciliation(db: Session = Depends(get_db_session)) -> ReconciliationSummary:
    """Latest reconciliation result."""
    return _reconciliation_from_snapshot(_repo(db).get_latest_reconciliation_snapshot())


@router.get("/incidents")
def list_incidents(limit: int = 20, db: Session = Depends(get_db_session)) -> list[IncidentSummary]:
    """Recent recovery incidents."""
    return [_incident_to_summary(incident) for incident in _repo(db).list_recent_incidents(limit=limit)]


@router.get("/llm-invocations")
def list_llm_invocations(limit: int = 20, db: Session = Depends(get_db_session)) -> list[LLMInvocationSummary]:
    """Recent LLM invocation records — including skips and errors."""
    items = LlmInvocationRepository(db).list_invocations(limit=limit)
    return [_llm_to_summary(item) for item in items]


@router.get("/evidence/latest")
def get_latest_evidence(db: Session = Depends(get_db_session)) -> dict[str, Any]:
    """Latest evidence bundle from the last complete cycle."""
    latest_recon = _repo(db).get_latest_reconciliation_snapshot()
    if latest_recon is None:
        return {"available": False, "message": "No evidence recorded yet"}
    return {
        "available": True,
        "snapshot_id": latest_recon.snapshot_id,
        "cycle_id": latest_recon.cycle_id,
        "status": latest_recon.status,
        "captured_at": latest_recon.captured_at.isoformat(),
        "execution_mode": latest_recon.execution_mode,
    }


@router.post("/controls/entry-disable", response_model=EntryControlResponse)
def disable_entry(body: EntryControlRequest, db: Session = Depends(get_db_session)) -> EntryControlResponse:
    """Disable new entries. Reduce-only exits are never affected."""
    repo = _repo(db)
    control = repo.set_runtime_control(
        scope="global",
        entry_enabled=False,
        reason=body.reason,
        updated_by="api",
    )
    repo.commit()
    return EntryControlResponse(
        success=True,
        entry_enabled=control.entry_enabled,
        changed_at=control.updated_at,
        reason=body.reason,
    )


@router.post("/controls/entry-enable", response_model=EntryControlResponse)
def enable_entry(body: EntryControlRequest, db: Session = Depends(get_db_session)) -> EntryControlResponse:
    """Re-enable entries after they were disabled."""
    repo = _repo(db)
    control = repo.set_runtime_control(
        scope="global",
        entry_enabled=True,
        reason=body.reason,
        updated_by="api",
    )
    repo.commit()
    return EntryControlResponse(
        success=True,
        entry_enabled=control.entry_enabled,
        changed_at=control.updated_at,
        reason=body.reason,
    )
