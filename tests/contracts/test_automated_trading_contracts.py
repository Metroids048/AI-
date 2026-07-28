"""Contract tests for V2 automated trading domain invariants.

Validates Exchange-First invariants:
- FillReceipt requires exchange_order_id + trade_ids + positive quantities
- POSITION_PROJECTED requires FillReceipt (cannot be created without it)
- Protection cannot be ACTIVE without exchange conditional order IDs
- Managed position invariant checks
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from services.automated_trading.domain.invariants import (
    assert_fill_receipt_valid,
    assert_managed_position_invariants,
    assert_protection_price_from_fill,
    assert_protection_receipt_valid,
    assert_reduce_quantity_valid,
)
from services.automated_trading.domain.receipts import FillReceipt, ProtectionReceipt


def _make_fill_receipt(**overrides) -> FillReceipt:
    defaults = {
        "intent_id": "intent-001",
        "exchange_order_id": "binance-order-123",
        "trade_ids": ("trade-001",),
        "filled_quantity": Decimal("0.001"),
        "average_fill_price": Decimal("65000.0"),
        "total_fee": Decimal("0.65"),
        "fill_timestamp": datetime(2026, 7, 28, 10, 0, 0, tzinfo=UTC),
    }
    defaults.update(overrides)
    return FillReceipt(**defaults)


class TestFillReceiptInvariants:
    def test_valid_fill_receipt_passes(self) -> None:
        receipt = _make_fill_receipt()
        assert_fill_receipt_valid(receipt)

    def test_empty_exchange_order_id_raises(self) -> None:
        with pytest.raises(ValueError, match="exchange_order_id"):
            FillReceipt(
                intent_id="intent-001",
                exchange_order_id="",
                trade_ids=("trade-001",),
                filled_quantity=Decimal("0.001"),
                average_fill_price=Decimal("65000.0"),
                total_fee=Decimal("0.65"),
                fill_timestamp=datetime(2026, 7, 28, tzinfo=UTC),
            )

    def test_empty_trade_ids_raises(self) -> None:
        with pytest.raises(ValueError, match="trade_id"):
            FillReceipt(
                intent_id="intent-001",
                exchange_order_id="binance-order-123",
                trade_ids=(),
                filled_quantity=Decimal("0.001"),
                average_fill_price=Decimal("65000.0"),
                total_fee=Decimal("0.65"),
                fill_timestamp=datetime(2026, 7, 28, tzinfo=UTC),
            )

    def test_zero_filled_quantity_raises(self) -> None:
        with pytest.raises(ValueError, match="filled_quantity"):
            FillReceipt(
                intent_id="intent-001",
                exchange_order_id="binance-order-123",
                trade_ids=("trade-001",),
                filled_quantity=Decimal("0"),
                average_fill_price=Decimal("65000.0"),
                total_fee=Decimal("0.65"),
                fill_timestamp=datetime(2026, 7, 28, tzinfo=UTC),
            )

    def test_zero_fill_price_raises(self) -> None:
        with pytest.raises(ValueError, match="average_fill_price"):
            FillReceipt(
                intent_id="intent-001",
                exchange_order_id="binance-order-123",
                trade_ids=("trade-001",),
                filled_quantity=Decimal("0.001"),
                average_fill_price=Decimal("0"),
                total_fee=Decimal("0.65"),
                fill_timestamp=datetime(2026, 7, 28, tzinfo=UTC),
            )


class TestProtectionReceiptInvariants:
    def test_valid_protection_receipt_passes(self) -> None:
        receipt = ProtectionReceipt(
            position_id="pos-001",
            stop_exchange_order_id="conditional-stop-456",
            tp_exchange_order_id="conditional-tp-789",
            submission_timestamp=datetime(2026, 7, 28, tzinfo=UTC),
        )
        assert_protection_receipt_valid(receipt)

    def test_empty_stop_order_id_raises(self) -> None:
        with pytest.raises(ValueError, match="stop_exchange_order_id"):
            ProtectionReceipt(
                position_id="pos-001",
                stop_exchange_order_id="",
                tp_exchange_order_id=None,
                submission_timestamp=datetime(2026, 7, 28, tzinfo=UTC),
            )

    def test_tp_order_id_optional(self) -> None:
        receipt = ProtectionReceipt(
            position_id="pos-001",
            stop_exchange_order_id="conditional-stop-456",
            tp_exchange_order_id=None,
            submission_timestamp=datetime(2026, 7, 28, tzinfo=UTC),
        )
        assert_protection_receipt_valid(receipt)  # Must not raise


class TestManagedPositionInvariants:
    def test_valid_position_with_fill_receipt_passes(self) -> None:
        receipt = _make_fill_receipt()
        assert_managed_position_invariants(
            position_id="pos-001",
            fill_receipt=receipt,
            exchange_order_id="binance-order-123",
        )

    def test_no_fill_receipt_raises(self) -> None:
        """Exchange-First: cannot have managed position without FillReceipt."""
        with pytest.raises(ValueError, match="no FillReceipt"):
            assert_managed_position_invariants(
                position_id="pos-001",
                fill_receipt=None,
                exchange_order_id=None,
            )

    def test_mismatched_exchange_order_id_raises(self) -> None:
        receipt = _make_fill_receipt(exchange_order_id="order-A")
        with pytest.raises(ValueError, match="does not match"):
            assert_managed_position_invariants(
                position_id="pos-001",
                fill_receipt=receipt,
                exchange_order_id="order-B",  # Different!
            )


class TestProtectionPriceInvariants:
    def test_long_stop_below_fill_passes(self) -> None:
        receipt = _make_fill_receipt(average_fill_price=Decimal("65000.0"))
        assert_protection_price_from_fill(receipt, Decimal("64000.0"), "long")

    def test_long_stop_above_fill_raises(self) -> None:
        receipt = _make_fill_receipt(average_fill_price=Decimal("65000.0"))
        with pytest.raises(ValueError, match="must be <"):
            assert_protection_price_from_fill(receipt, Decimal("66000.0"), "long")

    def test_short_stop_above_fill_passes(self) -> None:
        receipt = _make_fill_receipt(average_fill_price=Decimal("65000.0"))
        assert_protection_price_from_fill(receipt, Decimal("66000.0"), "short")

    def test_short_stop_below_fill_raises(self) -> None:
        receipt = _make_fill_receipt(average_fill_price=Decimal("65000.0"))
        with pytest.raises(ValueError, match="must be >"):
            assert_protection_price_from_fill(receipt, Decimal("64000.0"), "short")


class TestReduceQuantityInvariants:
    def test_valid_reduce_quantity_passes(self) -> None:
        assert_reduce_quantity_valid(Decimal("0.001"), Decimal("0.002"))

    def test_zero_reduce_quantity_raises(self) -> None:
        with pytest.raises(ValueError, match="must be > 0"):
            assert_reduce_quantity_valid(Decimal("0"), Decimal("0.002"))

    def test_reduce_exceeds_position_raises(self) -> None:
        with pytest.raises(ValueError, match="exceeds"):
            assert_reduce_quantity_valid(Decimal("0.003"), Decimal("0.002"))

    def test_reduce_equal_to_position_passes(self) -> None:
        assert_reduce_quantity_valid(Decimal("0.002"), Decimal("0.002"))
