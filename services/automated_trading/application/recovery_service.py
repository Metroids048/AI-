"""Restart and anomaly recovery for V2 automated trading.

Recovery runs on the first cycle after process start, and whenever reconciliation
returns RECOVERY_REQUIRED. Its job is to resolve ambiguity without ever creating
new risk (plan section 9.3):

1. Entry stays blocked for the whole recovery pass.
2. Pull a complete account snapshot.
3. Recover every V2 client order id (UNKNOWN intents are resolved by client id,
   never by blind resubmission).
4. Re-verify protection for every managed position.
5. Handle EMERGENCY_CLOSE_PENDING.
6. Only a clean reconciliation afterwards may lift the Entry block.

Recovery may submit reduce-only protection and reduce-only emergency closes. It
must never submit a new Entry, never adopt an unclaimable external position, and
never mutate the exchange to match stale local state.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING

from services.automated_trading.application.reconciliation_service import (
    Discrepancy,
    DiscrepancyCode,
    LocalStateView,
    OwnershipStatus,
    ReconciliationResult,
    ReconciliationStatus,
)

if TYPE_CHECKING:
    from services.automated_trading.infrastructure.market_snapshot_provider import (
        AuthoritativeAccountSnapshot,
    )


class RecoveryActionType(StrEnum):
    """Recovery actions. All are either read-only or strictly reduce-only."""

    RESOLVE_UNKNOWN_INTENT_BY_CLIENT_ID = "RESOLVE_UNKNOWN_INTENT_BY_CLIENT_ID"
    MARK_INTENT_NOT_SUBMITTED = "MARK_INTENT_NOT_SUBMITTED"
    RESUBMIT_PROTECTION = "RESUBMIT_PROTECTION"
    EMERGENCY_REDUCE_ONLY_CLOSE = "EMERGENCY_REDUCE_ONLY_CLOSE"
    QUARANTINE_EXTERNAL_POSITION = "QUARANTINE_EXTERNAL_POSITION"
    QUARANTINE_LOCAL_GHOST_POSITION = "QUARANTINE_LOCAL_GHOST_POSITION"
    CANCEL_ORPHAN_PROTECTION_ORDER = "CANCEL_ORPHAN_PROTECTION_ORDER"
    MANUAL_INTERVENTION_REQUIRED = "MANUAL_INTERVENTION_REQUIRED"


@dataclass(frozen=True)
class RecoveryAction:
    """A single planned recovery action.

    Actions are returned as a plan; the caller executes them through the adapter.
    No action in this module opens or increases risk.
    """

    action_type: RecoveryActionType
    target_ref: str
    symbol: str | None
    reason: str
    reduce_only: bool = True

    def __post_init__(self) -> None:
        if not self.reduce_only:
            raise ValueError(
                f"Recovery action {self.action_type.value} must be reduce-only; recovery may never add risk"
            )


@dataclass(frozen=True)
class PendingIntentView:
    """An intent that may or may not have reached the exchange."""

    intent_id: str
    symbol: str
    client_order_id: str
    state: str  # EXCHANGE_SUBMITTING | EXCHANGE_UNKNOWN | EXCHANGE_ACKNOWLEDGED


@dataclass(frozen=True)
class EmergencyCloseRequest:
    """A position flagged for emergency reduce-only close before restart."""

    position_id: str
    symbol: str
    direction: str
    quantity: Decimal
    reason: str


@dataclass(frozen=True)
class RecoveryResult:
    """Outcome of a recovery pass."""

    actions: tuple[RecoveryAction, ...]
    entry_blocked: bool
    unresolved_refs: tuple[str, ...]
    resolved_refs: tuple[str, ...]
    recovered_at: datetime | None = None
    notes: tuple[str, ...] = ()

    @property
    def requires_manual_intervention(self) -> bool:
        return any(action.action_type is RecoveryActionType.MANUAL_INTERVENTION_REQUIRED for action in self.actions)

    @property
    def reduce_only_exit_allowed(self) -> bool:
        """Reduce-only exit is never blocked by recovery."""
        return True


def recover_pending_state(
    snapshot: AuthoritativeAccountSnapshot | None,
    incidents: ReconciliationResult,
    *,
    local_state: LocalStateView | None = None,
    pending_intents: tuple[PendingIntentView, ...] = (),
    emergency_closes: tuple[EmergencyCloseRequest, ...] = (),
    recovered_at: datetime | None = None,
) -> RecoveryResult:
    """Plan recovery actions from a snapshot and reconciliation findings.

    Args:
        snapshot: Authoritative exchange snapshot, or None if unobtainable.
        incidents: Reconciliation result carrying the discrepancies to resolve.
        local_state: Local projection (used for ghost-position quarantine).
        pending_intents: Intents in submitting/unknown state to resolve by client id.
        emergency_closes: Positions flagged EMERGENCY_CLOSE_PENDING.
        recovered_at: Timestamp for the result.

    Returns:
        RecoveryResult with a reduce-only action plan. Entry stays blocked unless
        the pass is fully clean.
    """
    actions: list[RecoveryAction] = []
    unresolved: list[str] = []
    resolved: list[str] = []
    notes: list[str] = []

    if snapshot is None:
        # Cannot recover without exchange truth. Entry stays blocked; exits still allowed.
        notes.append("exchange snapshot unavailable; recovery deferred, Entry remains blocked")
        for ref in incidents.recovery_required_refs:
            unresolved.append(ref)
        actions.extend(
            RecoveryAction(
                action_type=RecoveryActionType.EMERGENCY_REDUCE_ONLY_CLOSE,
                target_ref=request.position_id,
                symbol=request.symbol,
                reason=f"emergency close pending before restart: {request.reason}",
            )
            for request in emergency_closes
        )
        return RecoveryResult(
            actions=tuple(actions),
            entry_blocked=True,
            unresolved_refs=tuple(dict.fromkeys(unresolved)),
            resolved_refs=(),
            recovered_at=recovered_at,
            notes=tuple(notes),
        )

    exchange_client_ids = {
        order.client_order_id for order in snapshot.pending_orders if order.client_order_id is not None
    }
    exchange_order_ids = {order.exchange_order_id for order in snapshot.pending_orders}
    exchange_position_keys = {f"{pos.symbol}:{pos.direction}" for pos in snapshot.positions}

    # --- 1. Resolve UNKNOWN / in-flight intents by client order id ---
    for intent in pending_intents:
        if intent.client_order_id in exchange_client_ids:
            actions.append(
                RecoveryAction(
                    action_type=RecoveryActionType.RESOLVE_UNKNOWN_INTENT_BY_CLIENT_ID,
                    target_ref=intent.intent_id,
                    symbol=intent.symbol,
                    reason=(
                        f"client order id {intent.client_order_id} found at exchange; "
                        "adopt exchange state instead of resubmitting"
                    ),
                )
            )
            resolved.append(intent.intent_id)
        else:
            # Not at exchange: the submission never landed. Mark it, do not resubmit here.
            actions.append(
                RecoveryAction(
                    action_type=RecoveryActionType.MARK_INTENT_NOT_SUBMITTED,
                    target_ref=intent.intent_id,
                    symbol=intent.symbol,
                    reason=(
                        f"client order id {intent.client_order_id} absent from exchange; "
                        "intent did not reach the exchange"
                    ),
                )
            )
            resolved.append(intent.intent_id)

    # --- 2. Act on reconciliation discrepancies ---
    for discrepancy in incidents.discrepancies:
        action = _plan_for_discrepancy(
            discrepancy,
            exchange_order_ids=exchange_order_ids,
            exchange_position_keys=exchange_position_keys,
        )
        if action is None:
            unresolved.append(discrepancy.exchange_ref or discrepancy.local_position_id or discrepancy.code.value)
            continue
        actions.append(action)
        ref = action.target_ref
        if action.action_type is RecoveryActionType.MANUAL_INTERVENTION_REQUIRED:
            unresolved.append(ref)
        else:
            resolved.append(ref)

    # --- 3. Emergency reduce-only closes ---
    for request in emergency_closes:
        actions.append(
            RecoveryAction(
                action_type=RecoveryActionType.EMERGENCY_REDUCE_ONLY_CLOSE,
                target_ref=request.position_id,
                symbol=request.symbol,
                reason=f"emergency close pending: {request.reason}",
            )
        )
        resolved.append(request.position_id)

    # --- 4. Local ghost positions with no exchange counterpart ---
    if local_state is not None:
        for position in local_state.positions:
            if position.state in ("CLOSED", "QUARANTINED"):
                continue
            key = f"{position.symbol}:{position.direction}"
            if key in exchange_position_keys:
                continue
            if any(
                action.target_ref == position.position_id
                and action.action_type is RecoveryActionType.QUARANTINE_LOCAL_GHOST_POSITION
                for action in actions
            ):
                continue
            actions.append(
                RecoveryAction(
                    action_type=RecoveryActionType.QUARANTINE_LOCAL_GHOST_POSITION,
                    target_ref=position.position_id,
                    symbol=position.symbol,
                    reason="local open position has no exchange counterpart; quarantine the projection",
                )
            )
            resolved.append(position.position_id)

    entry_blocked = bool(unresolved) or incidents.status is not ReconciliationStatus.HEALTHY or bool(actions)
    if not actions and incidents.status is ReconciliationStatus.HEALTHY:
        entry_blocked = False
        notes.append("recovery pass clean; Entry block may be lifted after a healthy reconciliation")

    return RecoveryResult(
        actions=tuple(actions),
        entry_blocked=entry_blocked,
        unresolved_refs=tuple(dict.fromkeys(unresolved)),
        resolved_refs=tuple(dict.fromkeys(resolved)),
        recovered_at=recovered_at,
        notes=tuple(notes),
    )


def _plan_for_discrepancy(
    discrepancy: Discrepancy,
    *,
    exchange_order_ids: set[str],
    exchange_position_keys: set[str],
) -> RecoveryAction | None:
    """Map a discrepancy to a reduce-only recovery action."""
    code = discrepancy.code

    if code is DiscrepancyCode.EXTERNAL_POSITION_UNCLAIMABLE:
        return RecoveryAction(
            action_type=RecoveryActionType.QUARANTINE_EXTERNAL_POSITION,
            target_ref=discrepancy.exchange_ref or (discrepancy.symbol or "unknown"),
            symbol=discrepancy.symbol,
            reason=(
                f"{OwnershipStatus.EXTERNAL_QUARANTINED.value}: no identity match; "
                "never auto-adopt and never auto-close"
            ),
        )

    if code is DiscrepancyCode.MANAGED_POSITION_UNPROTECTED:
        return RecoveryAction(
            action_type=RecoveryActionType.RESUBMIT_PROTECTION,
            target_ref=discrepancy.local_position_id or (discrepancy.symbol or "unknown"),
            symbol=discrepancy.symbol,
            reason="managed position is live at exchange without active protection",
        )

    if code is DiscrepancyCode.PROTECTION_ORDER_MISSING_AT_EXCHANGE:
        return RecoveryAction(
            action_type=RecoveryActionType.RESUBMIT_PROTECTION,
            target_ref=discrepancy.local_position_id or (discrepancy.symbol or "unknown"),
            symbol=discrepancy.symbol,
            reason="locally ACTIVE protection is absent from exchange open orders",
        )

    if code is DiscrepancyCode.LOCAL_POSITION_MISSING_AT_EXCHANGE:
        return RecoveryAction(
            action_type=RecoveryActionType.QUARANTINE_LOCAL_GHOST_POSITION,
            target_ref=discrepancy.local_position_id or (discrepancy.symbol or "unknown"),
            symbol=discrepancy.symbol,
            reason="local open position has no exchange counterpart",
        )

    if code is DiscrepancyCode.ORPHAN_V2_CLIENT_ORDER_AT_EXCHANGE:
        return RecoveryAction(
            action_type=RecoveryActionType.CANCEL_ORPHAN_PROTECTION_ORDER,
            target_ref=discrepancy.exchange_ref or "unknown",
            symbol=discrepancy.symbol,
            reason="V2-shaped client order id at exchange with no local record",
        )

    if code is DiscrepancyCode.UNKNOWN_ORDER_PRESENT:
        return RecoveryAction(
            action_type=RecoveryActionType.MANUAL_INTERVENTION_REQUIRED,
            target_ref=discrepancy.exchange_ref or "unknown",
            symbol=discrepancy.symbol,
            reason="exchange order without client order id cannot be attributed automatically",
        )

    if code in (DiscrepancyCode.QUANTITY_MISMATCH, DiscrepancyCode.DIRECTION_MISMATCH):
        return RecoveryAction(
            action_type=RecoveryActionType.MANUAL_INTERVENTION_REQUIRED,
            target_ref=discrepancy.local_position_id or (discrepancy.symbol or "unknown"),
            symbol=discrepancy.symbol,
            reason=f"{code.value}: local projection disagrees with exchange truth",
        )

    return None
