"""V2 domain invariants: Exchange-First consistency checks.

These functions assert that the Exchange-First invariants hold.
They must be called before any state mutation and before claiming
that a position is managed.

Invariants that MUST hold:
1. A MANAGED position requires a FillReceipt with exchange_order_id + trade_ids
2. POSITION_PROJECTED cannot exist without a FillReceipt
3. A PROTECTION_ACTIVE position must have exchange conditional order IDs
4. INTENT_CREATED -> FILLED transition is illegal (must pass through ACKNOWLEDGED)
5. Local position quantity cannot exceed fill receipt filled_quantity
6. Protection prices must be computed from fill_receipt.average_fill_price
"""

from __future__ import annotations

from decimal import Decimal

from services.automated_trading.domain.receipts import FillReceipt, ProtectionReceipt


def assert_fill_receipt_valid(receipt: FillReceipt) -> None:
    """Assert fill receipt satisfies Exchange-First requirements.

    Called before projecting any managed position.

    Raises:
        ValueError: If receipt fails invariant checks
    """
    if not receipt.exchange_order_id:
        raise ValueError("Invariant violated: FillReceipt missing exchange_order_id")
    if not receipt.trade_ids:
        raise ValueError("Invariant violated: FillReceipt missing trade_ids")
    if receipt.filled_quantity <= 0:
        raise ValueError(f"Invariant violated: FillReceipt filled_quantity={receipt.filled_quantity} must be > 0")
    if receipt.average_fill_price <= 0:
        raise ValueError(f"Invariant violated: FillReceipt average_fill_price={receipt.average_fill_price} must be > 0")


def assert_protection_receipt_valid(receipt: ProtectionReceipt) -> None:
    """Assert protection receipt satisfies Exchange-First requirements.

    Protection is only ACTIVE when exchange confirms conditional order IDs.

    Raises:
        ValueError: If receipt fails invariant checks
    """
    assert_protection_active_requires_exchange_order_id(receipt.stop_exchange_order_id)


def assert_protection_active_requires_exchange_order_id(stop_exchange_order_id: str | None) -> None:
    """PROTECTION_ACTIVE requires an exchange-confirmed stop order id."""
    if not stop_exchange_order_id:
        raise ValueError(
            "Invariant violated: missing stop exchange order id; "
            "protection cannot be ACTIVE without exchange-confirmed conditional order"
        )


def assert_protection_price_from_fill(
    fill_receipt: FillReceipt,
    stop_loss_price: Decimal,
    direction: str,
    min_stop_distance_pct: Decimal = Decimal("0.001"),
) -> None:
    """Assert stop-loss price is derived from exchange fill price, not decision price.

    Protection prices must be computed from average_fill_price to avoid
    slippage from using the decision/reference price.

    Raises:
        ValueError: If stop_loss_price is not plausibly derived from fill price
    """
    fill_price = fill_receipt.average_fill_price
    if direction == "long":
        if stop_loss_price >= fill_price:
            raise ValueError(
                f"Invariant violated: long stop_loss_price={stop_loss_price} must be < fill_price={fill_price}"
            )
        distance_pct = (fill_price - stop_loss_price) / fill_price
    elif direction == "short":
        if stop_loss_price <= fill_price:
            raise ValueError(
                f"Invariant violated: short stop_loss_price={stop_loss_price} must be > fill_price={fill_price}"
            )
        distance_pct = (stop_loss_price - fill_price) / fill_price
    else:
        raise ValueError(f"Unknown direction: {direction!r}")

    if distance_pct < min_stop_distance_pct:
        raise ValueError(
            f"Invariant violated: stop distance {distance_pct:.4%} is below minimum "
            f"{min_stop_distance_pct:.4%} — likely using wrong reference price"
        )


def assert_reduce_quantity_valid(reduce_quantity: Decimal, position_quantity: Decimal) -> None:
    """Assert reduce-only quantity does not exceed position quantity.

    Reduces that exceed position quantity would attempt to flip the position,
    violating the ReduceOnly contract.

    Raises:
        ValueError: If reduce_quantity > position_quantity
    """
    if reduce_quantity <= 0:
        raise ValueError(f"Invariant violated: reduce_quantity={reduce_quantity} must be > 0")
    if reduce_quantity > position_quantity:
        raise ValueError(
            f"Invariant violated: reduce_quantity={reduce_quantity} exceeds position_quantity={position_quantity}"
        )


def assert_managed_position_invariants(
    position_id: str,
    fill_receipt: FillReceipt | None,
    exchange_order_id: str | None,
) -> None:
    """Assert all Exchange-First invariants for a managed position.

    A managed position MUST have:
    - A valid FillReceipt with exchange_order_id and trade_ids
    - The FillReceipt must reference the same exchange_order_id

    Raises:
        ValueError: If any invariant is violated
    """
    if fill_receipt is None:
        raise ValueError(
            f"Invariant violated: Managed position {position_id!r} has no FillReceipt; "
            "positions cannot be projected without exchange fill evidence"
        )
    assert_fill_receipt_valid(fill_receipt)

    if exchange_order_id and fill_receipt.exchange_order_id != exchange_order_id:
        raise ValueError(
            f"Invariant violated: position {position_id!r} exchange_order_id={exchange_order_id!r} "
            f"does not match FillReceipt.exchange_order_id={fill_receipt.exchange_order_id!r}"
        )
