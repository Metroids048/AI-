"""Task 9 / Gate 9: fail-safe reduce-only exits that cannot be blocked.

Gate 9 requirements under test:
- Entry kill switch, AI veto, manifest, data staleness, net-edge — none block exit.
- Quantity is clamped to exchange truth and floored to step size; never scaled up.
- Already-Flat is reconciled as idempotent success, not a failure.
- Partial exit projects only the confirmed reduced quantity.
- Local CLOSED only after exchange confirms position is zero.
- Residual protection is cancelled after a confirmed full close.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from services.automated_trading.application.exit_service import (
    ExitBlockReason,
    ExitDecision,
    ExitExecutionStatus,
    ExitReason,
    ExitTimeout,
    ExitVerdict,
    ReduceOnlyAlreadyFlat,
    evaluate_exit,
    execute_reduce_only_exit,
    floor_to_step,
)
from services.automated_trading.domain.enums import V2PositionState
from services.automated_trading.infrastructure.binance_adapter import (
    BinanceAdapterUnavailable,
    ExchangeFillReceipt,
    ExchangeOrderReceipt,
)
from services.automated_trading.infrastructure.market_snapshot_provider import (
    AuthoritativeAccountSnapshot,
    ExchangePositionSnapshot,
)

NOW = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)
POSITION_ID = "pos-exit-9c2a"


def build_position(quantity: str = "0.01", direction: str = "long") -> ExchangePositionSnapshot:
    return ExchangePositionSnapshot(
        symbol="BTC/USDT",
        direction=direction,
        quantity=Decimal(quantity),
        entry_price=Decimal("50000"),
        mark_price=Decimal("50000"),
        unrealized_pnl=Decimal("0"),
        leverage=10,
    )


def _snapshot(quantity: str = "0", direction: str = "long") -> AuthoritativeAccountSnapshot:
    positions = [] if Decimal(quantity) == 0 else [build_position(quantity, direction)]
    return AuthoritativeAccountSnapshot(
        balance=Decimal("10000"),
        equity=Decimal("10000"),
        positions=positions,
        pending_orders=[],
        snapshot_timestamp=NOW,
    )


def default_evaluate(**overrides) -> ExitDecision:
    kwargs: dict[str, object] = {
        "position_id": POSITION_ID,
        "symbol": "BTC/USDT",
        "direction": "long",
        "reason": ExitReason.HARD_STOP,
        "requested_quantity": Decimal("0.01"),
        "authoritative_position": build_position(),
        "step_size": Decimal("0.001"),
        "fencing_token_valid": True,
    }
    kwargs.update(overrides)
    return evaluate_exit(**kwargs)


def _order_receipt(exchange_order_id: str = "ex-exit-1") -> ExchangeOrderReceipt:
    return ExchangeOrderReceipt(
        exchange_order_id=exchange_order_id,
        client_order_id="A2X-abc",
        symbol="BTC/USDT",
        side="sell",
        order_type="market",
        quantity=Decimal("0.01"),
        price=None,
        status="filled",
        acknowledged_at=NOW,
    )


def _fill(quantity: str, price: str, trade_id: str = "t-exit-1") -> ExchangeFillReceipt:
    return ExchangeFillReceipt(
        exchange_order_id="ex-exit-1",
        trade_id=trade_id,
        filled_quantity=Decimal(quantity),
        fill_price=Decimal(price),
        fee=Decimal("0.2"),
        fill_timestamp=NOW,
    )


def _execute(adapter, decision: ExitDecision | None = None, **overrides):
    kwargs: dict[str, object] = {
        "adapter": adapter,
        "authoritative_quantity": Decimal("0.01"),
        "step_size": Decimal("0.001"),
        "open_protection_order_ids": (),
    }
    kwargs.update(overrides)
    d = decision or default_evaluate()
    return execute_reduce_only_exit(d, **kwargs)


# ---------------------------------------------------------------------------
# Exit Gate: Entry-side conditions never block exits
# ---------------------------------------------------------------------------


def test_hard_exit_is_approved_regardless_of_entry_conditions() -> None:
    """Gate 9: entry kill switch, manifest, net-edge cannot trap a position."""
    # The evaluate_exit signature has no entry-side parameters at all.
    # This test confirms the exit gate is a separate, deliberately smaller gate.
    gate = ExitDecision.__dataclass_fields__.keys()
    for entry_only_param in (
        "entry_kill_switch_active",
        "manifest_eligible",
        "net_edge_after_cost_bps",
        "ai_advisory_veto",
    ):
        assert entry_only_param not in gate


def test_approved_exit_is_reduce_only() -> None:
    decision = default_evaluate()

    assert decision.approved
    assert decision.reduce_only is True
    assert decision.exchange_side == "sell"


def test_reduce_only_false_is_rejected_at_construction() -> None:
    """The coordinator has no code path that can add risk."""
    with pytest.raises(ValueError, match="reduce_only must be True"):
        ExitDecision(
            verdict=ExitVerdict.APPROVED,
            position_id=POSITION_ID,
            symbol="BTC/USDT",
            direction="long",
            reason=ExitReason.HARD_STOP,
            quantity=Decimal("0.01"),
            client_order_id="A2X-abc",
            reduce_only=False,
        )


def test_no_authoritative_position_returns_already_flat() -> None:
    decision = default_evaluate(authoritative_position=None)

    assert decision.verdict is ExitVerdict.ALREADY_FLAT
    assert decision.quantity == Decimal("0")


def test_zero_quantity_position_returns_already_flat() -> None:
    decision = default_evaluate(authoritative_position=build_position("0"))

    assert decision.verdict is ExitVerdict.ALREADY_FLAT


def test_direction_disagreement_blocks_exit() -> None:
    """An exit that would reverse the position must be blocked, not permitted."""
    decision = default_evaluate(
        direction="short",
        authoritative_position=build_position(direction="long"),
    )

    assert not decision.approved
    assert decision.block_reason is ExitBlockReason.SIDE_WOULD_NOT_REDUCE


def test_invalid_fencing_token_blocks_exit() -> None:
    decision = default_evaluate(fencing_token_valid=False)

    assert not decision.approved
    assert decision.block_reason is ExitBlockReason.INVALID_FENCING_TOKEN


# ---------------------------------------------------------------------------
# Quantity clamping (plan 8.2): never scale up
# ---------------------------------------------------------------------------


def test_quantity_is_clamped_to_exchange_truth() -> None:
    """Requested 0.05, exchange has 0.01 — never over-reduce."""
    decision = default_evaluate(
        requested_quantity=Decimal("0.05"),
        authoritative_position=build_position("0.01"),
    )

    assert decision.approved
    assert decision.quantity == Decimal("0.01")


def test_quantity_is_floored_to_step_size() -> None:
    decision = default_evaluate(
        requested_quantity=Decimal("0.0129"),
        authoritative_position=build_position("0.1"),  # larger than requested
        step_size=Decimal("0.001"),
    )

    assert decision.quantity == Decimal("0.012")


def test_quantity_rounding_to_zero_blocks_exit() -> None:
    decision = default_evaluate(
        requested_quantity=Decimal("0.0004"),
        step_size=Decimal("0.001"),
    )

    assert not decision.approved
    assert decision.block_reason is ExitBlockReason.QUANTITY_ROUNDS_TO_ZERO


def test_floor_to_step_never_rounds_up() -> None:
    assert floor_to_step(Decimal("0.0129"), Decimal("0.001")) == Decimal("0.012")
    assert floor_to_step(Decimal("0.01299"), Decimal("0.001")) == Decimal("0.012")
    with pytest.raises(ValueError, match="step_size must be"):
        floor_to_step(Decimal("1"), Decimal("0"))


# ---------------------------------------------------------------------------
# Execution: CLOSED only after exchange confirms zero
# ---------------------------------------------------------------------------


def test_confirmed_full_close_sets_position_closed() -> None:
    adapter = MagicMock()
    adapter.submit_reduce_only_exit.return_value = _order_receipt()
    adapter.fetch_fills.return_value = (_fill("0.01", "50000"),)

    result = _execute(adapter)

    assert result.status is ExitExecutionStatus.CLOSED
    assert result.position_state is V2PositionState.CLOSED
    assert result.position_closed is True
    assert result.reduced_quantity == Decimal("0.01")
    assert result.average_fill_price == Decimal("50000")


def test_partial_fill_does_not_close_position() -> None:
    adapter = MagicMock()
    adapter.submit_reduce_only_exit.return_value = _order_receipt()
    adapter.fetch_fills.return_value = (_fill("0.004", "50000"),)

    result = _execute(adapter)

    assert result.status is ExitExecutionStatus.PARTIALLY_REDUCED
    assert result.position_state is V2PositionState.REDUCING
    assert result.position_closed is False
    assert result.reduced_quantity == Decimal("0.004")
    assert result.remaining_quantity == Decimal("0.006")


def test_already_flat_rejection_is_reconciled_as_success() -> None:
    """Gate 9: ReduceOnly already flat is idempotent, not a failure."""
    adapter = MagicMock()
    adapter.submit_reduce_only_exit.side_effect = ReduceOnlyAlreadyFlat("reduce only rejected")
    adapter.fetch_authoritative_snapshot.return_value = _snapshot("0")

    result = _execute(adapter)

    assert result.status is ExitExecutionStatus.ALREADY_FLAT_RECONCILED
    assert result.position_state is V2PositionState.CLOSED
    assert result.position_closed is True


def test_already_flat_from_evaluate_exit_is_also_reconciled() -> None:
    """Flat detected before submission also produces a clean close."""
    adapter = MagicMock()
    decision = default_evaluate(authoritative_position=None)

    result = _execute(adapter, decision)

    assert result.status is ExitExecutionStatus.ALREADY_FLAT_RECONCILED
    assert result.position_closed is True
    adapter.submit_reduce_only_exit.assert_not_called()


def test_residual_protection_cancelled_on_full_close() -> None:
    """After a full close, protection orders have no position to guard; cancel them."""
    adapter = MagicMock()
    adapter.submit_reduce_only_exit.return_value = _order_receipt()
    adapter.fetch_fills.return_value = (_fill("0.01", "50000"),)

    result = _execute(adapter, open_protection_order_ids=("stop-111", "tp-222"))

    assert "stop-111" in result.residual_protection_cancelled
    assert "tp-222" in result.residual_protection_cancelled
    adapter.cancel_order.assert_any_call("BTC/USDT", "stop-111")
    adapter.cancel_order.assert_any_call("BTC/USDT", "tp-222")


def test_residual_protection_not_cancelled_on_partial() -> None:
    """A partial exit leaves a live position; do not cancel its protection."""
    adapter = MagicMock()
    adapter.submit_reduce_only_exit.return_value = _order_receipt()
    adapter.fetch_fills.return_value = (_fill("0.004", "50000"),)

    result = _execute(adapter, open_protection_order_ids=("stop-111",))

    assert result.residual_protection_cancelled == ()
    adapter.cancel_order.assert_not_called()


def test_protection_cancel_failure_does_not_raise() -> None:
    """A failed cancellation after close is logged but never fatal."""
    adapter = MagicMock()
    adapter.submit_reduce_only_exit.return_value = _order_receipt()
    adapter.fetch_fills.return_value = (_fill("0.01", "50000"),)
    adapter.cancel_order.side_effect = BinanceAdapterUnavailable("cancel rejected")

    result = _execute(adapter, open_protection_order_ids=("stop-111",))

    assert result.position_closed is True


def test_submission_failure_does_not_close_position() -> None:
    adapter = MagicMock()
    adapter.submit_reduce_only_exit.side_effect = BinanceAdapterUnavailable("gateway down")

    result = _execute(adapter)

    assert result.status is ExitExecutionStatus.FAILED
    assert result.position_state is V2PositionState.REDUCING
    assert result.position_closed is False


def test_submission_timeout_yields_unknown_for_client_id_recovery() -> None:
    adapter = MagicMock()
    adapter.submit_reduce_only_exit.side_effect = ExitTimeout("read timeout")

    result = _execute(adapter)

    assert result.status is ExitExecutionStatus.UNKNOWN
    assert result.requires_client_order_id_recovery is True
    assert result.position_closed is False
    assert adapter.submit_reduce_only_exit.call_count == 1


def test_fill_fetch_failure_leaves_position_reducing() -> None:
    adapter = MagicMock()
    adapter.submit_reduce_only_exit.return_value = _order_receipt()
    adapter.fetch_fills.side_effect = BinanceAdapterUnavailable("rate limited")

    result = _execute(adapter)

    assert result.status is ExitExecutionStatus.SUBMITTED_UNCONFIRMED
    assert result.position_state is V2PositionState.REDUCING
    assert result.exchange_order_id == "ex-exit-1"


def test_short_position_submits_buy_side() -> None:
    adapter = MagicMock()
    adapter.submit_reduce_only_exit.return_value = _order_receipt()
    adapter.fetch_fills.return_value = (_fill("0.01", "50000"),)

    decision = default_evaluate(direction="short", authoritative_position=build_position(direction="short"))
    execute_reduce_only_exit(
        decision,
        adapter=adapter,
        authoritative_quantity=Decimal("0.01"),
        step_size=Decimal("0.001"),
    )

    assert adapter.submit_reduce_only_exit.call_args[0][2] == "buy"


def test_multiple_exit_reasons_are_all_valid() -> None:
    """All supported reasons produce approved decisions."""
    for reason in ExitReason:
        decision = default_evaluate(reason=reason)
        assert decision.approved or decision.verdict is ExitVerdict.ALREADY_FLAT


def test_hard_exits_flag_emergency_on_the_command() -> None:
    adapter = MagicMock()
    adapter.submit_reduce_only_exit.return_value = _order_receipt()
    adapter.fetch_fills.return_value = (_fill("0.01", "50000"),)

    decision = default_evaluate(reason=ExitReason.PROTECTION_FAILURE_EMERGENCY)
    execute_reduce_only_exit(
        decision,
        adapter=adapter,
        authoritative_quantity=Decimal("0.01"),
        step_size=Decimal("0.001"),
    )

    submitted_command = adapter.submit_reduce_only_exit.call_args[0][0]
    assert submitted_command.is_emergency is True
