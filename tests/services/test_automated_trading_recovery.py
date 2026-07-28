"""Tests for restart and anomaly recovery.

Gate 5 requirements covered here:
- UNKNOWN intents are resolved by client order id, never by blind resubmission
- External positions are quarantined, never auto-adopted or auto-closed
- Restart recovers protection and handles emergency close
- Recovery never plans an action that adds risk
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from services.automated_trading.application.reconciliation_service import (
    Discrepancy,
    DiscrepancyCode,
    LocalPositionView,
    LocalStateView,
    ReconciliationResult,
    ReconciliationStatus,
    reconcile,
    unavailable,
)
from services.automated_trading.application.recovery_service import (
    EmergencyCloseRequest,
    PendingIntentView,
    RecoveryAction,
    RecoveryActionType,
    recover_pending_state,
)
from services.automated_trading.infrastructure.market_snapshot_provider import (
    AuthoritativeAccountSnapshot,
    ExchangeOrderSnapshot,
    ExchangePositionSnapshot,
)

NOW = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)


def _snapshot(
    *,
    positions: list[ExchangePositionSnapshot] | None = None,
    orders: list[ExchangeOrderSnapshot] | None = None,
) -> AuthoritativeAccountSnapshot:
    return AuthoritativeAccountSnapshot(
        balance=Decimal("10000"),
        equity=Decimal("10000"),
        positions=positions or [],
        pending_orders=orders or [],
        snapshot_timestamp=NOW,
    )


def _exchange_position(
    symbol: str = "BTC/USDT",
    direction: str = "long",
    quantity: str = "0.1",
) -> ExchangePositionSnapshot:
    return ExchangePositionSnapshot(
        symbol=symbol,
        direction=direction,
        quantity=Decimal(quantity),
        entry_price=Decimal("50000"),
        mark_price=Decimal("50100"),
        unrealized_pnl=Decimal("10"),
        leverage=10,
    )


def _order(
    *,
    exchange_order_id: str = "ex_1",
    client_order_id: str | None = "v2_entry_abc",
    symbol: str = "BTC/USDT",
) -> ExchangeOrderSnapshot:
    return ExchangeOrderSnapshot(
        exchange_order_id=exchange_order_id,
        client_order_id=client_order_id,
        symbol=symbol,
        side="buy",
        order_type="market",
        quantity=Decimal("0.1"),
        price=None,
        status="new",
        reduce_only=False,
    )


def _healthy() -> ReconciliationResult:
    return ReconciliationResult(
        status=ReconciliationStatus.HEALTHY,
        discrepancies=(),
        entry_blocked_symbols=frozenset(),
        reconciled_at=NOW,
    )


def _incidents(*discrepancies: Discrepancy) -> ReconciliationResult:
    return ReconciliationResult(
        status=ReconciliationStatus.RECOVERY_REQUIRED,
        discrepancies=discrepancies,
        entry_blocked_symbols=frozenset(d.symbol for d in discrepancies if d.symbol),
        recovery_required_refs=tuple(d.local_position_id or d.exchange_ref or d.code.value for d in discrepancies),
        reconciled_at=NOW,
    )


def test_unknown_intent_resolved_by_client_order_id_not_resubmit():
    """Gate 5: UNKNOWN resolves via client order id, never a blind duplicate submit."""
    snapshot = _snapshot(orders=[_order(client_order_id="v2_entry_abc")])
    intents = (
        PendingIntentView(
            intent_id="intent_1",
            symbol="BTC/USDT",
            client_order_id="v2_entry_abc",
            state="EXCHANGE_UNKNOWN",
        ),
    )

    result = recover_pending_state(snapshot, _healthy(), pending_intents=intents, recovered_at=NOW)

    action_types = {a.action_type for a in result.actions}
    assert RecoveryActionType.RESOLVE_UNKNOWN_INTENT_BY_CLIENT_ID in action_types
    assert "intent_1" in result.resolved_refs
    # No recovery action ever submits a new entry
    assert all(a.reduce_only for a in result.actions)


def test_unknown_intent_absent_at_exchange_is_marked_not_submitted():
    """A client id absent from the exchange means the submission never landed."""
    snapshot = _snapshot(orders=[])
    intents = (
        PendingIntentView(
            intent_id="intent_2",
            symbol="ETH/USDT",
            client_order_id="v2_entry_missing",
            state="EXCHANGE_UNKNOWN",
        ),
    )

    result = recover_pending_state(snapshot, _healthy(), pending_intents=intents, recovered_at=NOW)

    action_types = {a.action_type for a in result.actions}
    assert RecoveryActionType.MARK_INTENT_NOT_SUBMITTED in action_types
    assert RecoveryActionType.RESOLVE_UNKNOWN_INTENT_BY_CLIENT_ID not in action_types


def test_external_position_is_quarantined_never_adopted():
    """Gate 5: an unclaimable exchange position is quarantined, not adopted or closed."""
    snapshot = _snapshot(positions=[_exchange_position()])
    incidents = _incidents(
        Discrepancy(
            code=DiscrepancyCode.EXTERNAL_POSITION_UNCLAIMABLE,
            symbol="BTC/USDT",
            detail="no identity match",
            exchange_ref="BTC/USDT:long",
        )
    )

    result = recover_pending_state(snapshot, incidents, recovered_at=NOW)

    quarantines = [a for a in result.actions if a.action_type is RecoveryActionType.QUARANTINE_EXTERNAL_POSITION]
    assert len(quarantines) == 1
    assert quarantines[0].target_ref == "BTC/USDT:long"
    # No emergency close was planned for a position we do not own
    assert not any(a.action_type is RecoveryActionType.EMERGENCY_REDUCE_ONLY_CLOSE for a in result.actions)
    assert result.entry_blocked is True


def test_restart_recovers_protection_for_unprotected_position():
    """Restart re-submits protection for a live position that lost it."""
    snapshot = _snapshot(positions=[_exchange_position()])
    incidents = _incidents(
        Discrepancy(
            code=DiscrepancyCode.MANAGED_POSITION_UNPROTECTED,
            symbol="BTC/USDT",
            detail="no active protection",
            local_position_id="pos_1",
        )
    )

    result = recover_pending_state(snapshot, incidents, recovered_at=NOW)

    resubmits = [a for a in result.actions if a.action_type is RecoveryActionType.RESUBMIT_PROTECTION]
    assert len(resubmits) == 1
    assert resubmits[0].target_ref == "pos_1"
    assert resubmits[0].reduce_only is True


def test_restart_handles_emergency_close_pending():
    """EMERGENCY_CLOSE_PENDING produces a reduce-only close action."""
    snapshot = _snapshot(positions=[_exchange_position()])
    emergency = (
        EmergencyCloseRequest(
            position_id="pos_9",
            symbol="BTC/USDT",
            direction="long",
            quantity=Decimal("0.1"),
            reason="hard drawdown lock",
        ),
    )

    result = recover_pending_state(snapshot, _healthy(), emergency_closes=emergency, recovered_at=NOW)

    closes = [a for a in result.actions if a.action_type is RecoveryActionType.EMERGENCY_REDUCE_ONLY_CLOSE]
    assert len(closes) == 1
    assert closes[0].target_ref == "pos_9"
    assert closes[0].reduce_only is True


def test_emergency_close_planned_even_when_snapshot_unavailable():
    """A pending emergency close is not dropped when the snapshot is missing."""
    emergency = (
        EmergencyCloseRequest(
            position_id="pos_9",
            symbol="BTC/USDT",
            direction="long",
            quantity=Decimal("0.1"),
            reason="hard drawdown lock",
        ),
    )

    result = recover_pending_state(
        None,
        unavailable("gateway missing", reconciled_at=NOW),
        emergency_closes=emergency,
        recovered_at=NOW,
    )

    assert result.entry_blocked is True
    closes = [a for a in result.actions if a.action_type is RecoveryActionType.EMERGENCY_REDUCE_ONLY_CLOSE]
    assert len(closes) == 1
    assert result.reduce_only_exit_allowed is True


def test_local_ghost_position_is_quarantined():
    """A local open position absent from the exchange is quarantined."""
    snapshot = _snapshot(positions=[])
    local = LocalStateView(
        positions=(
            LocalPositionView(
                position_id="pos_ghost",
                symbol="BTC/USDT",
                direction="long",
                quantity=Decimal("0.1"),
                state="PROTECTED",
                claim_keys=frozenset({"pos_ghost"}),
                has_active_protection=True,
            ),
        )
    )

    result = recover_pending_state(snapshot, _healthy(), local_state=local, recovered_at=NOW)

    ghosts = [a for a in result.actions if a.action_type is RecoveryActionType.QUARANTINE_LOCAL_GHOST_POSITION]
    assert len(ghosts) == 1
    assert ghosts[0].target_ref == "pos_ghost"


def test_quantity_mismatch_escalates_to_manual_intervention():
    """A quantity disagreement is not auto-repaired."""
    snapshot = _snapshot(positions=[_exchange_position(quantity="0.2")])
    incidents = _incidents(
        Discrepancy(
            code=DiscrepancyCode.QUANTITY_MISMATCH,
            symbol="BTC/USDT",
            detail="local 0.1 vs exchange 0.2",
            local_position_id="pos_1",
        )
    )

    result = recover_pending_state(snapshot, incidents, recovered_at=NOW)

    assert result.requires_manual_intervention is True
    assert "pos_1" in result.unresolved_refs
    assert result.entry_blocked is True


def test_order_without_client_id_escalates_to_manual_intervention():
    """An unattributable exchange order requires a human."""
    snapshot = _snapshot(orders=[_order(exchange_order_id="ex_888", client_order_id=None)])
    incidents = _incidents(
        Discrepancy(
            code=DiscrepancyCode.UNKNOWN_ORDER_PRESENT,
            symbol="BTC/USDT",
            detail="no client order id",
            exchange_ref="ex_888",
        )
    )

    result = recover_pending_state(snapshot, incidents, recovered_at=NOW)

    assert result.requires_manual_intervention is True
    assert "ex_888" in result.unresolved_refs


def test_clean_pass_lifts_entry_block():
    """A healthy reconciliation with nothing to do allows Entry again."""
    result = recover_pending_state(_snapshot(), _healthy(), recovered_at=NOW)

    assert result.actions == ()
    assert result.entry_blocked is False
    assert any("Entry block may be lifted" in note for note in result.notes)


def test_snapshot_unavailable_keeps_entry_blocked():
    """No exchange truth means Entry stays blocked."""
    result = recover_pending_state(
        None,
        unavailable("REST timeout", reconciled_at=NOW),
        recovered_at=NOW,
    )

    assert result.entry_blocked is True


def test_recovery_actions_are_always_reduce_only():
    """Constructing a risk-adding recovery action is rejected outright."""
    with pytest.raises(ValueError, match="must be reduce-only"):
        RecoveryAction(
            action_type=RecoveryActionType.RESUBMIT_PROTECTION,
            target_ref="pos_1",
            symbol="BTC/USDT",
            reason="attempted risk-adding action",
            reduce_only=False,
        )


def test_reduce_only_exit_never_blocked_by_recovery():
    """Recovery never blocks reduce-only de-risking exit."""
    incidents = _incidents(
        Discrepancy(
            code=DiscrepancyCode.MANAGED_POSITION_UNPROTECTED,
            symbol="BTC/USDT",
            detail="no active protection",
            local_position_id="pos_1",
        )
    )

    result = recover_pending_state(_snapshot(positions=[_exchange_position()]), incidents, recovered_at=NOW)

    assert result.entry_blocked is True
    assert result.reduce_only_exit_allowed is True


def test_end_to_end_reconcile_then_recover():
    """Reconciliation output feeds recovery directly."""
    snapshot = _snapshot(positions=[_exchange_position()])
    local = LocalStateView(
        positions=(
            LocalPositionView(
                position_id="pos_1",
                symbol="BTC/USDT",
                direction="long",
                quantity=Decimal("0.1"),
                state="POSITION_PROJECTED",
                claim_keys=frozenset({"pos_1"}),
                has_active_protection=False,
            ),
        )
    )

    recon = reconcile(
        snapshot,
        local,
        reconciled_at=NOW,
        exchange_position_claim_refs={"BTC/USDT:long": frozenset({"pos_1"})},
    )
    assert recon.status is ReconciliationStatus.RECOVERY_REQUIRED

    recovery = recover_pending_state(snapshot, recon, local_state=local, recovered_at=NOW)

    assert any(a.action_type is RecoveryActionType.RESUBMIT_PROTECTION for a in recovery.actions)
    assert recovery.entry_blocked is True
