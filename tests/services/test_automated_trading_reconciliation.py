"""Tests for fail-closed reconciliation.

Gate 5 requirements covered here:
- Gateway timeout blocks all Entry (red light)
- UNAVAILABLE still allows reduce-only exit
- Unclaimable external positions are quarantined, never adopted
- An empty snapshot is never read as "no problems"
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from services.automated_trading.application.reconciliation_service import (
    DiscrepancyCode,
    LocalIntentView,
    LocalPositionView,
    LocalStateView,
    ReconciliationStatus,
    reconcile,
    unavailable,
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


def _local_position(
    *,
    position_id: str = "pos_1",
    symbol: str = "BTC/USDT",
    direction: str = "long",
    quantity: str = "0.1",
    state: str = "PROTECTED",
    claim_keys: frozenset[str] = frozenset({"pos_1"}),
    has_active_protection: bool = True,
    protection_exchange_order_ids: frozenset[str] = frozenset(),
) -> LocalPositionView:
    return LocalPositionView(
        position_id=position_id,
        symbol=symbol,
        direction=direction,
        quantity=Decimal(quantity),
        state=state,
        claim_keys=claim_keys,
        has_active_protection=has_active_protection,
        protection_exchange_order_ids=protection_exchange_order_ids,
    )


def test_gateway_timeout_blocks_all_entry():
    """A missing snapshot is UNAVAILABLE and blocks Entry for every symbol."""
    result = unavailable("binance REST timeout after 3 retries", reconciled_at=NOW)

    assert result.status is ReconciliationStatus.UNAVAILABLE
    assert result.entry_allowed_globally is False
    assert result.entry_allowed_for("BTC/USDT") is False
    assert result.entry_allowed_for("ETH/USDT") is False
    assert result.unavailable_reason == "binance REST timeout after 3 retries"
    assert result.discrepancies[0].code is DiscrepancyCode.SNAPSHOT_UNAVAILABLE


def test_reconcile_with_none_snapshot_is_unavailable_not_healthy():
    """An absent snapshot must never be interpreted as a clean account."""
    result = reconcile(None, LocalStateView(), unavailable_reason="gateway missing", reconciled_at=NOW)

    assert result.status is ReconciliationStatus.UNAVAILABLE
    assert result.entry_allowed_globally is False


def test_unavailable_still_allows_reduce_only_exit():
    """Invariant 6: reconciliation anomalies never block reduce-only exit."""
    result = unavailable("gateway missing", reconciled_at=NOW)

    assert result.reduce_only_exit_allowed() is True


def test_recovery_required_still_allows_reduce_only_exit():
    """RECOVERY_REQUIRED blocks Entry but not reduce-only exit."""
    snapshot = _snapshot(positions=[_exchange_position()])
    local = LocalStateView(positions=(_local_position(has_active_protection=False),))

    result = reconcile(
        snapshot,
        local,
        reconciled_at=NOW,
        exchange_position_claim_refs={"BTC/USDT:long": frozenset({"pos_1"})},
    )

    assert result.status is ReconciliationStatus.RECOVERY_REQUIRED
    assert result.entry_allowed_for("BTC/USDT") is False
    assert result.reduce_only_exit_allowed() is True


def test_empty_snapshot_with_empty_local_is_healthy():
    """A genuinely empty account with no local state is HEALTHY."""
    result = reconcile(_snapshot(), LocalStateView(), reconciled_at=NOW)

    assert result.status is ReconciliationStatus.HEALTHY
    assert result.entry_allowed_globally is True
    assert result.discrepancies == ()


def test_confirmed_entry_fill_without_position_projection_requires_recovery():
    """A flat account is not healthy while an acknowledged V2 entry has a fill receipt.

    This is the exact containment case for a margin-guard unwind: until the
    guard exit receipt can close the lifecycle, reconciliation must keep Entry
    fail-closed instead of treating matching zero position counts as healthy.
    """
    local = LocalStateView(
        intents=(
            LocalIntentView(
                intent_id="intent-margin-guard",
                symbol="ETH/USDT",
                client_order_id="v2_entry_intent-margin-guard",
                state="EXCHANGE_ACKNOWLEDGED",
                has_confirmed_entry_fill=True,
            ),
        )
    )

    result = reconcile(_snapshot(), local, reconciled_at=NOW)

    assert result.status is ReconciliationStatus.RECOVERY_REQUIRED
    assert result.entry_allowed_globally is False
    assert "ETH/USDT" in result.entry_blocked_symbols
    assert DiscrepancyCode.CONFIRMED_ENTRY_FILL_UNPROJECTED in {
        discrepancy.code for discrepancy in result.discrepancies
    }
    assert "intent-margin-guard" in result.recovery_required_refs


def test_matched_position_with_protection_is_healthy():
    """Identity-matched, protected, quantity-consistent position reconciles clean."""
    snapshot = _snapshot(positions=[_exchange_position()])
    local = LocalStateView(positions=(_local_position(),))

    result = reconcile(
        snapshot,
        local,
        reconciled_at=NOW,
        exchange_position_claim_refs={"BTC/USDT:long": frozenset({"pos_1"})},
    )

    assert result.status is ReconciliationStatus.HEALTHY
    assert result.entry_allowed_for("BTC/USDT") is True


def test_external_position_without_identity_is_quarantined():
    """Gate 5: an unclaimable exchange position is quarantined, never adopted."""
    snapshot = _snapshot(positions=[_exchange_position()])
    local = LocalStateView(positions=())

    result = reconcile(snapshot, local, reconciled_at=NOW, exchange_position_claim_refs={})

    assert result.status is ReconciliationStatus.DEGRADED
    assert "BTC/USDT" in result.entry_blocked_symbols
    assert result.entry_allowed_for("BTC/USDT") is False
    assert result.quarantine_candidates == ("BTC/USDT:long",)
    assert result.discrepancies[0].code is DiscrepancyCode.EXTERNAL_POSITION_UNCLAIMABLE


def test_explicit_external_baseline_is_preserved_without_adoption():
    """An operator-captured baseline remains external and does not block Entry."""
    snapshot = _snapshot(positions=[_exchange_position(quantity="0.1")])
    local = LocalStateView(external_baseline_positions={"BTC/USDT:long": Decimal("0.1")})

    result = reconcile(snapshot, local, reconciled_at=NOW)

    assert result.status is ReconciliationStatus.HEALTHY
    assert result.discrepancies == ()
    assert result.entry_allowed_for("BTC/USDT") is True


def test_managed_quantity_is_checked_above_external_baseline():
    """A managed entry may add quantity, but the baseline is never attributed to it."""
    snapshot = _snapshot(positions=[_exchange_position(quantity="0.2")])
    local = LocalStateView(
        positions=(_local_position(quantity="0.1"),),
        external_baseline_positions={"BTC/USDT:long": Decimal("0.1")},
    )

    result = reconcile(
        snapshot,
        local,
        reconciled_at=NOW,
        exchange_position_claim_refs={"BTC/USDT:long": frozenset({"pos_1"})},
    )

    assert result.status is ReconciliationStatus.HEALTHY
    assert result.entry_allowed_for("BTC/USDT") is True


def test_symbol_proximity_alone_does_not_claim_position():
    """Ownership requires identity; same symbol/direction/quantity is not enough."""
    snapshot = _snapshot(positions=[_exchange_position()])
    # Local position exists with matching symbol/direction/quantity but no identity refs supplied.
    local = LocalStateView(positions=(_local_position(),))

    result = reconcile(snapshot, local, reconciled_at=NOW, exchange_position_claim_refs={})

    codes = {d.code for d in result.discrepancies}
    assert DiscrepancyCode.EXTERNAL_POSITION_UNCLAIMABLE in codes
    assert result.entry_allowed_for("BTC/USDT") is False


def test_local_position_missing_at_exchange_requires_recovery():
    """A local open position with no exchange counterpart is a ghost row."""
    local = LocalStateView(positions=(_local_position(),))

    result = reconcile(_snapshot(), local, reconciled_at=NOW)

    assert result.status is ReconciliationStatus.RECOVERY_REQUIRED
    assert result.discrepancies[0].code is DiscrepancyCode.LOCAL_POSITION_MISSING_AT_EXCHANGE
    assert "pos_1" in result.recovery_required_refs


def test_quantity_mismatch_blocks_symbol_entry():
    """Quantity disagreement degrades the symbol and blocks its Entry."""
    snapshot = _snapshot(positions=[_exchange_position(quantity="0.2")])
    local = LocalStateView(positions=(_local_position(quantity="0.1"),))

    result = reconcile(
        snapshot,
        local,
        reconciled_at=NOW,
        exchange_position_claim_refs={"BTC/USDT:long": frozenset({"pos_1"})},
    )

    codes = {d.code for d in result.discrepancies}
    assert DiscrepancyCode.QUANTITY_MISMATCH in codes
    assert result.entry_allowed_for("BTC/USDT") is False


def test_unprotected_managed_position_requires_recovery():
    """A live managed position without protection escalates to RECOVERY_REQUIRED."""
    snapshot = _snapshot(positions=[_exchange_position()])
    local = LocalStateView(positions=(_local_position(has_active_protection=False),))

    result = reconcile(
        snapshot,
        local,
        reconciled_at=NOW,
        exchange_position_claim_refs={"BTC/USDT:long": frozenset({"pos_1"})},
    )

    assert result.status is ReconciliationStatus.RECOVERY_REQUIRED
    codes = {d.code for d in result.discrepancies}
    assert DiscrepancyCode.MANAGED_POSITION_UNPROTECTED in codes


def test_protection_recorded_active_but_missing_at_exchange():
    """Locally ACTIVE protection absent from the exchange book needs recovery."""
    snapshot = _snapshot(positions=[_exchange_position()], orders=[])
    local = LocalStateView(
        positions=(
            _local_position(
                has_active_protection=True,
                protection_exchange_order_ids=frozenset({"stop_999"}),
            ),
        )
    )

    result = reconcile(
        snapshot,
        local,
        reconciled_at=NOW,
        exchange_position_claim_refs={"BTC/USDT:long": frozenset({"pos_1"})},
    )

    assert result.status is ReconciliationStatus.RECOVERY_REQUIRED
    codes = {d.code for d in result.discrepancies}
    assert DiscrepancyCode.PROTECTION_ORDER_MISSING_AT_EXCHANGE in codes


def test_orphan_v2_client_order_requires_recovery_not_resubmit():
    """A V2-shaped client id with no local record must not trigger a blind resubmit."""
    from services.automated_trading.domain.client_order_id import entry_client_order_id

    snapshot = _snapshot(
        orders=[
            ExchangeOrderSnapshot(
                exchange_order_id="ex_777",
                client_order_id=entry_client_order_id("intent-orphan"),
                symbol="ETH/USDT",
                side="buy",
                order_type="market",
                quantity=Decimal("1"),
                price=None,
                status="new",
                reduce_only=False,
            )
        ]
    )

    result = reconcile(snapshot, LocalStateView(known_client_order_ids=frozenset()), reconciled_at=NOW)

    assert result.status is ReconciliationStatus.RECOVERY_REQUIRED
    codes = {d.code for d in result.discrepancies}
    assert DiscrepancyCode.ORPHAN_V2_CLIENT_ORDER_AT_EXCHANGE in codes
    assert result.entry_allowed_for("ETH/USDT") is False


def test_known_client_order_id_is_not_flagged():
    """An exchange order we already track locally is not a discrepancy."""
    snapshot = _snapshot(
        orders=[
            ExchangeOrderSnapshot(
                exchange_order_id="ex_777",
                client_order_id="v2_entry_abc",
                symbol="ETH/USDT",
                side="buy",
                order_type="market",
                quantity=Decimal("1"),
                price=None,
                status="new",
                reduce_only=False,
            )
        ]
    )
    local = LocalStateView(known_client_order_ids=frozenset({"v2_entry_abc"}))

    result = reconcile(snapshot, local, reconciled_at=NOW)

    assert result.status is ReconciliationStatus.HEALTHY


def test_order_without_client_id_requires_recovery():
    """An exchange order with no client id cannot be attributed automatically."""
    snapshot = _snapshot(
        orders=[
            ExchangeOrderSnapshot(
                exchange_order_id="ex_888",
                client_order_id=None,
                symbol="BTC/USDT",
                side="sell",
                order_type="stop_market",
                quantity=Decimal("0.1"),
                price=Decimal("48000"),
                status="new",
                reduce_only=True,
            )
        ]
    )

    result = reconcile(snapshot, LocalStateView(), reconciled_at=NOW)

    assert result.status is ReconciliationStatus.RECOVERY_REQUIRED
    codes = {d.code for d in result.discrepancies}
    assert DiscrepancyCode.UNKNOWN_ORDER_PRESENT in codes


def test_degraded_blocks_only_affected_symbol():
    """DEGRADED blocks the affected symbol while leaving others tradable."""
    snapshot = _snapshot(positions=[_exchange_position(symbol="BTC/USDT")])

    result = reconcile(snapshot, LocalStateView(), reconciled_at=NOW, exchange_position_claim_refs={})

    assert result.status is ReconciliationStatus.DEGRADED
    assert result.entry_allowed_for("BTC/USDT") is False
    assert result.entry_allowed_for("ETH/USDT") is True


def test_result_is_immutable():
    """ReconciliationResult is frozen."""
    import pytest

    result = reconcile(_snapshot(), LocalStateView(), reconciled_at=NOW)

    with pytest.raises(AttributeError):
        result.status = ReconciliationStatus.HEALTHY  # type: ignore[misc]
