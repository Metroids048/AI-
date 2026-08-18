"""Fail-closed reconciliation between exchange truth and local projection.

Reconciliation compares the authoritative Binance snapshot against the local V2
projection and produces a status that gates Entry. It never mutates the exchange
and never blocks reduce-only exits.

Status semantics (plan section 9.1):
- HEALTHY: complete snapshot, local/exchange explainable, Entry allowed.
- DEGRADED: snapshot available but a non-critical mismatch exists. Entry blocked
  for the affected symbols; Exit and Recovery allowed.
- UNAVAILABLE: gateway missing, REST timeout, incomplete snapshot, parse error,
  or ambiguous account identity. All Entry blocked; Exit retained.
- RECOVERY_REQUIRED: unknown orders, unprotected managed positions, protection
  inconsistent with position, or an exchange-side V2-looking client id with no
  local record. All Entry blocked; recovery must run first.

Ownership rules (plan section 9.2): a position may only be claimed via position
group id, client order id, exchange order id, or fill trade id. Symbol, quantity
proximity, price proximity, and time proximity are never sufficient. Unclaimable
exchange positions become EXTERNAL_QUARANTINED and block Entry for that symbol;
they are never auto-closed and never inherit historical protection.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING

from services.automated_trading.domain.client_order_id import is_v2_client_order_id

if TYPE_CHECKING:
    from services.automated_trading.infrastructure.market_snapshot_provider import (
        AuthoritativeAccountSnapshot,
    )


class ReconciliationStatus(StrEnum):
    """Reconciliation outcome. Only HEALTHY permits new Entry."""

    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNAVAILABLE = "UNAVAILABLE"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"


class OwnershipStatus(StrEnum):
    """How an exchange position maps to local V2 state."""

    MANAGED_V2 = "MANAGED_V2"
    EXTERNAL_QUARANTINED = "EXTERNAL_QUARANTINED"


class DiscrepancyCode(StrEnum):
    """Machine-readable reconciliation discrepancy codes."""

    SNAPSHOT_UNAVAILABLE = "SNAPSHOT_UNAVAILABLE"
    EXTERNAL_POSITION_UNCLAIMABLE = "EXTERNAL_POSITION_UNCLAIMABLE"
    LOCAL_POSITION_MISSING_AT_EXCHANGE = "LOCAL_POSITION_MISSING_AT_EXCHANGE"
    QUANTITY_MISMATCH = "QUANTITY_MISMATCH"
    DIRECTION_MISMATCH = "DIRECTION_MISMATCH"
    MANAGED_POSITION_UNPROTECTED = "MANAGED_POSITION_UNPROTECTED"
    PROTECTION_ORDER_MISSING_AT_EXCHANGE = "PROTECTION_ORDER_MISSING_AT_EXCHANGE"
    UNKNOWN_ORDER_PRESENT = "UNKNOWN_ORDER_PRESENT"
    ORPHAN_V2_CLIENT_ORDER_AT_EXCHANGE = "ORPHAN_V2_CLIENT_ORDER_AT_EXCHANGE"
    MANUAL_BASELINE_DRIFT = "MANUAL_BASELINE_DRIFT"


@dataclass(frozen=True)
class LocalPositionView:
    """Read-only local projection of a V2 managed position.

    claim_keys carries every identity that may be used to claim an exchange
    position: position id, client order ids, exchange order ids, trade ids.
    """

    position_id: str
    symbol: str
    direction: str
    quantity: Decimal
    state: str
    claim_keys: frozenset[str]
    has_active_protection: bool
    protection_exchange_order_ids: frozenset[str] = frozenset()
    # (exchange_order_id, client_order_id, exit_reason)
    protection_order_refs: tuple[tuple[str, str, str], ...] = ()
    entry_price: Decimal = Decimal("0")
    original_stop_price: Decimal | None = None
    current_stop_price: Decimal | None = None
    protection_id: str | None = None
    protection_policy: str = "P1"
    protection_version: int = 0


@dataclass(frozen=True)
class LocalIntentView:
    """Read-only local projection of an in-flight intent."""

    intent_id: str
    symbol: str
    client_order_id: str
    state: str


@dataclass(frozen=True)
class LocalStateView:
    """Aggregate local state passed into reconcile()."""

    positions: tuple[LocalPositionView, ...] = ()
    intents: tuple[LocalIntentView, ...] = ()
    known_client_order_ids: frozenset[str] = frozenset()
    v2_client_order_prefix: str = "v2_"
    # Explicit operator-captured external Testnet baseline. When present, a
    # same-direction exchange position may exceed a managed V2 position by
    # exactly this quantity; the baseline is never attributed to the strategy.
    external_baseline_positions: dict[str, Decimal] = field(default_factory=dict)


def runtime_snapshot_from_value(value: object, *, observed_at: datetime):
    """Convert the Runtime API exchange payload into the canonical snapshot type.

    The API probe intentionally stores a JSON-shaped payload for observability. This
    adapter is the only boundary that turns that payload back into the immutable
    reconciliation contract, so status and entry blocking cannot drift into a second
    ad-hoc implementation in the router.
    """
    from services.automated_trading.infrastructure.market_snapshot_provider import (
        AuthoritativeAccountSnapshot,
        ExchangeOrderSnapshot,
        ExchangePositionSnapshot,
    )

    if not isinstance(value, dict):
        raise ValueError("exchange snapshot must be an object")

    def _decimal(item: dict, *keys: str, default: str = "0") -> Decimal:
        for key in keys:
            raw = item.get(key)
            if raw not in (None, ""):
                return Decimal(str(raw))
        return Decimal(default)

    def _symbol(item: dict) -> str:
        raw = str(item.get("symbol") or "")
        if raw.endswith(":USDT"):
            raw = raw[:-5]
        if "/" not in raw and raw.endswith("USDT"):
            return f"{raw[:-4]}/USDT"
        return raw

    def _bool(item: dict, *keys: str) -> bool:
        for key in keys:
            if key not in item:
                continue
            raw = item[key]
            if isinstance(raw, bool):
                return raw
            if isinstance(raw, str):
                return raw.strip().lower() in {"1", "true", "yes", "y"}
            return bool(raw)
        return False

    positions: list[ExchangePositionSnapshot] = []
    for item in value.get("positions", ()):
        if not isinstance(item, dict):
            continue
        quantity = _decimal(item, "contracts", "quantity", "positionAmt")
        if quantity <= 0:
            continue
        direction = str(item.get("direction") or item.get("side") or "").lower()
        if direction not in {"long", "short"}:
            continue
        positions.append(
            ExchangePositionSnapshot(
                symbol=_symbol(item),
                direction=direction,
                quantity=quantity,
                entry_price=_decimal(item, "entry_price", "entryPrice", "avgEntryPrice"),
                mark_price=_decimal(item, "mark_price", "markPrice", default="0"),
                unrealized_pnl=_decimal(item, "unrealized_pnl", "unrealizedPnl", "unRealizedProfit"),
                leverage=(int(item["leverage"]) if item.get("leverage") not in (None, "") else None),
            )
        )

    orders: list[ExchangeOrderSnapshot] = []
    for item in value.get("open_orders", ()):
        if not isinstance(item, dict):
            continue
        order_status = str(item.get("status") or "new").lower()
        orders.append(
            ExchangeOrderSnapshot(
                exchange_order_id=str(
                    item.get("exchange_order_id") or item.get("algoId") or item.get("orderId") or item.get("id") or ""
                ),
                client_order_id=(
                    str(item.get("client_order_id") or item.get("clientAlgoId") or item.get("clientOrderId"))
                    if item.get("client_order_id") or item.get("clientAlgoId") or item.get("clientOrderId")
                    else None
                ),
                symbol=_symbol(item),
                side=str(item.get("side") or "").lower(),
                order_type=str(
                    item.get("order_type") or item.get("type") or item.get("orderType") or "unknown"
                ).lower(),
                quantity=_decimal(item, "quantity", "origQty", "qty", "algoQuantity"),
                price=(
                    _decimal(item, "price", "stopPrice", "triggerPrice")
                    if any(item.get(key) not in (None, "") for key in ("price", "stopPrice", "triggerPrice"))
                    else None
                ),
                status=order_status,
                reduce_only=_bool(item, "reduce_only", "reduceOnly"),
            )
        )

    account = value.get("account")
    account = account if isinstance(account, dict) else {}
    return AuthoritativeAccountSnapshot(
        balance=_decimal(account, "balance", "wallet_balance", "totalWalletBalance"),
        equity=_decimal(account, "equity", "total_equity", "totalMarginBalance"),
        positions=positions,
        pending_orders=orders,
        snapshot_timestamp=observed_at,
    )


def load_local_state_from_session(
    session,
    execution_mode,
    *,
    external_baseline_positions: dict[str, Decimal] | None = None,
) -> tuple[LocalStateView, dict[str, frozenset[str]]]:
    """Build the canonical V2 identity claims used by reconciliation.

    Runtime observability and the cycle runner must claim exchange positions from
    the same durable identity set.  This helper deliberately reads only V2 facts
    (orders, fills, protections and intents); symbol/quantity proximity never
    creates an ownership claim.
    """
    from sqlalchemy import select

    from services.automated_trading.infrastructure.models import (
        V2ExchangeFill,
        V2ExchangeOrder,
        V2ExecutionIntent,
        V2ProtectionRecord,
    )
    from services.automated_trading.infrastructure.repository import AutomatedTradingRepository

    mode_value = str(getattr(execution_mode, "value", execution_mode))
    repo = AutomatedTradingRepository(session)
    open_positions = repo.get_open_positions(execution_mode)
    positions: list[LocalPositionView] = []
    exchange_position_claim_refs: dict[str, frozenset[str]] = {}
    known_client_order_ids: set[str] = set()

    for position in open_positions:
        order = session.get(V2ExchangeOrder, position.order_record_id)
        fills = tuple(
            session.scalars(
                select(V2ExchangeFill).where(V2ExchangeFill.exchange_order_record_id == position.order_record_id)
            )
        )
        protections = tuple(
            session.scalars(select(V2ProtectionRecord).where(V2ProtectionRecord.position_id == position.position_id))
        )
        active_protections = tuple(p for p in protections if p.state == "PROTECTION_ACTIVE")
        active_protection = active_protections[-1] if active_protections else None
        claim_keys = {position.position_id, position.intent_id, position.order_record_id}
        if order is not None:
            claim_keys.add(order.client_order_id)
            known_client_order_ids.add(order.client_order_id)
            if order.exchange_order_id:
                claim_keys.add(order.exchange_order_id)
        for fill in fills:
            claim_keys.update(value for value in (fill.fill_id, fill.exchange_order_id, fill.trade_id) if value)

        protection_exchange_order_ids: set[str] = set()
        protection_order_refs: list[tuple[str, str, str]] = []
        for protection in protections:
            known_client_order_ids.add(protection.stop_client_order_id)
            if protection.tp_client_order_id:
                known_client_order_ids.add(protection.tp_client_order_id)
            for exchange_order_id in (protection.stop_exchange_order_id, protection.tp_exchange_order_id):
                if exchange_order_id:
                    protection_exchange_order_ids.add(exchange_order_id)
                    claim_keys.add(exchange_order_id)
            if protection.stop_exchange_order_id:
                protection_order_refs.append(
                    (protection.stop_exchange_order_id, protection.stop_client_order_id, "HARD_STOP")
                )
            if protection.tp_exchange_order_id and protection.tp_client_order_id:
                protection_order_refs.append(
                    (protection.tp_exchange_order_id, protection.tp_client_order_id, "TAKE_PROFIT")
                )

        frozen_claim_keys = frozenset(claim_keys)
        positions.append(
            LocalPositionView(
                position_id=position.position_id,
                symbol=position.symbol,
                direction=position.direction,
                quantity=Decimal(str(position.quantity)),
                state=position.state,
                claim_keys=frozen_claim_keys,
                has_active_protection=bool(active_protections),
                protection_exchange_order_ids=frozenset(protection_exchange_order_ids),
                protection_order_refs=tuple(protection_order_refs),
                entry_price=Decimal(str(position.entry_price)),
                original_stop_price=(
                    Decimal(str(active_protection.original_stop_loss_price or active_protection.stop_loss_price))
                    if active_protection is not None
                    else None
                ),
                current_stop_price=(
                    Decimal(str(active_protection.stop_loss_price)) if active_protection is not None else None
                ),
                protection_id=active_protection.protection_id if active_protection is not None else None,
                protection_policy=active_protection.policy if active_protection is not None else "P1",
                protection_version=int(active_protection.version) if active_protection is not None else 0,
            )
        )
        # A position is claimable only after an exchange order identity and a
        # confirmed fill receipt exist for the same durable V2 aggregate.
        if order is not None and order.exchange_order_id and fills:
            key = f"{position.symbol}:{position.direction}"
            exchange_position_claim_refs[key] = exchange_position_claim_refs.get(key, frozenset()) | frozen_claim_keys

    all_orders = tuple(session.scalars(select(V2ExchangeOrder)))
    known_client_order_ids.update(order.client_order_id for order in all_orders)
    order_by_intent = {order.intent_id: order for order in all_orders}
    pending_intents = tuple(
        session.scalars(
            select(V2ExecutionIntent).where(
                V2ExecutionIntent.execution_mode == mode_value,
                V2ExecutionIntent.state.in_(["EXCHANGE_SUBMITTING", "EXCHANGE_UNKNOWN", "EXCHANGE_ACKNOWLEDGED"]),
            )
        )
    )
    intents = tuple(
        LocalIntentView(
            intent_id=intent.intent_id,
            symbol=intent.symbol,
            client_order_id=order_by_intent[intent.intent_id].client_order_id,
            state=intent.state,
        )
        for intent in pending_intents
        if intent.intent_id in order_by_intent
    )
    return (
        LocalStateView(
            positions=tuple(positions),
            intents=intents,
            known_client_order_ids=frozenset(known_client_order_ids),
            external_baseline_positions=dict(external_baseline_positions or {}),
        ),
        exchange_position_claim_refs,
    )


@dataclass(frozen=True)
class Discrepancy:
    """A single reconciliation finding."""

    code: DiscrepancyCode
    symbol: str | None
    detail: str
    local_position_id: str | None = None
    exchange_ref: str | None = None


@dataclass(frozen=True)
class ReconciliationResult:
    """Reconciliation outcome. Entry decisions must consult this object only."""

    status: ReconciliationStatus
    discrepancies: tuple[Discrepancy, ...]
    entry_blocked_symbols: frozenset[str]
    quarantine_candidates: tuple[str, ...] = ()
    recovery_required_refs: tuple[str, ...] = ()
    unavailable_reason: str | None = None
    reconciled_at: datetime | None = None
    snapshot_positions: tuple[str, ...] = field(default=())

    @property
    def entry_allowed_globally(self) -> bool:
        """True only when reconciliation is HEALTHY.

        DEGRADED still blocks per-symbol Entry; UNAVAILABLE and RECOVERY_REQUIRED
        block all Entry.
        """
        return self.status is ReconciliationStatus.HEALTHY

    def entry_allowed_for(self, symbol: str) -> bool:
        """Entry permission for a specific symbol."""
        if self.status in (
            ReconciliationStatus.UNAVAILABLE,
            ReconciliationStatus.RECOVERY_REQUIRED,
        ):
            return False
        return symbol not in self.entry_blocked_symbols

    def reduce_only_exit_allowed(self) -> bool:
        """Reduce-only de-risking exit is always permitted.

        Invariant 6: reconciliation anomalies must never block reduce-only exit.
        """
        return True


QUANTITY_TOLERANCE = Decimal("0.00000001")


def unavailable(reason: str, *, reconciled_at: datetime | None = None) -> ReconciliationResult:
    """Build the fail-closed UNAVAILABLE result.

    Used when the snapshot could not be obtained at all (gateway missing, REST
    timeout, parse failure). An empty snapshot is never treated as "no problems".
    """
    return ReconciliationResult(
        status=ReconciliationStatus.UNAVAILABLE,
        discrepancies=(
            Discrepancy(
                code=DiscrepancyCode.SNAPSHOT_UNAVAILABLE,
                symbol=None,
                detail=reason,
            ),
        ),
        entry_blocked_symbols=frozenset(),
        unavailable_reason=reason,
        reconciled_at=reconciled_at,
    )


def _claim_exchange_position(
    *,
    exchange_symbol: str,
    exchange_direction: str,
    local_positions: tuple[LocalPositionView, ...],
    exchange_claim_refs: frozenset[str],
) -> LocalPositionView | None:
    """Claim an exchange position by identity only.

    Identity sources, in priority order: position group id, client order id,
    exchange order id, fill trade id. Symbol/quantity/price/time proximity is
    explicitly not an ownership proof.
    """
    if not exchange_claim_refs:
        return None

    for position in local_positions:
        if position.claim_keys & exchange_claim_refs:
            # Identity matched. Symbol/direction are consistency checks, not the claim.
            if position.symbol == exchange_symbol and position.direction == exchange_direction:
                return position
            return position
    return None


def reconcile(
    snapshot: AuthoritativeAccountSnapshot | None,
    local_state: LocalStateView,
    *,
    unavailable_reason: str | None = None,
    reconciled_at: datetime | None = None,
    exchange_position_claim_refs: dict[str, frozenset[str]] | None = None,
) -> ReconciliationResult:
    """Reconcile exchange truth against local projection.

    Args:
        snapshot: Authoritative exchange snapshot, or None when unobtainable.
        local_state: Local V2 projection.
        unavailable_reason: Why the snapshot is missing (required when snapshot is None).
        reconciled_at: Timestamp for the result.
        exchange_position_claim_refs: Per-exchange-position identity refs keyed by
            f"{symbol}:{direction}". Absent or empty refs mean the position cannot
            be claimed and must be quarantined.

    Returns:
        ReconciliationResult. Never raises for data mismatches; mismatches are
        reported as discrepancies with a fail-closed status.
    """
    if snapshot is None:
        return unavailable(
            unavailable_reason or "exchange snapshot unavailable",
            reconciled_at=reconciled_at,
        )

    claim_refs = exchange_position_claim_refs or {}
    discrepancies: list[Discrepancy] = []
    entry_blocked: set[str] = set()
    quarantine_candidates: list[str] = []
    recovery_refs: list[str] = []

    local_positions = local_state.positions
    matched_position_ids: set[str] = set()

    # --- Exchange positions -> local claim ---
    for exch_pos in snapshot.positions:
        key = f"{exch_pos.symbol}:{exch_pos.direction}"
        baseline_quantity = local_state.external_baseline_positions.get(key)
        refs = claim_refs.get(key, frozenset())
        claimed = _claim_exchange_position(
            exchange_symbol=exch_pos.symbol,
            exchange_direction=exch_pos.direction,
            local_positions=local_positions,
            exchange_claim_refs=refs,
        )

        if baseline_quantity is not None and exch_pos.quantity + QUANTITY_TOLERANCE < baseline_quantity:
            discrepancies.append(
                Discrepancy(
                    code=DiscrepancyCode.MANUAL_BASELINE_DRIFT,
                    symbol=exch_pos.symbol,
                    detail=(
                        f"captured manual baseline {baseline_quantity} exceeds current authoritative "
                        f"quantity {exch_pos.quantity} for {key}; operator acknowledgement is required"
                    ),
                    exchange_ref=key,
                )
            )
            entry_blocked.add(exch_pos.symbol)
            # Do not use a reduced manual net position as a managed V2 claim.
            if claimed is None:
                continue

        if claimed is None:
            # An explicitly captured same-direction baseline is allowed to
            # remain unmanaged. Any unrecorded quantity/direction remains
            # quarantined and blocks Entry.
            if baseline_quantity is not None and abs(exch_pos.quantity - baseline_quantity) <= QUANTITY_TOLERANCE:
                continue
            # Unclaimable: quarantine, block Entry for the symbol, never auto-close.
            discrepancies.append(
                Discrepancy(
                    code=DiscrepancyCode.EXTERNAL_POSITION_UNCLAIMABLE,
                    symbol=exch_pos.symbol,
                    detail=(
                        f"exchange {exch_pos.direction} position of {exch_pos.quantity} "
                        f"{exch_pos.symbol} has no local identity match; marked "
                        f"{OwnershipStatus.EXTERNAL_QUARANTINED.value}"
                    ),
                    exchange_ref=key,
                )
            )
            entry_blocked.add(exch_pos.symbol)
            quarantine_candidates.append(key)
            continue

        matched_position_ids.add(claimed.position_id)

        if claimed.direction != exch_pos.direction:
            discrepancies.append(
                Discrepancy(
                    code=DiscrepancyCode.DIRECTION_MISMATCH,
                    symbol=exch_pos.symbol,
                    detail=(
                        f"local {claimed.direction} vs exchange {exch_pos.direction} for position {claimed.position_id}"
                    ),
                    local_position_id=claimed.position_id,
                    exchange_ref=key,
                )
            )
            entry_blocked.add(exch_pos.symbol)

        expected_quantity = claimed.quantity + (baseline_quantity or Decimal("0"))
        if abs(expected_quantity - exch_pos.quantity) > QUANTITY_TOLERANCE:
            discrepancies.append(
                Discrepancy(
                    code=DiscrepancyCode.QUANTITY_MISMATCH,
                    symbol=exch_pos.symbol,
                    detail=(
                        f"local quantity {claimed.quantity} + external baseline {baseline_quantity or 0} "
                        f"vs exchange {exch_pos.quantity} "
                        f"for position {claimed.position_id}"
                    ),
                    local_position_id=claimed.position_id,
                    exchange_ref=key,
                )
            )
            entry_blocked.add(exch_pos.symbol)

        # A live exchange position with no active local protection needs recovery.
        if not claimed.has_active_protection:
            discrepancies.append(
                Discrepancy(
                    code=DiscrepancyCode.MANAGED_POSITION_UNPROTECTED,
                    symbol=exch_pos.symbol,
                    detail=f"managed position {claimed.position_id} has no active protection",
                    local_position_id=claimed.position_id,
                )
            )
            entry_blocked.add(exch_pos.symbol)
            recovery_refs.append(claimed.position_id)

        # Protection recorded locally must still exist at the exchange.
        exchange_order_ids = {order.exchange_order_id for order in snapshot.pending_orders}
        missing_protection = claimed.protection_exchange_order_ids - exchange_order_ids
        if claimed.has_active_protection and missing_protection:
            discrepancies.append(
                Discrepancy(
                    code=DiscrepancyCode.PROTECTION_ORDER_MISSING_AT_EXCHANGE,
                    symbol=exch_pos.symbol,
                    detail=(
                        f"protection orders {sorted(missing_protection)} recorded ACTIVE locally "
                        f"are absent from exchange open orders"
                    ),
                    local_position_id=claimed.position_id,
                )
            )
            entry_blocked.add(exch_pos.symbol)
            recovery_refs.append(claimed.position_id)

    # --- Local open positions with no exchange counterpart ---
    for position in local_positions:
        if position.state in ("CLOSED", "QUARANTINED"):
            continue
        if position.position_id in matched_position_ids:
            continue
        discrepancies.append(
            Discrepancy(
                code=DiscrepancyCode.LOCAL_POSITION_MISSING_AT_EXCHANGE,
                symbol=position.symbol,
                detail=(
                    f"local position {position.position_id} ({position.state}) has no exchange "
                    "position; local projection is stale or a ghost row"
                ),
                local_position_id=position.position_id,
            )
        )
        entry_blocked.add(position.symbol)
        recovery_refs.append(position.position_id)

    # A captured manual position is allowed to change through operator action.
    # Its disappearance or reduction is a symbol-scoped acknowledgement state,
    # not a managed V2 recovery failure.  Keep the old baseline until an
    # explicit operator rebaseline is recorded.
    exchange_position_keys = {f"{p.symbol}:{p.direction}" for p in snapshot.positions}
    for key, baseline_quantity in local_state.external_baseline_positions.items():
        if key in exchange_position_keys:
            continue
        symbol, _direction = key.rsplit(":", 1)
        discrepancies.append(
            Discrepancy(
                code=DiscrepancyCode.MANUAL_BASELINE_DRIFT,
                symbol=symbol,
                detail=(
                    f"captured manual baseline {baseline_quantity} at {key} is absent from the "
                    "authoritative snapshot; operator acknowledgement is required before rebaseline"
                ),
                exchange_ref=key,
            )
        )
        entry_blocked.add(symbol)

    # --- Unknown / orphan exchange orders ---
    for order in snapshot.pending_orders:
        client_id = order.client_order_id
        if client_id is None:
            discrepancies.append(
                Discrepancy(
                    code=DiscrepancyCode.UNKNOWN_ORDER_PRESENT,
                    symbol=order.symbol,
                    detail=f"exchange order {order.exchange_order_id} has no client order id",
                    exchange_ref=order.exchange_order_id,
                )
            )
            entry_blocked.add(order.symbol)
            recovery_refs.append(order.exchange_order_id)
            continue

        if client_id in local_state.known_client_order_ids:
            continue

        if is_v2_client_order_id(client_id):
            # A V2-shaped client id we have no local record for: recovery, never a blind resubmit.
            discrepancies.append(
                Discrepancy(
                    code=DiscrepancyCode.ORPHAN_V2_CLIENT_ORDER_AT_EXCHANGE,
                    symbol=order.symbol,
                    detail=(
                        f"exchange order {order.exchange_order_id} carries V2 client id {client_id} "
                        "with no local record"
                    ),
                    exchange_ref=order.exchange_order_id,
                )
            )
            entry_blocked.add(order.symbol)
            recovery_refs.append(order.exchange_order_id)

    status = _resolve_status(discrepancies)

    return ReconciliationResult(
        status=status,
        discrepancies=tuple(discrepancies),
        entry_blocked_symbols=frozenset(entry_blocked),
        quarantine_candidates=tuple(quarantine_candidates),
        recovery_required_refs=tuple(dict.fromkeys(recovery_refs)),
        reconciled_at=reconciled_at,
        snapshot_positions=tuple(f"{p.symbol}:{p.direction}:{p.quantity}" for p in snapshot.positions),
    )


_RECOVERY_CODES = frozenset(
    {
        DiscrepancyCode.MANAGED_POSITION_UNPROTECTED,
        DiscrepancyCode.PROTECTION_ORDER_MISSING_AT_EXCHANGE,
        DiscrepancyCode.UNKNOWN_ORDER_PRESENT,
        DiscrepancyCode.ORPHAN_V2_CLIENT_ORDER_AT_EXCHANGE,
        DiscrepancyCode.LOCAL_POSITION_MISSING_AT_EXCHANGE,
    }
)


def _resolve_status(discrepancies: list[Discrepancy]) -> ReconciliationStatus:
    """Escalate to the most severe applicable status."""
    if not discrepancies:
        return ReconciliationStatus.HEALTHY
    codes = {d.code for d in discrepancies}
    if codes & _RECOVERY_CODES:
        return ReconciliationStatus.RECOVERY_REQUIRED
    return ReconciliationStatus.DEGRADED
