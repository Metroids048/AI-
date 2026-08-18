"""V2 domain receipts: immutable exchange evidence records.

Receipts are the Exchange-First proof that exchange actions occurred.
They are immutable once created and serve as the source of truth for
local state projection.

Invariants:
- FillReceipt requires exchange_order_id + trade_ids + positive quantity/price
- ProtectionReceipt requires exchange conditional order IDs
- ReduceReceipt requires exchange_order_id + trade_ids
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True)
class FillReceipt:
    """Immutable evidence that an exchange order was filled.

    This is the Exchange-First proof required before any local position
    can be projected as MANAGED. Without this receipt, no position exists.

    exchange_order_id: Binance order ID assigned by exchange.
    trade_ids: List of trade IDs (fills may span multiple partial fills).
    filled_quantity: Total quantity filled (must be > 0).
    average_fill_price: Volume-weighted average fill price (must be > 0).
    total_fee: Total fee paid (in quote currency).
    fill_timestamp: Exchange-reported fill timestamp.
    """

    intent_id: str
    exchange_order_id: str
    trade_ids: tuple[str, ...]  # Empty only for order-level authoritative recovery
    filled_quantity: Decimal
    average_fill_price: Decimal
    total_fee: Decimal
    fill_timestamp: datetime
    source: str = "BINANCE_USER_TRADE"

    def __post_init__(self) -> None:
        if not self.exchange_order_id:
            raise ValueError("FillReceipt requires exchange_order_id")
        if not self.trade_ids and self.source != "BINANCE_ORDER_STATUS_RECOVERY":
            raise ValueError("FillReceipt requires trade_ids unless order-level recovery is explicit")
        if self.filled_quantity <= 0:
            raise ValueError(f"FillReceipt filled_quantity must be > 0, got {self.filled_quantity}")
        if self.average_fill_price <= 0:
            raise ValueError(f"FillReceipt average_fill_price must be > 0, got {self.average_fill_price}")


@dataclass(frozen=True)
class ProtectionReceipt:
    """Immutable evidence that exchange acknowledged protection orders.

    Protection is only considered ACTIVE when the exchange confirms
    conditional order IDs for stop-loss (and optionally take-profit).
    """

    position_id: str
    stop_exchange_order_id: str
    tp_exchange_order_id: str | None
    submission_timestamp: datetime

    def __post_init__(self) -> None:
        if not self.stop_exchange_order_id:
            raise ValueError("ProtectionReceipt requires stop_exchange_order_id")


@dataclass(frozen=True)
class ReduceReceipt:
    """Immutable evidence that a reduce-only exit was filled by exchange."""

    position_id: str
    exchange_order_id: str
    trade_ids: tuple[str, ...]
    filled_quantity: Decimal
    average_fill_price: Decimal
    total_fee: Decimal
    fill_timestamp: datetime

    def __post_init__(self) -> None:
        if not self.exchange_order_id:
            raise ValueError("ReduceReceipt requires exchange_order_id")
        if not self.trade_ids:
            raise ValueError("ReduceReceipt requires at least one trade_id")
        if self.filled_quantity <= 0:
            raise ValueError(f"ReduceReceipt filled_quantity must be > 0, got {self.filled_quantity}")
        if self.average_fill_price <= 0:
            raise ValueError(f"ReduceReceipt average_fill_price must be > 0, got {self.average_fill_price}")
