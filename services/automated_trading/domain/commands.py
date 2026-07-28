"""V2 domain commands: immutable intent declarations.

Commands represent decisions to act, but do not mutate state directly.
They are inputs to the event-sourced state machine.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from services.automated_trading.domain.enums import (
    V2CandidateType,
    V2ExecutionMode,
)


@dataclass(frozen=True)
class CreateEntryIntent:
    """Command: Create a new entry intent from strategy decision.

    This is the first step in the entry flow. It represents a strategy decision
    to enter a position, but no exchange contact has been made yet.
    """

    symbol: str
    direction: str  # "long" | "short"
    candidate_key: str
    candidate_type: V2CandidateType
    execution_mode: V2ExecutionMode
    stop_distance_r: Decimal  # Risk units for stop-loss
    target_r: Decimal | None  # Risk units for take-profit (None = trailing only)
    decision_bar_timestamp: datetime
    decision_funnel_id: str | None  # Link to decision funnel record
    fencing_token: str  # Scheduler cycle fence token


@dataclass(frozen=True)
class SubmitEntryToExchange:
    """Command: Submit entry order to exchange.

    Requires:
    - Valid entry intent in INTENT_CREATED state
    - Execution mode = BINANCE_TESTNET
    - Reconciliation status = HEALTHY
    - No entry kill switch active
    """

    intent_id: str
    quantity: Decimal
    leverage: int
    client_order_id: str  # Unique client-side order ID for idempotency


@dataclass(frozen=True)
class RecordExchangeFill:
    """Command: Record exchange fill receipt.

    Requires:
    - Valid exchange_order_id
    - At least one trade_id
    - Positive filled_quantity
    - Positive average_fill_price

    This is the Exchange-First proof: local state cannot transition to FILLED
    without this command carrying exchange fill evidence.
    """

    intent_id: str
    exchange_order_id: str
    trade_ids: list[str]
    filled_quantity: Decimal
    average_fill_price: Decimal
    total_fee: Decimal
    fill_timestamp: datetime


@dataclass(frozen=True)
class ProjectManagedPosition:
    """Command: Project local managed position from exchange fill.

    Requires:
    - Valid FillReceipt (RecordExchangeFill must have been processed)
    - Intent must be in FILLED state

    This creates the local position projection that tracks the exchange position.
    """

    intent_id: str
    position_id: str  # Unique managed position ID


@dataclass(frozen=True)
class SubmitProtectionOrders:
    """Command: Submit stop-loss and take-profit protection to exchange.

    Requires:
    - Valid managed position in POSITION_PROJECTED state
    - Calculated absolute stop price and take-profit price
    - Exchange order submission succeeds

    Protection prices are calculated from average_fill_price, not from decision prices.
    """

    position_id: str
    stop_loss_price: Decimal
    take_profit_price: Decimal | None
    stop_client_order_id: str
    tp_client_order_id: str | None


@dataclass(frozen=True)
class RecordProtectionActive:
    """Command: Record exchange confirmation of protection orders.

    Requires:
    - Exchange conditional order IDs for stop-loss (and take-profit if submitted)

    Protection is not considered active until exchange acknowledges the conditional orders.
    """

    position_id: str
    stop_exchange_order_id: str
    tp_exchange_order_id: str | None


@dataclass(frozen=True)
class SubmitReduceOnlyExit:
    """Command: Submit reduce-only exit order.

    Triggers:
    - Natural exit signal (time-based, partial profit, trailing stop)
    - Protection triggered (stop-loss or take-profit hit)
    - Emergency exit (hard drawdown, reconciliation failure)

    Reduce-only exits must not depend on:
    - Strategy manifest availability
    - LLM availability
    - Net edge calculation
    - Data freshness
    """

    position_id: str
    exit_reason: str
    reduce_quantity: Decimal
    client_order_id: str
    is_emergency: bool = False


@dataclass(frozen=True)
class RecordReduceOnlyFill:
    """Command: Record reduce-only exit fill receipt.

    Requires:
    - Exchange order ID
    - Trade IDs
    - Filled quantity <= managed position quantity
    """

    position_id: str
    exchange_order_id: str
    trade_ids: list[str]
    filled_quantity: Decimal
    average_fill_price: Decimal
    total_fee: Decimal
    fill_timestamp: datetime


@dataclass(frozen=True)
class QuarantinePosition:
    """Command: Quarantine position due to irrecoverable state.

    Triggers:
    - Reconciliation detected unmanaged external position
    - Protection submission failed after entry filled
    - Client order ID collision
    - Exchange order ID unknown after timeout

    Quarantined positions require manual intervention.
    """

    position_id: str
    quarantine_reason: str
    reconciliation_snapshot: dict | None = None
