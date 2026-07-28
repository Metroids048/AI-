"""Market snapshot provider for pre-submission validation.

Provides real-time market state snapshots used by Entry Gate to validate:
- Price drift from decision price
- Market data freshness
- Symbol configuration (tick size, min notional, etc.)

These snapshots are immutable evidence of market state at decision time.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True)
class PreSubmitMarketSnapshot:
    """Immutable market state snapshot at pre-submission time.

    Used by Entry Gate to validate:
    - Price has not drifted too far from decision price
    - Market data is fresh
    - Symbol configuration is valid

    symbol: Trading symbol (e.g., "BTC/USDT")
    current_price: Current market price (from ticker or recent trade)
    atr: Current ATR value (for drift validation)
    last_update: Timestamp of last market data update
    tick_size: Minimum price increment
    step_size: Minimum quantity increment
    min_notional: Minimum order notional value
    """

    symbol: str
    current_price: Decimal
    atr: Decimal
    last_update: datetime
    tick_size: Decimal
    step_size: Decimal
    min_notional: Decimal

    def __post_init__(self) -> None:
        if self.current_price <= 0:
            raise ValueError(f"current_price must be > 0, got {self.current_price}")
        if self.atr < 0:
            raise ValueError(f"atr must be >= 0, got {self.atr}")
        if self.tick_size <= 0:
            raise ValueError(f"tick_size must be > 0, got {self.tick_size}")
        if self.step_size <= 0:
            raise ValueError(f"step_size must be > 0, got {self.step_size}")
        if self.min_notional <= 0:
            raise ValueError(f"min_notional must be > 0, got {self.min_notional}")


@dataclass(frozen=True)
class AuthoritativeAccountSnapshot:
    """Immutable exchange account state snapshot.

    Used by Reconciliation to compare exchange truth vs local projection.

    balance: Available balance (in quote currency, e.g., USDT)
    equity: Total equity (balance + unrealized PnL)
    positions: List of open positions at exchange
    pending_orders: List of open orders at exchange
    snapshot_timestamp: Exchange-reported timestamp
    """

    balance: Decimal
    equity: Decimal
    positions: list[ExchangePositionSnapshot]
    pending_orders: list[ExchangeOrderSnapshot]
    snapshot_timestamp: datetime

    def __post_init__(self) -> None:
        if self.equity < 0:
            raise ValueError(f"equity cannot be negative, got {self.equity}")


@dataclass(frozen=True)
class ExchangePositionSnapshot:
    """Immutable exchange position snapshot."""

    symbol: str
    direction: str  # "long" | "short"
    quantity: Decimal
    entry_price: Decimal
    mark_price: Decimal
    unrealized_pnl: Decimal
    leverage: int


@dataclass(frozen=True)
class ExchangeOrderSnapshot:
    """Immutable exchange order snapshot."""

    exchange_order_id: str
    client_order_id: str | None
    symbol: str
    side: str  # "buy" | "sell"
    order_type: str  # "market" | "limit" | "stop_market" | "take_profit_market"
    quantity: Decimal
    price: Decimal | None
    status: str  # "new" | "partially_filled" | "filled" | "canceled"
    reduce_only: bool
