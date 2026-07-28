"""Entry Gate and Exchange-First entry execution (plan sections 6, 8.1, 11.3).

Two separate concerns live here, and the separation is deliberate:

``evaluate_entry`` is a pure predicate. It answers "is opening risk permitted
right now?" from a runtime context, makes no network call, and has no side
effects. Every rejection carries a stable reason code.

``execute_entry`` performs the Exchange-First submission. Its contract:
- SHADOW never submits. It returns a rehearsed result instead.
- A submission failure creates no local position, ever.
- A timeout yields EXCHANGE_UNKNOWN, never a retry under a new Client Order ID.
- A position is projected only from a real fill receipt carrying an exchange
  order id, at least one trade id, positive quantity and positive price.
- Partial fills project only the confirmed quantity, never the requested one.

Entry Gate blocks listed in plan section 8.1 apply *only* to opening risk. None
of them may block a reduce-only exit; that is the exit service's separate gate.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_DOWN, Decimal
from enum import StrEnum
from typing import TYPE_CHECKING

from services.automated_trading.application.reconciliation_service import ReconciliationStatus
from services.automated_trading.domain.client_order_id import entry_client_order_id
from services.automated_trading.domain.enums import V2ExecutionMode, V2IntentState
from services.automated_trading.infrastructure.runtime_lock import EngineActivation
from services.automated_trading.observability.decision_funnel import DecisionReasonCode

if TYPE_CHECKING:
    from services.automated_trading.domain.candidates import TradeCandidate
    from services.automated_trading.infrastructure.market_snapshot_provider import (
        PreSubmitMarketSnapshot,
    )

# Sampling default drift ceiling (plan section 11.3):
#   max(20 bps, 0.25 * ATR / reference_price * 10000)
MIN_DRIFT_CEILING_BPS = Decimal("20")
ATR_DRIFT_FRACTION = Decimal("0.25")


class EntryDecision(StrEnum):
    """Outcome of the Entry Gate."""

    APPROVED = "APPROVED"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True)
class EntryGateResult:
    """Immutable Entry Gate verdict.

    ``blocks`` lists every failed check, not just the first, so an operator sees
    the full picture rather than fixing one blocker at a time.
    """

    decision: EntryDecision
    reason_code: DecisionReasonCode
    blocks: tuple[tuple[DecisionReasonCode, str], ...] = ()
    drift_bps: Decimal | None = None
    drift_ceiling_bps: Decimal | None = None

    @property
    def approved(self) -> bool:
        return self.decision is EntryDecision.APPROVED


@dataclass(frozen=True)
class EntryRuntimeContext:
    """Everything the Entry Gate needs. No exchange calls are made from it."""

    engine_activation: EngineActivation
    execution_mode: V2ExecutionMode
    reconciliation_status: ReconciliationStatus
    entry_blocked_symbols: frozenset[str] = frozenset()
    entry_kill_switch_active: bool = False
    recovery_entry_blocked: bool = False
    open_position_symbols: frozenset[str] = frozenset()
    symbol_cooldown_active: bool = False
    daily_trade_limit_reached: bool = False
    manifest_eligible: bool = True
    net_edge_after_cost_bps: Decimal | None = None
    risk_budget_available: bool = True
    ai_advisory_veto: bool = False
    now: datetime | None = None


def drift_ceiling_bps(candidate: TradeCandidate, snapshot: PreSubmitMarketSnapshot) -> Decimal:
    """Resolve the drift ceiling for this candidate and market state.

    The candidate's own tolerance is honoured when it is stricter than the
    ATR-scaled floor; a volatile market must not silently widen a deliberately
    tight candidate.
    """
    atr_component = Decimal("0")
    if snapshot.atr > 0 and candidate.signal_reference_price > 0:
        atr_component = ATR_DRIFT_FRACTION * snapshot.atr / candidate.signal_reference_price * Decimal("10000")

    ceiling = max(MIN_DRIFT_CEILING_BPS, atr_component)
    return min(ceiling, candidate.max_entry_drift_bps) if candidate.max_entry_drift_bps > 0 else ceiling


def evaluate_entry(
    candidate: TradeCandidate,
    runtime: EntryRuntimeContext,
    snapshot: PreSubmitMarketSnapshot | None = None,
) -> EntryGateResult:
    """Decide whether opening risk is permitted. Pure; no side effects.

    ``snapshot`` is optional so the gate can be evaluated before a pre-submit
    read. When it is absent the price-drift check is deferred rather than
    silently passed.
    """
    blocks: list[tuple[DecisionReasonCode, str]] = []

    # --- Engine activation ---
    if runtime.engine_activation is EngineActivation.DISABLED:
        blocks.append((DecisionReasonCode.SHADOW_MODE_NO_SUBMIT, "V2 engine is DISABLED"))

    # --- Kill switch (entry only; never blocks reduce-only exit) ---
    if runtime.entry_kill_switch_active:
        blocks.append((DecisionReasonCode.ENTRY_KILL_SWITCH_ACTIVE, "entry kill switch is active"))

    # --- Reconciliation must be HEALTHY to add risk ---
    if runtime.reconciliation_status is ReconciliationStatus.UNAVAILABLE:
        blocks.append((DecisionReasonCode.RECONCILIATION_UNAVAILABLE, "exchange truth unavailable; cannot add risk"))
    elif runtime.reconciliation_status is ReconciliationStatus.RECOVERY_REQUIRED:
        blocks.append((DecisionReasonCode.RECOVERY_REQUIRED, "recovery must complete before new entry"))
    elif runtime.reconciliation_status is ReconciliationStatus.DEGRADED:
        blocks.append((DecisionReasonCode.RECONCILIATION_DEGRADED, "reconciliation is DEGRADED"))

    if runtime.recovery_entry_blocked:
        blocks.append((DecisionReasonCode.RECOVERY_REQUIRED, "recovery pass has not cleared the entry block"))

    if candidate.symbol in runtime.entry_blocked_symbols:
        blocks.append(
            (
                DecisionReasonCode.UNMANAGED_EXTERNAL_POSITION,
                f"{candidate.symbol} is entry-blocked by reconciliation",
            )
        )

    # --- One position per symbol ---
    if candidate.symbol in runtime.open_position_symbols:
        blocks.append((DecisionReasonCode.POSITION_ALREADY_OPEN, f"{candidate.symbol} already has an open position"))

    # --- Sampling throttles ---
    if runtime.symbol_cooldown_active:
        blocks.append((DecisionReasonCode.SYMBOL_COOLDOWN_ACTIVE, f"{candidate.symbol} is in cooldown"))
    if runtime.daily_trade_limit_reached:
        blocks.append((DecisionReasonCode.DAILY_TRADE_LIMIT_REACHED, "daily trade limit reached"))

    # --- Manifest / edge / risk budget ---
    if not runtime.manifest_eligible:
        blocks.append((DecisionReasonCode.MANIFEST_NOT_ELIGIBLE, "active manifest does not admit this candidate"))
    if runtime.net_edge_after_cost_bps is not None and runtime.net_edge_after_cost_bps <= 0:
        blocks.append(
            (
                DecisionReasonCode.NET_EDGE_AFTER_COST_NEGATIVE,
                f"net edge after cost is {runtime.net_edge_after_cost_bps} bps",
            )
        )
    if not runtime.risk_budget_available:
        blocks.append((DecisionReasonCode.RISK_LIMIT_EXCEEDED, "risk budget exhausted"))

    # --- AI advisory veto (advisory only, and only for opening risk) ---
    if runtime.ai_advisory_veto:
        blocks.append((DecisionReasonCode.AI_ADVISORY_VETO, "AI advisory vetoed this entry"))

    # --- Candidate expiry ---
    if runtime.now is not None and candidate.is_expired(runtime.now):
        blocks.append(
            (
                DecisionReasonCode.CANDIDATE_EXPIRED,
                f"candidate expired at {candidate.expires_at.isoformat()}",
            )
        )

    # --- Price drift ---
    drift = None
    ceiling = None
    if snapshot is not None:
        drift = candidate.drift_bps_from(snapshot.current_price)
        ceiling = drift_ceiling_bps(candidate, snapshot)
        if drift > ceiling:
            blocks.append(
                (
                    DecisionReasonCode.PRICE_DRIFT_EXCEEDED,
                    f"drift {drift:.2f} bps exceeds ceiling {ceiling:.2f} bps; wait for the next closed bar",
                )
            )

    if blocks:
        return EntryGateResult(
            decision=EntryDecision.BLOCKED,
            reason_code=blocks[0][0],
            blocks=tuple(blocks),
            drift_bps=drift,
            drift_ceiling_bps=ceiling,
        )

    return EntryGateResult(
        decision=EntryDecision.APPROVED,
        reason_code=DecisionReasonCode.OK,
        drift_bps=drift,
        drift_ceiling_bps=ceiling,
    )


# ---------------------------------------------------------------------------
# Exchange-First entry execution
# ---------------------------------------------------------------------------


class EntryExecutionStatus(StrEnum):
    """Terminal status of an entry execution attempt."""

    NOT_ATTEMPTED = "NOT_ATTEMPTED"
    SHADOW_REHEARSED = "SHADOW_REHEARSED"
    FILLED = "FILLED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    ACKNOWLEDGED_UNFILLED = "ACKNOWLEDGED_UNFILLED"
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class EntryExecutionResult:
    """Outcome of an entry submission.

    ``position_projectable`` is the single authority on whether a local managed
    position may be created. It is True only for a real fill with a real
    exchange order id and at least one trade id.
    """

    status: EntryExecutionStatus
    intent_state: V2IntentState
    client_order_id: str
    reason_code: DecisionReasonCode = DecisionReasonCode.OK
    exchange_order_id: str | None = None
    trade_ids: tuple[str, ...] = ()
    filled_quantity: Decimal = Decimal("0")
    average_fill_price: Decimal | None = None
    total_fee: Decimal = Decimal("0")
    fill_timestamp: datetime | None = None
    detail: str = ""
    requested_quantity: Decimal = Decimal("0")

    @property
    def position_projectable(self) -> bool:
        """Exchange-First gate on creating local position state."""
        return (
            self.status in {EntryExecutionStatus.FILLED, EntryExecutionStatus.PARTIALLY_FILLED}
            and bool(self.exchange_order_id)
            and bool(self.trade_ids)
            and self.filled_quantity > 0
            and self.average_fill_price is not None
            and self.average_fill_price > 0
        )

    @property
    def requires_client_order_id_recovery(self) -> bool:
        """UNKNOWN must be resolved by Client Order ID lookup, never by resubmit."""
        return self.status is EntryExecutionStatus.UNKNOWN


class ExchangeTimeout(Exception):
    """Raised by an adapter when a request was sent but the outcome is undetermined."""


def round_quantity_to_step(quantity: Decimal, step_size: Decimal) -> Decimal:
    """Floor a quantity to the exchange step size.

    Flooring, never rounding: rounding up can exceed the intended risk budget.
    """
    if step_size <= 0:
        raise ValueError(f"step_size must be > 0, got {step_size}")
    return (quantity / step_size).to_integral_value(rounding=ROUND_DOWN) * step_size


def _aggregate_fills(
    fills: tuple,
) -> tuple[Decimal, Decimal | None, Decimal, tuple[str, ...], datetime | None]:
    """Aggregate fill receipts into (quantity, vwap, fee, trade_ids, last_timestamp).

    Only confirmed exchange fills are aggregated. The requested quantity is
    never substituted for a missing fill.
    """
    total_quantity = Decimal("0")
    notional = Decimal("0")
    total_fee = Decimal("0")
    trade_ids: list[str] = []
    last_timestamp: datetime | None = None

    for fill in fills:
        total_quantity += fill.filled_quantity
        notional += fill.filled_quantity * fill.fill_price
        total_fee += fill.fee
        trade_ids.append(fill.trade_id)
        if last_timestamp is None or fill.fill_timestamp > last_timestamp:
            last_timestamp = fill.fill_timestamp

    vwap = (notional / total_quantity) if total_quantity > 0 else None
    return total_quantity, vwap, total_fee, tuple(trade_ids), last_timestamp


def execute_entry(
    candidate: TradeCandidate,
    gate_result: EntryGateResult,
    snapshot: PreSubmitMarketSnapshot,
    *,
    adapter,
    intent_id: str,
    quantity: Decimal,
    leverage: int,
    engine_activation: EngineActivation,
) -> EntryExecutionResult:
    """Submit an entry to the exchange under the Exchange-First contract.

    Args:
        candidate: The approved candidate.
        gate_result: Entry Gate verdict; a blocked verdict short-circuits.
        snapshot: Pre-submit market snapshot (supplies step size / min notional).
        adapter: Exchange adapter exposing ``submit_market_order`` and ``fetch_fills``.
        intent_id: Intent identifier; determines the Client Order ID.
        quantity: Requested quantity before step-size rounding.
        leverage: Requested leverage.
        engine_activation: SHADOW rehearses, ACTIVE submits.

    Returns:
        EntryExecutionResult. Never raises for exchange failures; they are
        reported as terminal statuses so the caller can record a funnel stage.
    """
    from services.automated_trading.domain.commands import SubmitEntryToExchange
    from services.automated_trading.infrastructure.binance_adapter import BinanceAdapterUnavailable

    client_order_id = entry_client_order_id(intent_id)

    if not gate_result.approved:
        return EntryExecutionResult(
            status=EntryExecutionStatus.NOT_ATTEMPTED,
            intent_state=V2IntentState.INTENT_CREATED,
            client_order_id=client_order_id,
            reason_code=gate_result.reason_code,
            detail="entry gate blocked; no submission attempted",
            requested_quantity=quantity,
        )

    normalized_quantity = round_quantity_to_step(quantity, snapshot.step_size)
    if normalized_quantity <= 0:
        return EntryExecutionResult(
            status=EntryExecutionStatus.NOT_ATTEMPTED,
            intent_state=V2IntentState.INTENT_CREATED,
            client_order_id=client_order_id,
            reason_code=DecisionReasonCode.RISK_LIMIT_EXCEEDED,
            detail=f"quantity {quantity} rounds to zero at step size {snapshot.step_size}",
            requested_quantity=quantity,
        )

    notional = normalized_quantity * snapshot.current_price
    if notional < snapshot.min_notional:
        return EntryExecutionResult(
            status=EntryExecutionStatus.NOT_ATTEMPTED,
            intent_state=V2IntentState.INTENT_CREATED,
            client_order_id=client_order_id,
            reason_code=DecisionReasonCode.RISK_LIMIT_EXCEEDED,
            detail=f"notional {notional} below exchange minimum {snapshot.min_notional}",
            requested_quantity=normalized_quantity,
        )

    # SHADOW must never reach the exchange.
    if engine_activation is not EngineActivation.ACTIVE:
        return EntryExecutionResult(
            status=EntryExecutionStatus.SHADOW_REHEARSED,
            intent_state=V2IntentState.INTENT_CREATED,
            client_order_id=client_order_id,
            reason_code=DecisionReasonCode.SHADOW_MODE_NO_SUBMIT,
            detail=f"{engine_activation.value} rehearsed {normalized_quantity} {candidate.symbol}; no submission",
            requested_quantity=normalized_quantity,
        )

    command = SubmitEntryToExchange(
        intent_id=intent_id,
        quantity=normalized_quantity,
        leverage=leverage,
        client_order_id=client_order_id,
    )
    side = "buy" if candidate.side == "LONG" else "sell"

    try:
        receipt = adapter.submit_market_order(command, candidate.symbol, side)
    except ExchangeTimeout as exc:
        # Request left the process; the exchange may or may not have it.
        return EntryExecutionResult(
            status=EntryExecutionStatus.UNKNOWN,
            intent_state=V2IntentState.EXCHANGE_UNKNOWN,
            client_order_id=client_order_id,
            reason_code=DecisionReasonCode.EXCHANGE_UNKNOWN,
            detail=f"submission outcome undetermined: {exc}. Resolve by client order id lookup.",
            requested_quantity=normalized_quantity,
        )
    except BinanceAdapterUnavailable as exc:
        # No request was sent: pre-submit failure, no exchange-side risk.
        return EntryExecutionResult(
            status=EntryExecutionStatus.REJECTED,
            intent_state=V2IntentState.REJECTED,
            client_order_id=client_order_id,
            reason_code=DecisionReasonCode.EXCHANGE_UNAVAILABLE,
            detail=f"pre-submit failure: {exc}",
            requested_quantity=normalized_quantity,
        )

    try:
        fills = adapter.fetch_fills(candidate.symbol, receipt.exchange_order_id)
    except (BinanceAdapterUnavailable, ExchangeTimeout) as exc:
        # The order exists but its fills are unread. Acknowledged, not filled.
        return EntryExecutionResult(
            status=EntryExecutionStatus.ACKNOWLEDGED_UNFILLED,
            intent_state=V2IntentState.EXCHANGE_ACKNOWLEDGED,
            client_order_id=client_order_id,
            reason_code=DecisionReasonCode.EXCHANGE_UNKNOWN,
            exchange_order_id=receipt.exchange_order_id,
            detail=f"order acknowledged but fills unavailable: {exc}",
            requested_quantity=normalized_quantity,
        )

    filled_quantity, vwap, total_fee, trade_ids, fill_timestamp = _aggregate_fills(fills)

    if not trade_ids or filled_quantity <= 0 or vwap is None:
        return EntryExecutionResult(
            status=EntryExecutionStatus.ACKNOWLEDGED_UNFILLED,
            intent_state=V2IntentState.EXCHANGE_ACKNOWLEDGED,
            client_order_id=client_order_id,
            reason_code=DecisionReasonCode.OK,
            exchange_order_id=receipt.exchange_order_id,
            detail="order acknowledged with no confirmed fill; no position projected",
            requested_quantity=normalized_quantity,
        )

    partial = filled_quantity < normalized_quantity
    return EntryExecutionResult(
        status=EntryExecutionStatus.PARTIALLY_FILLED if partial else EntryExecutionStatus.FILLED,
        intent_state=V2IntentState.FILLED,
        client_order_id=client_order_id,
        reason_code=DecisionReasonCode.OK,
        exchange_order_id=receipt.exchange_order_id,
        trade_ids=trade_ids,
        filled_quantity=filled_quantity,
        average_fill_price=vwap,
        total_fee=total_fee,
        fill_timestamp=fill_timestamp,
        detail=(
            f"filled {filled_quantity} of {normalized_quantity} at vwap {vwap}"
            if partial
            else f"filled {filled_quantity} at vwap {vwap}"
        ),
        requested_quantity=normalized_quantity,
    )
