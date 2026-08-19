"""Safe resolution of irrecoverable *historical* Binance Testnet ledger gaps.

This module deliberately has no exchange-write capability.  It records the
strict cutover semantics for a confirmed old entry whose exact terminal fill
cannot be attributed without inventing an order, trade id, price, timestamp or
PnL.  Current exposure reconciliation remains fail-closed; only a flat,
post-cutover Testnet episode can be recorded here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import Session

from services.automated_trading.domain.enums import V2ExecutionMode, V2ProtectionState
from services.automated_trading.infrastructure.models import (
    V2ExchangeFill,
    V2ExecutionIncident,
    V2ExecutionIntent,
    V2ManagedPosition,
    V2ProtectionRecord,
)
from services.automated_trading.infrastructure.repository import AutomatedTradingRepository


class HistoricalEvidenceSource(StrEnum):
    """The exhaustive sources required before a ledger-gap cutover."""

    V2_SQLITE = "V2_SQLITE"
    LOCAL_LOGS = "LOCAL_LOGS"
    PROJECT_EVIDENCE = "PROJECT_EVIDENCE"
    GIT_HISTORY = "GIT_HISTORY"
    BINANCE_TESTNET_HISTORY = "BINANCE_TESTNET_HISTORY"


_REQUIRED_SOURCES = frozenset(HistoricalEvidenceSource)
_INCIDENT_TYPE = "HISTORICAL_LEDGER_GAP"


@dataclass(frozen=True)
class HistoricalLedgerGapResolution:
    """Durable result of a Testnet-only historical cutover."""

    incident_id: str
    case_id: str
    created: bool
    strategy_performance_eligible: bool = False


def record_historical_ledger_gap(
    session: Session,
    *,
    intent_id: str,
    current_flat_symbols: frozenset[str],
    current_open_order_ids: frozenset[str],
    active_protection_ids: frozenset[str],
    sources_checked: frozenset[HistoricalEvidenceSource],
    cutover_epoch: str,
    writer_started_at: datetime,
    resolution_reason: str,
    last_known_exchange_evidence: dict[str, str],
    observed_at: datetime,
) -> HistoricalLedgerGapResolution:
    """Record an auditable gap without creating a synthetic terminal lifecycle.

    Preconditions mirror the Gate 0 cutover contract.  The caller must obtain
    authoritative current-account facts and exhaustive-search evidence first;
    this function validates that those facts are sufficient to stop *current*
    exposure from being blocked while preserving historical incompleteness.
    """
    intent = session.get(V2ExecutionIntent, intent_id)
    if intent is None:
        raise ValueError(f"Historical ledger gap intent {intent_id!r} not found")
    if intent.execution_mode != V2ExecutionMode.BINANCE_TESTNET.value:
        raise ValueError("HISTORICAL_LEDGER_GAP is permitted only for BINANCE_TESTNET")
    if intent.symbol not in current_flat_symbols:
        raise ValueError("historical cutover requires an authoritative current flat symbol snapshot")
    if current_open_order_ids:
        raise ValueError("historical cutover requires no linked current exchange orders")
    if active_protection_ids:
        raise ValueError("historical cutover requires no linked active protections")
    if not cutover_epoch or not resolution_reason:
        raise ValueError("historical cutover requires an epoch and explicit resolution reason")
    if not _REQUIRED_SOURCES.issubset(sources_checked):
        missing = sorted(source.value for source in _REQUIRED_SOURCES - sources_checked)
        raise ValueError(f"historical cutover requires exhaustive evidence sources: missing {missing}")

    entry_fills = tuple(
        session.scalars(
            select(V2ExchangeFill).where(
                V2ExchangeFill.intent_id == intent_id,
                V2ExchangeFill.reduce_only.is_(False),
            )
        )
    )
    if not entry_fills:
        raise ValueError("historical cutover requires a confirmed non-reduce-only entry fill")
    last_entry_fill_at = max(fill.exchange_event_time for fill in entry_fills)
    if last_entry_fill_at.tzinfo is None:
        last_entry_fill_at = last_entry_fill_at.replace(tzinfo=UTC)
    if writer_started_at.tzinfo is None:
        writer_started_at = writer_started_at.replace(tzinfo=UTC)
    if writer_started_at <= last_entry_fill_at:
        raise ValueError("historical cutover requires the writer startup to post-date the legacy episode")
    if session.scalar(select(V2ManagedPosition).where(V2ManagedPosition.intent_id == intent_id)) is not None:
        raise ValueError("historical cutover is only for unprojected entry lifecycles")
    local_active_protection = session.scalar(
        select(V2ProtectionRecord)
        .join(V2ManagedPosition, V2ManagedPosition.position_id == V2ProtectionRecord.position_id)
        .where(
            V2ManagedPosition.intent_id == intent_id,
            V2ProtectionRecord.state == V2ProtectionState.PROTECTION_ACTIVE.value,
        )
    )
    if local_active_protection is not None:
        raise ValueError("historical cutover refuses an entry with active local protection")

    existing = session.scalar(
        select(V2ExecutionIncident).where(
            V2ExecutionIncident.incident_type == _INCIDENT_TYPE,
            V2ExecutionIncident.intent_id == intent_id,
        )
    )
    case_id = f"HIST-V2-ENTRY-{intent_id[:8]}"
    if existing is not None:
        return HistoricalLedgerGapResolution(existing.incident_id, case_id, created=False)

    repo = AutomatedTradingRepository(session)
    incident_id = repo.record_incident(
        incident_type=_INCIDENT_TYPE,
        severity="MEDIUM",
        related_aggregate_id=intent_id,
        intent_id=intent_id,
        position_id=None,
        description=(
            "LEGACY_TERMINAL_EVIDENCE_UNAVAILABLE: Testnet entry is historical only and is excluded from performance"
        ),
        context={
            "case_id": case_id,
            "position_id": None,
            "entry_identity": {
                "intent_id": intent_id,
                "symbol": intent.symbol,
                "direction": intent.direction,
                "entry_trade_ids": [str(fill.trade_id) for fill in entry_fills if fill.trade_id],
                "entry_exchange_order_ids": sorted({str(fill.exchange_order_id) for fill in entry_fills}),
            },
            "last_known_exchange_evidence": dict(last_known_exchange_evidence),
            "current_flat_snapshot": {"flat_symbols": sorted(current_flat_symbols)},
            "current_open_order_ids": sorted(current_open_order_ids),
            "active_protection_ids": sorted(active_protection_ids),
            "exhaustive_search_timestamp": observed_at.isoformat(),
            "sources_checked": sorted(source.value for source in sources_checked),
            "cutover_epoch": cutover_epoch,
            "writer_started_at": writer_started_at.isoformat(),
            "resolution_reason": resolution_reason,
            "resolution": "HISTORICAL_LEDGER_GAP",
            "realized_pnl": "UNKNOWN",
            "strategy_performance_eligible": False,
            "current_exposure_blocker": False,
        },
    )
    incident = session.get(V2ExecutionIncident, incident_id)
    if incident is None:  # pragma: no cover - repository contract guard
        raise RuntimeError("historical ledger incident was not durable")
    # It is resolved for *current exposure* only. The immutable incident and
    # its DEGRADED historical-ledger projection remain visible forever.
    incident.resolved = True
    incident.resolved_at = observed_at
    session.flush()
    return HistoricalLedgerGapResolution(incident_id, case_id, created=True)
