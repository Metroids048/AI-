"""Protection coordinator: every exchange-confirmed position gets protected.

Plan sections 7.1-7.5. The invariant this module exists to uphold (Gate 8):
there is no such thing as "managed, unprotected, and healthy". A live exchange
position without an exchange-acknowledged stop is either mid-escalation or the
account is Entry-blocked.

Price source (7.1): absolute stop/take-profit prices are derived from
``average_fill_price`` on a real fill receipt, never from a decision-time or
reference price. A price computed at decision time and submitted minutes later
is the bug class this design removes.

Rounding (7.2): prices are rounded to tick size in the *risk-safer* direction.
For a long, the stop rounds up (closer to entry, exits sooner) and the target
rounds down (takes profit sooner). Rounding a stop away from entry would widen
real risk beyond what the candidate authorized.

Escalation (7.4): submit -> on failure re-read the true position and retry once
under a new attempt number -> still failing, emergency market reduce-only close
-> re-read -> still open, EMERGENCY_CLOSE_PENDING plus an account-wide Entry
block and a high-priority alert. No exception is ever silently swallowed.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from enum import StrEnum
from typing import TYPE_CHECKING

from services.automated_trading.domain.client_order_id import (
    stop_client_order_id,
    target_client_order_id,
)
from services.automated_trading.domain.enums import V2ProtectionState

if TYPE_CHECKING:
    from services.automated_trading.domain.candidates import TradeCandidate


class ProtectionOutcome(StrEnum):
    """Terminal outcome of an ``ensure_protection`` pass."""

    ACTIVE = "ACTIVE"
    ALREADY_FLAT = "ALREADY_FLAT"
    UNKNOWN = "UNKNOWN"
    EMERGENCY_CLOSED = "EMERGENCY_CLOSED"
    EMERGENCY_CLOSE_PENDING = "EMERGENCY_CLOSE_PENDING"


class ProtectionFailureAction(StrEnum):
    """Actions taken along the escalation ladder, in order."""

    RETRIED_SUBMISSION = "RETRIED_SUBMISSION"
    EMERGENCY_REDUCE_ONLY_CLOSE = "EMERGENCY_REDUCE_ONLY_CLOSE"
    ACCOUNT_ENTRY_BLOCK = "ACCOUNT_ENTRY_BLOCK"
    HIGH_PRIORITY_ALERT = "HIGH_PRIORITY_ALERT"


class ProtectionSubmissionError(Exception):
    """Adapter failed to submit a protection order."""


class ProtectionTimeout(Exception):
    """Protection submission outcome is undetermined."""


@dataclass(frozen=True)
class ProtectionPlan:
    """Absolute protection prices derived from a real fill.

    ``stop_price`` is mandatory: a position with no stop must never be left
    resting. ``take_profit_price`` is optional (trail-only candidates).
    """

    position_id: str
    symbol: str
    direction: str
    quantity: Decimal
    average_fill_price: Decimal
    stop_price: Decimal
    take_profit_price: Decimal | None
    stop_client_order_id: str
    tp_client_order_id: str | None
    attempt: int

    def __post_init__(self) -> None:
        if self.quantity <= 0:
            raise ValueError(f"protection quantity must be > 0, got {self.quantity}")
        if self.stop_price <= 0:
            raise ValueError(f"stop price must be > 0, got {self.stop_price}")

        # Geometry check (plan 7.2). A stop on the wrong side of entry is not a
        # stop; it is an immediate market exit at a loss.
        if self.direction == "long":
            if not self.stop_price < self.average_fill_price:
                raise ValueError(f"long stop {self.stop_price} must be below fill {self.average_fill_price}")
            if self.take_profit_price is not None and not self.average_fill_price < self.take_profit_price:
                raise ValueError(
                    f"long take-profit {self.take_profit_price} must be above fill {self.average_fill_price}"
                )
        elif self.direction == "short":
            if not self.stop_price > self.average_fill_price:
                raise ValueError(f"short stop {self.stop_price} must be above fill {self.average_fill_price}")
            if self.take_profit_price is not None and not self.average_fill_price > self.take_profit_price:
                raise ValueError(
                    f"short take-profit {self.take_profit_price} must be below fill {self.average_fill_price}"
                )
        else:
            raise ValueError(f"direction must be long or short, got {self.direction!r}")

    @property
    def protection_side(self) -> str:
        """Exchange side that reduces this position."""
        return "sell" if self.direction == "long" else "buy"


@dataclass(frozen=True)
class ProtectionResult:
    """Outcome of ensuring protection for one position."""

    outcome: ProtectionOutcome
    state: V2ProtectionState
    stop_exchange_order_id: str | None = None
    tp_exchange_order_id: str | None = None
    actions: tuple[ProtectionFailureAction, ...] = ()
    account_entry_blocked: bool = False
    detail: str = ""
    attempts: int = 1

    @property
    def is_active(self) -> bool:
        """ACTIVE requires a real exchange order id (plan 7.3)."""
        return self.state is V2ProtectionState.PROTECTION_ACTIVE and bool(self.stop_exchange_order_id)

    @property
    def requires_manual_intervention(self) -> bool:
        return self.outcome is ProtectionOutcome.EMERGENCY_CLOSE_PENDING


def round_to_tick(price: Decimal, tick_size: Decimal, *, direction: str, leg: str) -> Decimal:
    """Round a protection price to tick size in the risk-safer direction.

    Long stop rounds up and short stop rounds down: both move the stop *closer*
    to entry, so realized risk never exceeds the authorized distance. Targets
    round the opposite way, taking profit marginally sooner.
    """
    if tick_size <= 0:
        raise ValueError(f"tick_size must be > 0, got {tick_size}")

    if leg == "stop":
        rounding = ROUND_CEILING if direction == "long" else ROUND_FLOOR
    elif leg == "target":
        rounding = ROUND_FLOOR if direction == "long" else ROUND_CEILING
    else:
        raise ValueError(f"leg must be 'stop' or 'target', got {leg!r}")

    return (price / tick_size).to_integral_value(rounding=rounding) * tick_size


def build_protection_plan(
    *,
    position_id: str,
    candidate: TradeCandidate,
    average_fill_price: Decimal,
    filled_quantity: Decimal,
    tick_size: Decimal,
    attempt: int = 1,
) -> ProtectionPlan:
    """Derive absolute protection prices from a confirmed fill.

    Args:
        position_id: Managed position identifier.
        candidate: Supplies the relative stop/take-profit distances.
        average_fill_price: VWAP from a real exchange fill receipt.
        filled_quantity: Confirmed filled quantity (not the requested quantity).
        tick_size: Exchange price increment.
        attempt: Protection attempt number; increments on retry so the Client
            Order ID changes while the logical identity stays the same.

    Raises:
        ValueError: If the resolved geometry is invalid.
    """
    if average_fill_price <= 0:
        raise ValueError(f"average_fill_price must be > 0, got {average_fill_price}")
    if filled_quantity <= 0:
        raise ValueError(f"filled_quantity must be > 0, got {filled_quantity}")

    raw_stop, raw_target = candidate.resolve_protection_prices(average_fill_price)
    direction = candidate.direction

    stop_price = round_to_tick(raw_stop, tick_size, direction=direction, leg="stop")
    take_profit_price = (
        round_to_tick(raw_target, tick_size, direction=direction, leg="target") if raw_target is not None else None
    )

    return ProtectionPlan(
        position_id=position_id,
        symbol=candidate.symbol,
        direction=direction,
        quantity=filled_quantity,
        average_fill_price=average_fill_price,
        stop_price=stop_price,
        take_profit_price=take_profit_price,
        stop_client_order_id=stop_client_order_id(position_id, revision=attempt),
        tp_client_order_id=target_client_order_id(position_id, revision=attempt) if take_profit_price else None,
        attempt=attempt,
    )


# ---------------------------------------------------------------------------
# Escalation ladder (plan 7.4)
# ---------------------------------------------------------------------------


def _exchange_quantity_for(adapter, symbol: str, direction: str) -> Decimal:
    """Read the true exchange quantity for one position side.

    Returns 0 when the exchange reports no such position. A read failure is
    propagated, never converted into an assumed-flat 0.
    """
    snapshot = adapter.fetch_authoritative_snapshot()
    for position in snapshot.positions:
        if position.symbol == symbol and position.direction == direction:
            return position.quantity
    return Decimal("0")


def ensure_protection(
    plan: ProtectionPlan,
    *,
    adapter,
    max_attempts: int = 2,
) -> ProtectionResult:
    """Ensure the position carries exchange-acknowledged protection.

    Escalation on submission failure: retry once under a new attempt number,
    then emergency market reduce-only close, then EMERGENCY_CLOSE_PENDING with
    an account-wide Entry block.

    Args:
        plan: Protection plan derived from a real fill.
        adapter: Exchange adapter.
        max_attempts: Submission attempts before escalating to emergency close.

    Returns:
        ProtectionResult. Exceptions are converted into persisted outcomes;
        nothing is silently suppressed.
    """
    from services.automated_trading.domain.commands import SubmitProtectionOrders
    from services.automated_trading.infrastructure.binance_adapter import BinanceAdapterUnavailable

    actions: list[ProtectionFailureAction] = []
    current_plan = plan
    last_error = ""

    for attempt in range(1, max_attempts + 1):
        command = SubmitProtectionOrders(
            position_id=current_plan.position_id,
            stop_loss_price=current_plan.stop_price,
            take_profit_price=current_plan.take_profit_price,
            stop_client_order_id=current_plan.stop_client_order_id,
            tp_client_order_id=current_plan.tp_client_order_id,
        )

        try:
            stop_receipt, tp_receipt = adapter.submit_protection(
                command,
                current_plan.symbol,
                current_plan.protection_side,
                current_plan.quantity,
            )
        except ProtectionTimeout as exc:
            # Undetermined: the order may exist. Resolve by lookup, never resubmit.
            return ProtectionResult(
                outcome=ProtectionOutcome.UNKNOWN,
                state=V2ProtectionState.PROTECTION_UNKNOWN,
                actions=tuple(actions),
                detail=(
                    f"protection submission outcome undetermined: {exc}. "
                    f"Resolve by client order id {current_plan.stop_client_order_id}."
                ),
                attempts=attempt,
            )
        except (ProtectionSubmissionError, BinanceAdapterUnavailable) as exc:
            last_error = str(exc)

            # Re-read the true position before deciding anything.
            try:
                live_quantity = _exchange_quantity_for(adapter, current_plan.symbol, current_plan.direction)
            except Exception as read_exc:  # noqa: BLE001 - must not be suppressed
                return _escalate_emergency(
                    current_plan,
                    adapter=adapter,
                    actions=actions,
                    attempts=attempt,
                    detail=(
                        f"protection failed ({last_error}) and the position read also failed "
                        f"({read_exc}); cannot confirm exposure"
                    ),
                )

            if live_quantity <= 0:
                # Nothing to protect; the position closed underneath us.
                return ProtectionResult(
                    outcome=ProtectionOutcome.ALREADY_FLAT,
                    state=V2ProtectionState.PROTECTION_CANCELLED,
                    actions=tuple(actions),
                    detail=f"protection failed ({last_error}) but exchange position is already flat",
                    attempts=attempt,
                )

            if attempt < max_attempts:
                actions.append(ProtectionFailureAction.RETRIED_SUBMISSION)
                current_plan = ProtectionPlan(
                    position_id=current_plan.position_id,
                    symbol=current_plan.symbol,
                    direction=current_plan.direction,
                    quantity=live_quantity,
                    average_fill_price=current_plan.average_fill_price,
                    stop_price=current_plan.stop_price,
                    take_profit_price=current_plan.take_profit_price,
                    stop_client_order_id=stop_client_order_id(current_plan.position_id, revision=attempt + 1),
                    tp_client_order_id=(
                        target_client_order_id(current_plan.position_id, revision=attempt + 1)
                        if current_plan.take_profit_price
                        else None
                    ),
                    attempt=attempt + 1,
                )
                continue

            return _escalate_emergency(
                current_plan,
                adapter=adapter,
                actions=actions,
                attempts=attempt,
                detail=f"protection submission failed after {attempt} attempts: {last_error}",
            )

        # Submission returned. ACTIVE demands a real exchange order id.
        if not stop_receipt.exchange_order_id:
            return _escalate_emergency(
                current_plan,
                adapter=adapter,
                actions=actions,
                attempts=attempt,
                detail="exchange returned no stop order id; protection cannot be ACTIVE",
            )

        return ProtectionResult(
            outcome=ProtectionOutcome.ACTIVE,
            state=V2ProtectionState.PROTECTION_ACTIVE,
            stop_exchange_order_id=stop_receipt.exchange_order_id,
            tp_exchange_order_id=tp_receipt.exchange_order_id if tp_receipt else None,
            actions=tuple(actions),
            detail=f"protection active for {current_plan.quantity} {current_plan.symbol}",
            attempts=attempt,
        )

    # Loop cannot fall through, but fail closed rather than implicitly returning None.
    return _escalate_emergency(
        current_plan,
        adapter=adapter,
        actions=actions,
        attempts=max_attempts,
        detail=f"protection exhausted {max_attempts} attempts: {last_error}",
    )


def _escalate_emergency(
    plan: ProtectionPlan,
    *,
    adapter,
    actions: list[ProtectionFailureAction],
    attempts: int,
    detail: str,
) -> ProtectionResult:
    """Emergency reduce-only close, then verify it actually closed.

    If the position is still open afterwards the account is Entry-blocked and a
    high-priority alert is raised. This is the only correct end state for a live
    position that cannot be protected.
    """
    from services.automated_trading.domain.client_order_id import exit_client_order_id
    from services.automated_trading.domain.commands import SubmitReduceOnlyExit
    from services.automated_trading.infrastructure.binance_adapter import BinanceAdapterUnavailable

    actions = [*actions, ProtectionFailureAction.EMERGENCY_REDUCE_ONLY_CLOSE]

    command = SubmitReduceOnlyExit(
        position_id=plan.position_id,
        exit_reason="PROTECTION_FAILED_EMERGENCY_CLOSE",
        reduce_quantity=plan.quantity,
        client_order_id=exit_client_order_id(plan.position_id, attempt=plan.attempt),
        is_emergency=True,
    )

    close_error = ""
    try:
        adapter.submit_reduce_only_exit(command, plan.symbol, plan.protection_side)
    except (ProtectionTimeout, ProtectionSubmissionError, BinanceAdapterUnavailable) as exc:
        close_error = str(exc)

    # Verify against exchange truth regardless of what the close call reported.
    try:
        live_quantity = _exchange_quantity_for(adapter, plan.symbol, plan.direction)
    except Exception as exc:  # noqa: BLE001 - must not be suppressed
        actions.extend([ProtectionFailureAction.ACCOUNT_ENTRY_BLOCK, ProtectionFailureAction.HIGH_PRIORITY_ALERT])
        return ProtectionResult(
            outcome=ProtectionOutcome.EMERGENCY_CLOSE_PENDING,
            state=V2ProtectionState.PROTECTION_FAILED,
            actions=tuple(actions),
            account_entry_blocked=True,
            detail=f"{detail}; emergency close verification failed ({exc})",
            attempts=attempts,
        )

    if live_quantity <= 0:
        return ProtectionResult(
            outcome=ProtectionOutcome.EMERGENCY_CLOSED,
            state=V2ProtectionState.PROTECTION_CANCELLED,
            actions=tuple(actions),
            detail=f"{detail}; emergency reduce-only close confirmed flat",
            attempts=attempts,
        )

    actions.extend([ProtectionFailureAction.ACCOUNT_ENTRY_BLOCK, ProtectionFailureAction.HIGH_PRIORITY_ALERT])
    suffix = f" (close error: {close_error})" if close_error else ""
    return ProtectionResult(
        outcome=ProtectionOutcome.EMERGENCY_CLOSE_PENDING,
        state=V2ProtectionState.PROTECTION_FAILED,
        actions=tuple(actions),
        account_entry_blocked=True,
        detail=f"{detail}; position still open at {live_quantity} after emergency close{suffix}",
        attempts=attempts,
    )
