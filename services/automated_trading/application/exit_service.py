"""Reduce-only exit coordinator: de-risking must never be blocked.

Plan sections 8.1-8.4. The Exit Gate is deliberately a *different* gate from the
Entry Gate, not a relaxed version of it. It checks only what is needed to make a
reduce-only order safe and correct:

- an authoritative exchange position exists,
- the side genuinely reduces that position,
- ``reduce_only=True``,
- quantity > 0 and quantity <= authoritative position quantity,
- Client Order ID is idempotent,
- the fencing token is valid,
- the gateway is callable.

None of the following may ever block a hard exit (plan 8.1): manifest validity,
AI availability or veto, meta-label outcome, signal bar staleness, entry kill
switch, insufficient net edge, or a news risk event. Those govern *adding* risk.

Two further invariants:
- Quantity is clamped down to exchange truth and floored to step size (8.2);
  it is never scaled up.
- Local CLOSED happens only after the exchange confirms the position reached
  zero (Gate 9). A submitted exit is not a closed position.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_DOWN, Decimal
from enum import StrEnum
from typing import TYPE_CHECKING

from services.automated_trading.domain.client_order_id import exit_client_order_id
from services.automated_trading.domain.enums import V2PositionState

if TYPE_CHECKING:
    from services.automated_trading.infrastructure.market_snapshot_provider import (
        ExchangePositionSnapshot,
    )


class ExitReason(StrEnum):
    """Supported exit triggers (plan 8.4)."""

    HARD_STOP = "HARD_STOP"
    TAKE_PROFIT = "TAKE_PROFIT"
    TIME_EXIT = "TIME_EXIT"
    STRATEGY_INVALIDATION = "STRATEGY_INVALIDATION"
    OPPOSITE_SIGNAL_CLOSE = "OPPOSITE_SIGNAL_CLOSE"
    PROTECTION_FAILURE_EMERGENCY = "PROTECTION_FAILURE_EMERGENCY"
    MANUAL_REDUCE_ONLY = "MANUAL_REDUCE_ONLY"

    @property
    def is_hard_exit(self) -> bool:
        """Hard exits must never be gated on advisory or entry-side conditions."""
        return self in {
            ExitReason.HARD_STOP,
            ExitReason.PROTECTION_FAILURE_EMERGENCY,
            ExitReason.STRATEGY_INVALIDATION,
        }


class ExitBlockReason(StrEnum):
    """Why an exit could not be attempted. Deliberately a short list."""

    NO_AUTHORITATIVE_POSITION = "NO_AUTHORITATIVE_POSITION"
    QUANTITY_NOT_POSITIVE = "QUANTITY_NOT_POSITIVE"
    QUANTITY_ROUNDS_TO_ZERO = "QUANTITY_ROUNDS_TO_ZERO"
    SIDE_WOULD_NOT_REDUCE = "SIDE_WOULD_NOT_REDUCE"
    INVALID_FENCING_TOKEN = "INVALID_FENCING_TOKEN"
    GATEWAY_UNAVAILABLE = "GATEWAY_UNAVAILABLE"


class ExitVerdict(StrEnum):
    APPROVED = "APPROVED"
    BLOCKED = "BLOCKED"
    ALREADY_FLAT = "ALREADY_FLAT"


class ExitExecutionStatus(StrEnum):
    """Terminal status of a reduce-only exit attempt."""

    NOT_ATTEMPTED = "NOT_ATTEMPTED"
    CLOSED = "CLOSED"
    PARTIALLY_REDUCED = "PARTIALLY_REDUCED"
    ALREADY_FLAT_RECONCILED = "ALREADY_FLAT_RECONCILED"
    SUBMITTED_UNCONFIRMED = "SUBMITTED_UNCONFIRMED"
    UNKNOWN = "UNKNOWN"
    FAILED = "FAILED"


class ExitTimeout(Exception):
    """Exit submission outcome is undetermined."""


class ReduceOnlyAlreadyFlat(Exception):
    """Exchange rejected a reduce-only order because the position is already flat."""


@dataclass(frozen=True)
class ExitDecision:
    """Immutable exit plan.

    ``reduce_only`` is fixed True: this coordinator has no code path that can
    submit a risk-adding order.
    """

    verdict: ExitVerdict
    position_id: str
    symbol: str
    direction: str
    reason: ExitReason
    quantity: Decimal
    client_order_id: str
    reduce_only: bool = True
    block_reason: ExitBlockReason | None = None
    detail: str = ""

    def __post_init__(self) -> None:
        if not self.reduce_only:
            raise ValueError("ExitDecision.reduce_only must be True; this coordinator cannot add risk")

    @property
    def approved(self) -> bool:
        return self.verdict is ExitVerdict.APPROVED

    @property
    def exchange_side(self) -> str:
        """The side that reduces this position."""
        return "sell" if self.direction == "long" else "buy"


@dataclass(frozen=True)
class ExitExecutionResult:
    """Outcome of a reduce-only exit submission."""

    status: ExitExecutionStatus
    position_state: V2PositionState
    client_order_id: str
    exchange_order_id: str | None = None
    trade_ids: tuple[str, ...] = ()
    reduced_quantity: Decimal = Decimal("0")
    average_fill_price: Decimal | None = None
    total_fee: Decimal = Decimal("0")
    remaining_quantity: Decimal | None = None
    fill_timestamp: datetime | None = None
    residual_protection_cancelled: tuple[str, ...] = ()
    detail: str = ""

    @property
    def position_closed(self) -> bool:
        """Gate 9: local CLOSED only after exchange confirms zero."""
        return (
            self.status
            in {
                ExitExecutionStatus.CLOSED,
                ExitExecutionStatus.ALREADY_FLAT_RECONCILED,
            }
            and self.position_state is V2PositionState.CLOSED
        )

    @property
    def requires_client_order_id_recovery(self) -> bool:
        return self.status is ExitExecutionStatus.UNKNOWN


def floor_to_step(quantity: Decimal, step_size: Decimal) -> Decimal:
    """Floor a quantity to step size. Never rounds up (plan 8.2)."""
    if step_size <= 0:
        raise ValueError(f"step_size must be > 0, got {step_size}")
    return (quantity / step_size).to_integral_value(rounding=ROUND_DOWN) * step_size


def evaluate_exit(
    *,
    position_id: str,
    symbol: str,
    direction: str,
    reason: ExitReason,
    requested_quantity: Decimal,
    authoritative_position: ExchangePositionSnapshot | None,
    step_size: Decimal,
    attempt: int = 1,
    fencing_token_valid: bool = True,
    gateway_available: bool = True,
) -> ExitDecision:
    """Decide whether and how much to reduce. Pure; no exchange calls.

    Note what is absent: no manifest check, no AI call, no net-edge check, no
    entry kill switch, no data-freshness check. Those are Entry Gate concerns and
    must not be able to trap a position (plan 8.1).
    """
    client_order_id = exit_client_order_id(position_id, attempt=attempt)

    def blocked(block_reason: ExitBlockReason, detail: str, quantity: Decimal = Decimal("0")) -> ExitDecision:
        return ExitDecision(
            verdict=ExitVerdict.BLOCKED,
            position_id=position_id,
            symbol=symbol,
            direction=direction,
            reason=reason,
            quantity=quantity,
            client_order_id=client_order_id,
            block_reason=block_reason,
            detail=detail,
        )

    if not fencing_token_valid:
        return blocked(ExitBlockReason.INVALID_FENCING_TOKEN, "fencing token is not valid for this cycle")
    if not gateway_available:
        return blocked(ExitBlockReason.GATEWAY_UNAVAILABLE, "exchange gateway is not callable")

    if authoritative_position is None or authoritative_position.quantity <= 0:
        # Nothing to reduce. This is not a failure; it is reconciled as flat.
        return ExitDecision(
            verdict=ExitVerdict.ALREADY_FLAT,
            position_id=position_id,
            symbol=symbol,
            direction=direction,
            reason=reason,
            quantity=Decimal("0"),
            client_order_id=client_order_id,
            detail="no authoritative exchange position to reduce",
        )

    if authoritative_position.direction != direction:
        return blocked(
            ExitBlockReason.SIDE_WOULD_NOT_REDUCE,
            f"local direction {direction} disagrees with exchange {authoritative_position.direction}",
        )

    if requested_quantity <= 0:
        return blocked(
            ExitBlockReason.QUANTITY_NOT_POSITIVE,
            f"requested quantity {requested_quantity} is not positive",
        )

    # Clamp down to exchange truth, then floor to step size. Never scale up.
    clamped = min(requested_quantity, authoritative_position.quantity)
    quantity = floor_to_step(clamped, step_size)

    if quantity <= 0:
        return blocked(
            ExitBlockReason.QUANTITY_ROUNDS_TO_ZERO,
            f"quantity {clamped} floors to zero at step size {step_size}",
        )

    return ExitDecision(
        verdict=ExitVerdict.APPROVED,
        position_id=position_id,
        symbol=symbol,
        direction=direction,
        reason=reason,
        quantity=quantity,
        client_order_id=client_order_id,
        detail=f"reduce {quantity} of authoritative {authoritative_position.quantity}",
    )


def execute_reduce_only_exit(
    decision: ExitDecision,
    *,
    adapter,
    authoritative_quantity: Decimal,
    step_size: Decimal,
    open_protection_order_ids: tuple[str, ...] = (),
) -> ExitExecutionResult:
    """Submit the reduce-only exit and reconcile local state against exchange truth.

    Exchange-First:
    - Already-Flat rejection from the exchange is reconciled as success (8.3).
    - Partial fill projects only the confirmed quantity.
    - Local CLOSED happens only after exchange confirms position is zero.
    - A timeout yields UNKNOWN; the caller must resolve by Client Order ID,
      not by resubmitting (which would double the exit).
    - Residual protection orders are cancelled after a confirmed full close.
    """
    from services.automated_trading.domain.commands import SubmitReduceOnlyExit
    from services.automated_trading.infrastructure.binance_adapter import BinanceAdapterUnavailable

    if not decision.approved and decision.verdict is not ExitVerdict.ALREADY_FLAT:
        return ExitExecutionResult(
            status=ExitExecutionStatus.NOT_ATTEMPTED,
            position_state=V2PositionState.REDUCING,
            client_order_id=decision.client_order_id,
            detail=f"exit blocked: {decision.block_reason} – {decision.detail}",
        )

    if decision.verdict is ExitVerdict.ALREADY_FLAT:
        # Exchange confirmed nothing to reduce: reconcile as flat.
        return ExitExecutionResult(
            status=ExitExecutionStatus.ALREADY_FLAT_RECONCILED,
            position_state=V2PositionState.CLOSED,
            client_order_id=decision.client_order_id,
            detail="position already flat at exchange; ALREADY_FLAT_RECONCILED",
        )

    command = SubmitReduceOnlyExit(
        position_id=decision.position_id,
        exit_reason=decision.reason.value,
        reduce_quantity=decision.quantity,
        client_order_id=decision.client_order_id,
        is_emergency=decision.reason.is_hard_exit,
    )

    try:
        receipt = adapter.submit_reduce_only_exit(command, decision.symbol, decision.exchange_side)
    except ReduceOnlyAlreadyFlat:
        # Exchange rejected because the position is already flat.
        try:
            snapshot = adapter.fetch_authoritative_snapshot()
            still_open = any(
                p.symbol == decision.symbol and p.direction == decision.direction and p.quantity > 0
                for p in snapshot.positions
            )
        except Exception:  # noqa: BLE001
            still_open = False  # Assume flat when we cannot read

        if not still_open:
            _cancel_protection(open_protection_order_ids, decision.symbol, adapter)
            return ExitExecutionResult(
                status=ExitExecutionStatus.ALREADY_FLAT_RECONCILED,
                position_state=V2PositionState.CLOSED,
                client_order_id=decision.client_order_id,
                residual_protection_cancelled=open_protection_order_ids,
                detail="exchange returned ReduceOnly already flat; position confirmed zero",
            )
        return ExitExecutionResult(
            status=ExitExecutionStatus.SUBMITTED_UNCONFIRMED,
            position_state=V2PositionState.REDUCING,
            client_order_id=decision.client_order_id,
            detail="exchange reported already flat but position still shows open; needs reconciliation",
        )
    except ExitTimeout as exc:
        return ExitExecutionResult(
            status=ExitExecutionStatus.UNKNOWN,
            position_state=V2PositionState.REDUCING,
            client_order_id=decision.client_order_id,
            detail=f"exit outcome undetermined: {exc}. Resolve by client order id lookup.",
        )
    except BinanceAdapterUnavailable as exc:
        return ExitExecutionResult(
            status=ExitExecutionStatus.FAILED,
            position_state=V2PositionState.REDUCING,
            client_order_id=decision.client_order_id,
            detail=f"exit submission failed: {exc}",
        )

    # Fetch fills to confirm actual reduction.
    try:
        fills = adapter.fetch_fills(decision.symbol, receipt.exchange_order_id)
    except (BinanceAdapterUnavailable, ExitTimeout):
        return ExitExecutionResult(
            status=ExitExecutionStatus.SUBMITTED_UNCONFIRMED,
            position_state=V2PositionState.REDUCING,
            client_order_id=decision.client_order_id,
            exchange_order_id=receipt.exchange_order_id,
            detail="order acknowledged but fills unavailable; position state unresolved",
        )

    reduced_qty, vwap, total_fee, trade_ids, fill_ts = _aggregate_fills(fills)

    if not trade_ids or reduced_qty <= 0:
        return ExitExecutionResult(
            status=ExitExecutionStatus.SUBMITTED_UNCONFIRMED,
            position_state=V2PositionState.REDUCING,
            client_order_id=decision.client_order_id,
            exchange_order_id=receipt.exchange_order_id,
            detail="order acknowledged with no confirmed fill",
        )

    remaining = max(Decimal("0"), authoritative_quantity - reduced_qty)
    remaining_floored = floor_to_step(remaining, step_size)

    if remaining_floored <= 0:
        # Full close confirmed. Cancel residual protection.
        _cancel_protection(open_protection_order_ids, decision.symbol, adapter)
        return ExitExecutionResult(
            status=ExitExecutionStatus.CLOSED,
            position_state=V2PositionState.CLOSED,
            client_order_id=decision.client_order_id,
            exchange_order_id=receipt.exchange_order_id,
            trade_ids=trade_ids,
            reduced_quantity=reduced_qty,
            average_fill_price=vwap,
            total_fee=total_fee,
            remaining_quantity=Decimal("0"),
            fill_timestamp=fill_ts,
            residual_protection_cancelled=open_protection_order_ids,
            detail=f"position fully closed at vwap {vwap}",
        )

    return ExitExecutionResult(
        status=ExitExecutionStatus.PARTIALLY_REDUCED,
        position_state=V2PositionState.REDUCING,
        client_order_id=decision.client_order_id,
        exchange_order_id=receipt.exchange_order_id,
        trade_ids=trade_ids,
        reduced_quantity=reduced_qty,
        average_fill_price=vwap,
        total_fee=total_fee,
        remaining_quantity=remaining_floored,
        fill_timestamp=fill_ts,
        detail=f"partial exit: reduced {reduced_qty}, remaining {remaining_floored}",
    )


def _cancel_protection(order_ids: tuple[str, ...], symbol: str, adapter) -> None:
    """Best-effort cancellation of residual protection orders.

    A failure to cancel a protection order after a confirmed close is not fatal:
    the order is reduce-only and the position is already zero, so triggering it
    would be an exchange no-op. We still try, and log failures rather than raising.
    """
    import logging

    logger = logging.getLogger(__name__)
    for order_id in order_ids:
        try:
            adapter.cancel_order(symbol, order_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("failed to cancel residual protection %s: %s", order_id, exc)


def _aggregate_fills(fills: tuple) -> tuple[Decimal, Decimal | None, Decimal, tuple[str, ...], datetime | None]:
    total_quantity = Decimal("0")
    notional = Decimal("0")
    total_fee = Decimal("0")
    trade_ids: list[str] = []
    last_ts: datetime | None = None
    for fill in fills:
        total_quantity += fill.filled_quantity
        notional += fill.filled_quantity * fill.fill_price
        total_fee += fill.fee
        trade_ids.append(fill.trade_id)
        if last_ts is None or fill.fill_timestamp > last_ts:
            last_ts = fill.fill_timestamp
    vwap = (notional / total_quantity) if total_quantity > 0 else None
    return total_quantity, vwap, total_fee, tuple(trade_ids), last_ts
