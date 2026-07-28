"""V2 domain enumerations: immutable state machine states and event types.

Exchange-First Invariant:
- INTENT_CREATED: local decision made, no exchange contact
- EXCHANGE_SUBMITTING: order sent to exchange, awaiting ack
- EXCHANGE_ACKNOWLEDGED: exchange confirmed order receipt (order_id assigned)
- FILLED: exchange confirmed fill (requires fill receipt with trade_id)
- PROTECTED: stop-loss/take-profit submitted to exchange
- CLOSED: order/position fully closed

State transitions must be validated by validate_transition() before persistence.
"""

from __future__ import annotations

from enum import StrEnum


class V2IntentState(StrEnum):
    """Trading intent lifecycle states.

    INTENT_CREATED: Strategy decision made, not yet submitted to exchange.
    EXCHANGE_SUBMITTING: Order submitted to exchange, awaiting acknowledgment.
    EXCHANGE_UNKNOWN: Request left the process but the outcome is undetermined
        (timeout / connection reset). Resolution is by Client Order ID lookup
        only; resubmitting under a new Client Order ID is forbidden.
    EXCHANGE_ACKNOWLEDGED: Exchange confirmed receipt, order_id assigned.
    FILLED: Exchange confirmed fill (requires FillReceipt with trade_id).
    REJECTED: Exchange rejected the order.
    CANCELLED: Order cancelled before fill.
    EXPIRED: Order expired without fill.
    """

    INTENT_CREATED = "INTENT_CREATED"
    EXCHANGE_SUBMITTING = "EXCHANGE_SUBMITTING"
    EXCHANGE_UNKNOWN = "EXCHANGE_UNKNOWN"
    EXCHANGE_ACKNOWLEDGED = "EXCHANGE_ACKNOWLEDGED"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


class V2PositionState(StrEnum):
    """Managed position lifecycle states.

    POSITION_PROJECTED: Local projection of exchange position (requires FillReceipt).
    PROTECTED: Stop-loss/take-profit submitted to exchange.
    REDUCING: Reduce-only exit order submitted.
    CLOSED: Position fully closed.
    QUARANTINED: Reconciliation detected unmanageable state.
    """

    POSITION_PROJECTED = "POSITION_PROJECTED"
    PROTECTED = "PROTECTED"
    REDUCING = "REDUCING"
    CLOSED = "CLOSED"
    QUARANTINED = "QUARANTINED"


class V2ProtectionState(StrEnum):
    """Protection order lifecycle states.

    PROTECTION_INTENT: Calculated protection prices, not yet submitted.
    PROTECTION_SUBMITTING: Protection order sent to exchange.
    PROTECTION_ACTIVE: Exchange confirmed protection order (conditional order_id assigned).
    PROTECTION_TRIGGERED: Exchange reports protection triggered.
    PROTECTION_FILLED: Protection fill confirmed (requires FillReceipt).
    PROTECTION_CANCELLED: Protection cancelled (position closed by other exit).
    PROTECTION_FAILED: Submission failed after retry; escalation to emergency
        reduce-only close is required. Never a resting state for a live position.
    PROTECTION_UNKNOWN: Submission outcome undetermined (timeout). Resolved by
        Client Order ID lookup, never by blind resubmission.
    """

    PROTECTION_INTENT = "PROTECTION_INTENT"
    PROTECTION_SUBMITTING = "PROTECTION_SUBMITTING"
    PROTECTION_ACTIVE = "PROTECTION_ACTIVE"
    PROTECTION_TRIGGERED = "PROTECTION_TRIGGERED"
    PROTECTION_FILLED = "PROTECTION_FILLED"
    PROTECTION_CANCELLED = "PROTECTION_CANCELLED"
    PROTECTION_FAILED = "PROTECTION_FAILED"
    PROTECTION_UNKNOWN = "PROTECTION_UNKNOWN"


class V2ExecutionMode(StrEnum):
    """Execution backend mode.

    BINANCE_TESTNET: Real Binance USDT-M Simulation/Testnet orders.
    LOCAL_PAPER: Simulated fills from local bar data (no exchange contact).
    """

    BINANCE_TESTNET = "BINANCE_TESTNET"
    LOCAL_PAPER = "LOCAL_PAPER"


class V2CandidateType(StrEnum):
    """Strategy candidate classification.

    PRIMARY: Production-validated strategy candidate (promotable to live).
    SAMPLING: Testnet sampling candidate (never promotable, separate performance tracking).
    RESEARCH: Research-only candidate (no automatic execution).
    """

    PRIMARY = "PRIMARY"
    SAMPLING = "SAMPLING"
    RESEARCH = "RESEARCH"


class V2DecisionTerminal(StrEnum):
    """Decision funnel terminal reasons (why no entry was created).

    ENTRY_CREATED: Entry intent successfully created.
    NO_CLOSED_BAR: No new closed bar available.
    DUPLICATE_BAR: Bar already processed.
    NO_CANDIDATE: No eligible strategy candidate.
    REGIME_MISMATCH: Market regime does not match candidate requirements.
    TECHNICAL_SIGNALS_INSUFFICIENT: Technical signals below threshold.
    META_LABEL_REJECTED: Meta-label model rejected entry.
    NET_EDGE_NEGATIVE: Expected edge after costs is negative.
    ENTRY_GATE_BLOCKED: Risk gate blocked entry (drawdown/kill-switch/reconciliation).
    EXCHANGE_UNAVAILABLE: Exchange adapter unavailable.
    """

    ENTRY_CREATED = "ENTRY_CREATED"
    NO_CLOSED_BAR = "NO_CLOSED_BAR"
    DUPLICATE_BAR = "DUPLICATE_BAR"
    NO_CANDIDATE = "NO_CANDIDATE"
    REGIME_MISMATCH = "REGIME_MISMATCH"
    TECHNICAL_SIGNALS_INSUFFICIENT = "TECHNICAL_SIGNALS_INSUFFICIENT"
    META_LABEL_REJECTED = "META_LABEL_REJECTED"
    NET_EDGE_NEGATIVE = "NET_EDGE_NEGATIVE"
    ENTRY_GATE_BLOCKED = "ENTRY_GATE_BLOCKED"
    EXCHANGE_UNAVAILABLE = "EXCHANGE_UNAVAILABLE"
