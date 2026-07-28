"""Task 8 / Gate 8: every exchange-confirmed position gets protected.

Gate 8 requirements under test:
- Protection prices are recomputed from the real fill price.
- Tick rounding moves prices in the risk-safer direction.
- No exchange order id means protection can never be ACTIVE.
- A failed protection submission escalates to emergency reduce-only close.
- Protection *and* emergency close both failing blocks Entry account-wide.
- A simultaneous Stop/TP race resolves against exchange truth.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from services.automated_trading.application.protection_service import (
    ProtectionFailureAction,
    ProtectionOutcome,
    ProtectionPlan,
    ProtectionSubmissionError,
    ProtectionTimeout,
    build_protection_plan,
    ensure_protection,
    round_to_tick,
)
from services.automated_trading.domain.candidates import (
    CandidateLane,
    CandidateSide,
    TradeCandidate,
)
from services.automated_trading.domain.enums import V2CandidateType, V2ProtectionState
from services.automated_trading.infrastructure.binance_adapter import (
    BinanceAdapterUnavailable,
    ExchangeOrderReceipt,
)
from services.automated_trading.infrastructure.market_snapshot_provider import (
    AuthoritativeAccountSnapshot,
    ExchangePositionSnapshot,
)

NOW = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)
POSITION_ID = "pos-8ab3"


def build_candidate(side: CandidateSide = CandidateSide.LONG, **overrides) -> TradeCandidate:
    defaults: dict[str, object] = {
        "candidate_id": "cand-1",
        "cycle_id": "cycle-1",
        "strategy_id": "testnet_sampling_v2",
        "strategy_version": "1.0.0",
        "lane": CandidateLane.TESTNET_SAMPLING,
        "candidate_type": V2CandidateType.SAMPLING,
        "symbol": "BTC/USDT",
        "side": side,
        "signal_candle_close_time": NOW - timedelta(minutes=15),
        "signal_reference_price": Decimal("50000"),
        "confidence": Decimal("0.55"),
        "stop_distance": Decimal("500"),
        "take_profit_distance": Decimal("750"),
        "max_entry_drift_bps": Decimal("20"),
        "expires_at": NOW + timedelta(seconds=65),
        "non_promotable": True,
    }
    defaults.update(overrides)
    return TradeCandidate(**defaults)


def build_plan(direction: str = "long", **overrides) -> ProtectionPlan:
    long_side = direction == "long"
    defaults: dict[str, object] = {
        "position_id": POSITION_ID,
        "symbol": "BTC/USDT",
        "direction": direction,
        "quantity": Decimal("0.01"),
        "average_fill_price": Decimal("50000"),
        "stop_price": Decimal("49500") if long_side else Decimal("50500"),
        "take_profit_price": Decimal("50750") if long_side else Decimal("49250"),
        "stop_client_order_id": "A2S-abc123def456789012",
        "tp_client_order_id": "A2T-abc123def456789012",
        "attempt": 1,
    }
    defaults.update(overrides)
    return ProtectionPlan(**defaults)


def _snapshot(positions: list[ExchangePositionSnapshot]) -> AuthoritativeAccountSnapshot:
    return AuthoritativeAccountSnapshot(
        balance=Decimal("10000"),
        equity=Decimal("10000"),
        positions=positions,
        pending_orders=[],
        snapshot_timestamp=NOW,
    )


def _position(quantity: str, symbol: str = "BTC/USDT", direction: str = "long") -> ExchangePositionSnapshot:
    return ExchangePositionSnapshot(
        symbol=symbol,
        direction=direction,
        quantity=Decimal(quantity),
        entry_price=Decimal("50000"),
        mark_price=Decimal("50000"),
        unrealized_pnl=Decimal("0"),
        leverage=10,
    )


def _receipt(exchange_order_id: str | None, order_type: str = "stop_market") -> ExchangeOrderReceipt:
    return ExchangeOrderReceipt(
        exchange_order_id=exchange_order_id or "",
        client_order_id="A2S-abc123def456789012",
        symbol="BTC/USDT",
        side="sell",
        order_type=order_type,
        quantity=Decimal("0.01"),
        price=Decimal("49500"),
        status="new",
        acknowledged_at=NOW,
    )


# ---------------------------------------------------------------------------
# 7.1 Price source: real fill price
# ---------------------------------------------------------------------------


def test_protection_prices_are_derived_from_actual_fill_price() -> None:
    """The reference price is 50000 but the fill was 50120; protection follows the fill."""
    candidate = build_candidate()

    plan = build_protection_plan(
        position_id=POSITION_ID,
        candidate=candidate,
        average_fill_price=Decimal("50120"),
        filled_quantity=Decimal("0.01"),
        tick_size=Decimal("0.01"),
    )

    assert plan.stop_price == Decimal("49620.00")  # 50120 - 500
    assert plan.take_profit_price == Decimal("50870.00")  # 50120 + 750
    assert plan.average_fill_price == Decimal("50120")


def test_short_protection_geometry_is_inverted() -> None:
    plan = build_protection_plan(
        position_id=POSITION_ID,
        candidate=build_candidate(side=CandidateSide.SHORT),
        average_fill_price=Decimal("50000"),
        filled_quantity=Decimal("0.01"),
        tick_size=Decimal("0.01"),
    )

    assert plan.stop_price == Decimal("50500.00")
    assert plan.take_profit_price == Decimal("49250.00")
    assert plan.protection_side == "buy"


def test_plan_uses_confirmed_quantity_not_requested() -> None:
    plan = build_protection_plan(
        position_id=POSITION_ID,
        candidate=build_candidate(),
        average_fill_price=Decimal("50000"),
        filled_quantity=Decimal("0.004"),
        tick_size=Decimal("0.01"),
    )

    assert plan.quantity == Decimal("0.004")


def test_zero_fill_price_or_quantity_is_rejected() -> None:
    with pytest.raises(ValueError, match="average_fill_price must be"):
        build_protection_plan(
            position_id=POSITION_ID,
            candidate=build_candidate(),
            average_fill_price=Decimal("0"),
            filled_quantity=Decimal("0.01"),
            tick_size=Decimal("0.01"),
        )
    with pytest.raises(ValueError, match="filled_quantity must be"):
        build_protection_plan(
            position_id=POSITION_ID,
            candidate=build_candidate(),
            average_fill_price=Decimal("50000"),
            filled_quantity=Decimal("0"),
            tick_size=Decimal("0.01"),
        )


# ---------------------------------------------------------------------------
# 7.2 Tick rounding in the risk-safer direction
# ---------------------------------------------------------------------------


def test_long_stop_rounds_up_toward_entry() -> None:
    """Rounding a long stop down would widen real risk past the authorized distance."""
    assert round_to_tick(Decimal("49500.123"), Decimal("0.1"), direction="long", leg="stop") == Decimal("49500.2")


def test_short_stop_rounds_down_toward_entry() -> None:
    assert round_to_tick(Decimal("50500.187"), Decimal("0.1"), direction="short", leg="stop") == Decimal("50500.1")


def test_long_target_rounds_down_taking_profit_sooner() -> None:
    assert round_to_tick(Decimal("50750.187"), Decimal("0.1"), direction="long", leg="target") == Decimal("50750.1")


def test_short_target_rounds_up_taking_profit_sooner() -> None:
    assert round_to_tick(Decimal("49250.123"), Decimal("0.1"), direction="short", leg="target") == Decimal("49250.2")


def test_tick_rounding_rejects_invalid_arguments() -> None:
    with pytest.raises(ValueError, match="tick_size must be"):
        round_to_tick(Decimal("100"), Decimal("0"), direction="long", leg="stop")
    with pytest.raises(ValueError, match="leg must be"):
        round_to_tick(Decimal("100"), Decimal("0.1"), direction="long", leg="trailing")


def test_coarse_tick_rounding_keeps_stop_on_the_safe_side() -> None:
    plan = build_protection_plan(
        position_id=POSITION_ID,
        candidate=build_candidate(),
        average_fill_price=Decimal("50120"),
        filled_quantity=Decimal("0.01"),
        tick_size=Decimal("10"),
    )

    # 49620 -> rounds up to 49620 (already on tick); stop stays below fill.
    assert plan.stop_price < plan.average_fill_price
    assert plan.stop_price % Decimal("10") == 0


def test_invalid_geometry_is_rejected() -> None:
    """A stop on the wrong side of entry is an instant loss, not a stop."""
    with pytest.raises(ValueError, match="long stop .* must be below fill"):
        build_plan(stop_price=Decimal("50500"))
    with pytest.raises(ValueError, match="long take-profit .* must be above fill"):
        build_plan(take_profit_price=Decimal("49000"))
    with pytest.raises(ValueError, match="short stop .* must be above fill"):
        build_plan(direction="short", stop_price=Decimal("49500"))


# ---------------------------------------------------------------------------
# 7.3 ACTIVE requires an exchange order id
# ---------------------------------------------------------------------------


def test_protection_is_active_only_with_exchange_order_id() -> None:
    adapter = MagicMock()
    adapter.submit_protection.return_value = (_receipt("stop-999"), _receipt("tp-111", "take_profit_market"))

    result = ensure_protection(build_plan(), adapter=adapter)

    assert result.outcome is ProtectionOutcome.ACTIVE
    assert result.state is V2ProtectionState.PROTECTION_ACTIVE
    assert result.stop_exchange_order_id == "stop-999"
    assert result.is_active is True


def test_missing_exchange_order_id_cannot_be_active() -> None:
    """Gate 8: no exchange order id means protection is not ACTIVE."""
    adapter = MagicMock()
    adapter.submit_protection.return_value = (_receipt(None), None)
    adapter.fetch_authoritative_snapshot.return_value = _snapshot([])

    result = ensure_protection(build_plan(), adapter=adapter)

    assert result.state is not V2ProtectionState.PROTECTION_ACTIVE
    assert result.is_active is False


def test_stop_only_plan_reports_no_tp_order() -> None:
    adapter = MagicMock()
    adapter.submit_protection.return_value = (_receipt("stop-999"), None)

    result = ensure_protection(build_plan(take_profit_price=None, tp_client_order_id=None), adapter=adapter)

    assert result.is_active is True
    assert result.tp_exchange_order_id is None


# ---------------------------------------------------------------------------
# 7.4 Failure escalation
# ---------------------------------------------------------------------------


def test_first_failure_retries_once_under_new_attempt_number() -> None:
    adapter = MagicMock()
    adapter.submit_protection.side_effect = [
        ProtectionSubmissionError("-2021 order would immediately trigger"),
        (_receipt("stop-999"), None),
    ]
    adapter.fetch_authoritative_snapshot.return_value = _snapshot([_position("0.01")])

    result = ensure_protection(build_plan(), adapter=adapter)

    assert result.outcome is ProtectionOutcome.ACTIVE
    assert ProtectionFailureAction.RETRIED_SUBMISSION in result.actions
    assert adapter.submit_protection.call_count == 2
    # The retry must use a different client order id revision.
    first_command = adapter.submit_protection.call_args_list[0][0][0]
    second_command = adapter.submit_protection.call_args_list[1][0][0]
    assert first_command.stop_client_order_id != second_command.stop_client_order_id


def test_retry_uses_true_exchange_quantity() -> None:
    """A partial close between attempts must shrink the protected quantity."""
    adapter = MagicMock()
    adapter.submit_protection.side_effect = [
        ProtectionSubmissionError("transient"),
        (_receipt("stop-999"), None),
    ]
    adapter.fetch_authoritative_snapshot.return_value = _snapshot([_position("0.006")])

    ensure_protection(build_plan(), adapter=adapter)

    assert adapter.submit_protection.call_args_list[1][0][3] == Decimal("0.006")


def test_repeated_failure_triggers_emergency_reduce_only_close() -> None:
    """Gate 8: protection that cannot be established forces an emergency close."""
    adapter = MagicMock()
    adapter.submit_protection.side_effect = ProtectionSubmissionError("rejected")
    adapter.fetch_authoritative_snapshot.side_effect = [
        _snapshot([_position("0.01")]),  # after first failure
        _snapshot([_position("0.01")]),  # after second failure
        _snapshot([]),  # after emergency close: flat
    ]

    result = ensure_protection(build_plan(), adapter=adapter)

    assert result.outcome is ProtectionOutcome.EMERGENCY_CLOSED
    assert ProtectionFailureAction.EMERGENCY_REDUCE_ONLY_CLOSE in result.actions
    adapter.submit_reduce_only_exit.assert_called_once()
    assert adapter.submit_reduce_only_exit.call_args[0][0].is_emergency is True


def test_protection_and_emergency_close_both_failing_blocks_entry_account_wide() -> None:
    """Gate 8: the only correct end state is EMERGENCY_CLOSE_PENDING + Entry block."""
    adapter = MagicMock()
    adapter.submit_protection.side_effect = ProtectionSubmissionError("rejected")
    adapter.submit_reduce_only_exit.side_effect = BinanceAdapterUnavailable("gateway down")
    adapter.fetch_authoritative_snapshot.return_value = _snapshot([_position("0.01")])

    result = ensure_protection(build_plan(), adapter=adapter)

    assert result.outcome is ProtectionOutcome.EMERGENCY_CLOSE_PENDING
    assert result.state is V2ProtectionState.PROTECTION_FAILED
    assert result.account_entry_blocked is True
    assert result.requires_manual_intervention is True
    assert ProtectionFailureAction.ACCOUNT_ENTRY_BLOCK in result.actions
    assert ProtectionFailureAction.HIGH_PRIORITY_ALERT in result.actions


def test_position_still_open_after_emergency_close_is_pending() -> None:
    adapter = MagicMock()
    adapter.submit_protection.side_effect = ProtectionSubmissionError("rejected")
    adapter.fetch_authoritative_snapshot.return_value = _snapshot([_position("0.01")])

    result = ensure_protection(build_plan(), adapter=adapter)

    assert result.outcome is ProtectionOutcome.EMERGENCY_CLOSE_PENDING
    assert result.account_entry_blocked is True
    assert "still open" in result.detail


def test_protection_timeout_is_unknown_not_resubmitted() -> None:
    adapter = MagicMock()
    adapter.submit_protection.side_effect = ProtectionTimeout("read timeout")

    result = ensure_protection(build_plan(), adapter=adapter)

    assert result.outcome is ProtectionOutcome.UNKNOWN
    assert result.state is V2ProtectionState.PROTECTION_UNKNOWN
    assert adapter.submit_protection.call_count == 1
    assert "client order id" in result.detail


def test_position_closed_underneath_needs_no_protection() -> None:
    adapter = MagicMock()
    adapter.submit_protection.side_effect = ProtectionSubmissionError("-2022 reduce only rejected")
    adapter.fetch_authoritative_snapshot.return_value = _snapshot([])

    result = ensure_protection(build_plan(), adapter=adapter)

    assert result.outcome is ProtectionOutcome.ALREADY_FLAT
    assert result.state is V2ProtectionState.PROTECTION_CANCELLED
    adapter.submit_reduce_only_exit.assert_not_called()


def test_unreadable_position_after_failure_escalates_rather_than_assuming_flat() -> None:
    """A failed position read must never be treated as 'no exposure'."""
    adapter = MagicMock()
    adapter.submit_protection.side_effect = ProtectionSubmissionError("rejected")
    adapter.fetch_authoritative_snapshot.side_effect = BinanceAdapterUnavailable("timeout")

    result = ensure_protection(build_plan(), adapter=adapter)

    assert result.outcome is ProtectionOutcome.EMERGENCY_CLOSE_PENDING
    assert result.account_entry_blocked is True


# ---------------------------------------------------------------------------
# 7.5 Stop/TP race
# ---------------------------------------------------------------------------


def test_simultaneous_stop_and_tp_resolve_against_exchange_truth() -> None:
    """When both protection legs fire, exchange position is the only authority."""
    adapter = MagicMock()
    adapter.submit_protection.side_effect = ProtectionSubmissionError("both legs already triggered")
    # Exchange reports flat: the race already resolved, no reverse position may be created.
    adapter.fetch_authoritative_snapshot.return_value = _snapshot([])

    result = ensure_protection(build_plan(), adapter=adapter)

    assert result.outcome is ProtectionOutcome.ALREADY_FLAT
    adapter.submit_reduce_only_exit.assert_not_called()


def test_partial_close_during_race_protects_only_remaining_quantity() -> None:
    adapter = MagicMock()
    adapter.submit_protection.side_effect = [
        ProtectionSubmissionError("stop leg triggered mid-submit"),
        (_receipt("stop-new"), None),
    ]
    adapter.fetch_authoritative_snapshot.return_value = _snapshot([_position("0.003")])

    result = ensure_protection(build_plan(quantity=Decimal("0.01")), adapter=adapter)

    assert result.outcome is ProtectionOutcome.ACTIVE
    assert adapter.submit_protection.call_args_list[1][0][3] == Decimal("0.003")


def test_no_exception_is_silently_suppressed() -> None:
    """Every failure path returns a persisted outcome carrying the cause."""
    adapter = MagicMock()
    adapter.submit_protection.side_effect = ProtectionSubmissionError("binance -4131")
    adapter.fetch_authoritative_snapshot.return_value = _snapshot([_position("0.01")])

    result = ensure_protection(build_plan(), adapter=adapter)

    assert "binance -4131" in result.detail
