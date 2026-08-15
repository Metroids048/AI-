"""V2 execution repository: append-only event persistence with forced transitions.

Exchange-First persistence rules:
- Cannot create V2ManagedPosition without confirmed fills in v2_exchange_fills
- State mutations go through transition_* (domain validated + event appended)
- Client order IDs, exchange order IDs, and (account_id, trade_id) must be unique
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from services.automated_trading.domain.invariants import (
    assert_fill_receipt_valid,
    assert_protection_active_requires_exchange_order_id,
)
from services.automated_trading.domain.receipts import FillReceipt, ProtectionReceipt
from services.automated_trading.domain.state import (
    validate_intent_transition,
    validate_position_transition,
    validate_protection_transition,
)
from services.automated_trading.infrastructure.models import (
    V2DecisionSnapshot,
    V2ExchangeFill,
    V2ExchangeOrder,
    V2ExecutionCycle,
    V2ExecutionDecision,
    V2ExecutionEvent,
    V2ExecutionIncident,
    V2ExecutionIntent,
    V2ManagedPosition,
    V2ProtectionRecord,
    V2ReconciliationSnapshot,
    V2RuntimeControl,
    V2ShadowOutcome,
    V2ShadowRecord,
)
from services.strategy_library.canonical import canonical_hash

if TYPE_CHECKING:
    from services.automated_trading.domain.enums import (
        V2CandidateType,
        V2ExecutionMode,
        V2IntentState,
        V2PositionState,
        V2ProtectionState,
    )


class AutomatedTradingRepository:
    """Repository for V2 automated trading persistence."""

    def __init__(self, session: Session) -> None:
        self.session = session

    # ------------------------------------------------------------------
    # Cycle / Decision / Intent
    # ------------------------------------------------------------------

    def create_cycle(
        self,
        *,
        cycle_id: str,
        symbol: str,
        timeframe: str,
        bar_timestamp: datetime,
        execution_mode: V2ExecutionMode,
        fencing_token: str,
    ) -> V2ExecutionCycle:
        cycle = V2ExecutionCycle(
            cycle_id=cycle_id,
            symbol=symbol,
            timeframe=timeframe,
            bar_timestamp=bar_timestamp,
            execution_mode=execution_mode.value,
            fencing_token=fencing_token,
        )
        self.session.add(cycle)
        self.session.flush()
        return cycle

    def complete_cycle(
        self,
        cycle_id: str,
        decision_terminal: str,
        completed_at: datetime,
    ) -> None:
        cycle = self.session.get(V2ExecutionCycle, cycle_id)
        if cycle is None:
            raise ValueError(f"Cycle {cycle_id!r} not found")
        cycle.decision_terminal = decision_terminal
        cycle.completed_at = completed_at
        self.session.flush()

    def create_decision(
        self,
        *,
        decision_id: str,
        cycle_id: str,
        candidate_key: str | None = None,
        terminal_reason: str | None = None,
        payload: dict | None = None,
    ) -> V2ExecutionDecision:
        decision = V2ExecutionDecision(
            decision_id=decision_id,
            cycle_id=cycle_id,
            candidate_key=candidate_key,
            terminal_reason=terminal_reason,
            payload=payload or {},
        )
        self.session.add(decision)
        self.session.flush()
        return decision

    def append_forward_snapshot(
        self,
        *,
        snapshot_id: str,
        cycle_id: str,
        decision_id: str,
        symbol: str,
        decision_time: datetime,
        snapshot_hash: str,
        payload: dict,
    ) -> V2DecisionSnapshot:
        """Insert one hash-sealed snapshot, refusing divergent rewrites."""
        existing = self.session.get(V2DecisionSnapshot, snapshot_id)
        if existing is not None:
            if existing.snapshot_hash != snapshot_hash or existing.payload != payload:
                raise ValueError("FORWARD_SNAPSHOT_IMMUTABLE_VIOLATION")
            return existing
        existing_decision = self.session.scalar(
            select(V2DecisionSnapshot).where(V2DecisionSnapshot.decision_id == decision_id)
        )
        if existing_decision is not None:
            if existing_decision.snapshot_hash != snapshot_hash:
                raise ValueError("FORWARD_SNAPSHOT_DECISION_IMMUTABLE_VIOLATION")
            return existing_decision
        row = V2DecisionSnapshot(
            snapshot_id=snapshot_id,
            cycle_id=cycle_id,
            decision_id=decision_id,
            symbol=symbol,
            decision_time=decision_time,
            snapshot_hash=snapshot_hash,
            payload=payload,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def append_shadow_records(self, *, snapshot_id: str, records: list[dict]) -> list[V2ShadowRecord]:
        """Insert idempotent, non-executable Shadow evidence rows."""
        result: list[V2ShadowRecord] = []
        for record in records:
            variant = str(record["variant"])
            payload = dict(record)
            payload_hash = canonical_hash(payload)
            existing = self.session.scalar(
                select(V2ShadowRecord).where(
                    V2ShadowRecord.snapshot_id == snapshot_id,
                    V2ShadowRecord.variant == variant,
                )
            )
            if existing is not None:
                if existing.payload_hash != payload_hash or existing.payload != payload:
                    raise ValueError("SHADOW_RECORD_IMMUTABLE_VIOLATION")
                result.append(existing)
                continue
            row = V2ShadowRecord(
                snapshot_id=snapshot_id,
                variant=variant,
                payload_hash=payload_hash,
                payload=payload,
            )
            self.session.add(row)
            self.session.flush()
            result.append(row)
        return result

    def append_shadow_outcome(self, *, shadow_id: str, payload: dict) -> V2ShadowOutcome:
        """Append one immutable outcome backfill for a Shadow row."""
        outcome_hash = canonical_hash(payload)
        existing = self.session.scalar(select(V2ShadowOutcome).where(V2ShadowOutcome.shadow_id == shadow_id))
        if existing is not None:
            if existing.outcome_hash != outcome_hash or existing.payload != payload:
                raise ValueError("SHADOW_OUTCOME_IMMUTABLE_VIOLATION")
            return existing
        row = V2ShadowOutcome(shadow_id=shadow_id, outcome_hash=outcome_hash, payload=dict(payload))
        self.session.add(row)
        self.session.flush()
        return row

    def list_forward_snapshots(
        self,
        *,
        cycle_id: str | None = None,
        symbol: str | None = None,
        limit: int | None = None,
    ) -> list[V2DecisionSnapshot]:
        stmt = select(V2DecisionSnapshot).order_by(V2DecisionSnapshot.decision_time.asc())
        if cycle_id is not None:
            stmt = stmt.where(V2DecisionSnapshot.cycle_id == cycle_id)
        if symbol is not None:
            stmt = stmt.where(V2DecisionSnapshot.symbol == symbol)
        if limit is not None:
            stmt = stmt.limit(limit)
        return list(self.session.scalars(stmt))

    def list_shadow_records(
        self,
        *,
        snapshot_id: str | None = None,
        variant: str | None = None,
        limit: int | None = None,
    ) -> list[V2ShadowRecord]:
        stmt = select(V2ShadowRecord).order_by(V2ShadowRecord.created_at.asc())
        if snapshot_id is not None:
            stmt = stmt.where(V2ShadowRecord.snapshot_id == snapshot_id)
        if variant is not None:
            stmt = stmt.where(V2ShadowRecord.variant == variant)
        if limit is not None:
            stmt = stmt.limit(limit)
        return list(self.session.scalars(stmt))

    def create_intent(
        self,
        *,
        intent_id: str,
        cycle_id: str,
        symbol: str,
        direction: str,
        candidate_key: str,
        candidate_type: V2CandidateType,
        execution_mode: V2ExecutionMode,
        decision_bar_timestamp: datetime,
        decision_funnel_id: str | None,
        state: V2IntentState,
        decision_id: str | None = None,
    ) -> None:
        intent = V2ExecutionIntent(
            intent_id=intent_id,
            cycle_id=cycle_id,
            decision_id=decision_id,
            symbol=symbol,
            direction=direction,
            candidate_key=candidate_key,
            candidate_type=candidate_type.value,
            execution_mode=execution_mode.value,
            decision_bar_timestamp=decision_bar_timestamp,
            decision_funnel_id=decision_funnel_id,
            state=state.value,
            version=0,
        )
        self.session.add(intent)
        self.session.flush()

    def transition_intent(
        self,
        intent_id: str,
        expected_current: V2IntentState,
        next_state: V2IntentState,
        event_type: str,
        payload: dict,
        occurred_at: datetime | None = None,
    ) -> None:
        intent = self.session.get(V2ExecutionIntent, intent_id)
        if intent is None:
            raise ValueError(f"Intent {intent_id!r} not found")
        from services.automated_trading.domain.enums import V2IntentState as IntentState

        current = IntentState(intent.state)
        if current != expected_current:
            raise ValueError(
                f"Intent {intent_id!r} expected state {expected_current.value}, "
                f"found {current.value} (version={intent.version})"
            )
        validate_intent_transition(current, next_state)
        intent.state = next_state.value
        intent.version = int(intent.version) + 1
        self.append_event(
            aggregate_id=intent_id,
            aggregate_type="INTENT",
            event_type=event_type,
            event_payload={
                **payload,
                "from_state": current.value,
                "to_state": next_state.value,
                "version": intent.version,
            },
            occurred_at=occurred_at or datetime.now(UTC),
        )
        self.session.flush()

    def reconcile_intent_from_confirmed_fill(self, intent_id: str) -> None:
        """Correct a terminal local guess only after an exact exchange fill is durable."""
        intent = self.session.get(V2ExecutionIntent, intent_id)
        if intent is None:
            raise ValueError(f"Intent {intent_id!r} not found")
        order = self.session.scalar(select(V2ExchangeOrder).where(V2ExchangeOrder.intent_id == intent_id))
        if order is None or not order.exchange_order_id:
            raise ValueError("confirmed exchange order identity is required")
        fill = self.session.scalar(
            select(V2ExchangeFill).where(
                V2ExchangeFill.intent_id == intent_id,
                V2ExchangeFill.exchange_order_id == order.exchange_order_id,
            )
        )
        if fill is None:
            raise ValueError("confirmed exchange fill receipt is required")
        current = intent.state
        if current == "FILLED":
            return
        if current not in {"EXCHANGE_ACKNOWLEDGED", "CANCELLED", "EXPIRED"}:
            raise ValueError(f"intent state {current!r} cannot be reconciled from a fill")
        intent.state = "FILLED"
        intent.version = int(intent.version) + 1
        self.append_event(
            aggregate_id=intent_id,
            aggregate_type="INTENT",
            event_type="ExchangeFillReconciled",
            event_payload={
                "from_state": current,
                "to_state": "FILLED",
                "exchange_order_id": order.exchange_order_id,
                "trade_id": fill.trade_id,
                "version": intent.version,
            },
            occurred_at=datetime.now(UTC),
        )
        self.session.flush()

    # ------------------------------------------------------------------
    # Orders / Fills
    # ------------------------------------------------------------------

    def save_order_submission(
        self,
        *,
        intent_id: str,
        client_order_id: str,
        quantity: float,
        leverage: int,
        submitted_at: datetime,
    ) -> str:
        order = V2ExchangeOrder(
            intent_id=intent_id,
            client_order_id=client_order_id,
            quantity=quantity,
            leverage=leverage,
            submitted_at=submitted_at,
        )
        self.session.add(order)
        self.session.flush()
        return order.order_record_id

    def save_exchange_order_receipt(
        self,
        *,
        order_record_id: str,
        exchange_order_id: str,
        acknowledged_at: datetime,
    ) -> None:
        order = self.session.get(V2ExchangeOrder, order_record_id)
        if order is None:
            raise ValueError(f"Order record {order_record_id!r} not found")
        if not exchange_order_id:
            raise ValueError("exchange_order_id is required")
        order.exchange_order_id = exchange_order_id
        order.acknowledged_at = acknowledged_at
        self.session.flush()

    def save_exchange_fill_receipt(
        self,
        *,
        intent_id: str,
        exchange_order_record_id: str,
        account_id: str,
        exchange_order_id: str,
        trade_id: str,
        symbol: str,
        side: str,
        reduce_only: bool,
        filled_quantity: Decimal,
        fill_price: Decimal,
        commission: Decimal | None,
        commission_asset: str | None,
        exchange_event_time: datetime,
        received_at: datetime,
        raw_hash: str,
    ) -> str:
        order = self.session.get(V2ExchangeOrder, exchange_order_record_id)
        if order is None:
            raise ValueError(f"Order record {exchange_order_record_id!r} not found")
        if order.intent_id != intent_id:
            raise ValueError(f"Fill intent_id={intent_id!r} does not match order intent_id={order.intent_id!r}")
        if order.exchange_order_id and order.exchange_order_id != exchange_order_id:
            raise ValueError(
                f"Fill exchange_order_id={exchange_order_id!r} does not match "
                f"saved order exchange_order_id={order.exchange_order_id!r}"
            )
        if not order.exchange_order_id:
            order.exchange_order_id = exchange_order_id

        fill = V2ExchangeFill(
            intent_id=intent_id,
            exchange_order_record_id=exchange_order_record_id,
            account_id=account_id,
            exchange_order_id=exchange_order_id,
            trade_id=trade_id,
            symbol=symbol,
            side=side,
            reduce_only=reduce_only,
            filled_quantity=filled_quantity,
            fill_price=fill_price,
            commission=commission,
            commission_asset=commission_asset,
            exchange_event_time=exchange_event_time,
            received_at=received_at,
            raw_hash=raw_hash,
        )
        self.session.add(fill)
        self.session.flush()
        self._refresh_order_aggregate_from_fills(exchange_order_record_id)
        self.session.flush()
        return fill.fill_id

    def save_fill_receipt(
        self,
        order_record_id: str,
        receipt: FillReceipt,
        *,
        account_id: str = "binance_testnet",
        symbol: str = "BTC/USDT",
        side: str = "BUY",
        reduce_only: bool = False,
    ) -> None:
        """Compatibility wrapper: persist per-trade fills then refresh order cache."""
        assert_fill_receipt_valid(receipt)
        order = self.session.get(V2ExchangeOrder, order_record_id)
        if order is None:
            raise ValueError(f"Order record {order_record_id!r} not found")
        if order.intent_id != receipt.intent_id:
            raise ValueError(
                f"FillReceipt intent_id={receipt.intent_id!r} does not match order intent_id={order.intent_id!r}"
            )

        order.exchange_order_id = receipt.exchange_order_id
        n = len(receipt.trade_ids)
        # Distribute quantity evenly across trade IDs for compatibility path;
        # production callers should use save_exchange_fill_receipt per trade.
        per_qty = (receipt.filled_quantity / n).quantize(Decimal("0.00000001"))
        allocated = Decimal("0")
        for idx, trade_id in enumerate(receipt.trade_ids):
            qty = receipt.filled_quantity - allocated if idx == n - 1 else per_qty
            allocated += qty
            self.save_exchange_fill_receipt(
                intent_id=receipt.intent_id,
                exchange_order_record_id=order_record_id,
                account_id=account_id,
                exchange_order_id=receipt.exchange_order_id,
                trade_id=trade_id,
                symbol=symbol,
                side=side,
                reduce_only=reduce_only,
                filled_quantity=qty,
                fill_price=receipt.average_fill_price,
                commission=(receipt.total_fee / n) if n else receipt.total_fee,
                commission_asset="USDT",
                exchange_event_time=receipt.fill_timestamp,
                received_at=receipt.fill_timestamp,
                raw_hash=f"{receipt.exchange_order_id}:{trade_id}",
            )

    def _aggregate_fills(self, order_record_id: str) -> dict[str, Any]:
        fills = list(
            self.session.scalars(
                select(V2ExchangeFill).where(V2ExchangeFill.exchange_order_record_id == order_record_id)
            )
        )
        if not fills:
            raise ValueError(
                f"No confirmed fills for order {order_record_id!r}; "
                "cannot project position without v2_exchange_fills rows"
            )
        total_qty = sum((f.filled_quantity for f in fills), Decimal("0"))
        notional = sum((f.filled_quantity * f.fill_price for f in fills), Decimal("0"))
        total_fee = sum((f.commission or Decimal("0") for f in fills), Decimal("0"))
        vwap = (notional / total_qty) if total_qty > 0 else Decimal("0")

        def _as_naive(dt: datetime) -> datetime:
            if dt.tzinfo is None:
                return dt
            return dt.astimezone(UTC).replace(tzinfo=None)

        return {
            "filled_quantity": total_qty,
            "average_fill_price": vwap,
            "total_fee": total_fee,
            "trade_ids": [f.trade_id for f in fills],
            "exchange_order_id": fills[0].exchange_order_id,
            "fill_timestamp": max(_as_naive(f.exchange_event_time) for f in fills),
        }

    def _refresh_order_aggregate_from_fills(self, order_record_id: str) -> None:
        order = self.session.get(V2ExchangeOrder, order_record_id)
        if order is None:
            return
        fills = list(
            self.session.scalars(
                select(V2ExchangeFill).where(V2ExchangeFill.exchange_order_record_id == order_record_id)
            )
        )
        if not fills:
            return
        agg = self._aggregate_fills(order_record_id)
        order.filled_quantity = agg["filled_quantity"]
        order.average_fill_price = agg["average_fill_price"]
        order.total_fee = agg["total_fee"]
        order.fill_timestamp = agg["fill_timestamp"]
        order.trade_ids = {"trade_ids": list(agg["trade_ids"])}
        if not order.exchange_order_id:
            order.exchange_order_id = agg["exchange_order_id"]

    # ------------------------------------------------------------------
    # Position projection / transitions
    # ------------------------------------------------------------------

    def project_position_from_confirmed_fills(
        self,
        *,
        position_id: str,
        intent_id: str,
        order_record_id: str,
        symbol: str,
        direction: str,
        execution_mode: V2ExecutionMode,
        projected_at: datetime,
    ) -> None:
        """Project position quantities exclusively from persisted fill rows."""
        from services.automated_trading.domain.enums import V2PositionState

        order = self.session.get(V2ExchangeOrder, order_record_id)
        if order is None:
            raise ValueError(f"Order record {order_record_id!r} not found")
        if order.intent_id != intent_id:
            raise ValueError(f"Cannot project: order intent_id={order.intent_id!r} != intent_id={intent_id!r}")
        agg = self._aggregate_fills(order_record_id)
        if not order.exchange_order_id:
            raise ValueError(
                f"Cannot project position: order {order_record_id!r} has no exchange_order_id; "
                "fill receipt must be saved first"
            )

        position = V2ManagedPosition(
            position_id=position_id,
            intent_id=intent_id,
            order_record_id=order_record_id,
            symbol=symbol,
            direction=direction,
            execution_mode=execution_mode.value,
            quantity=agg["filled_quantity"],
            entry_price=agg["average_fill_price"],
            entry_fee=agg["total_fee"],
            state=V2PositionState.POSITION_PROJECTED.value,
            version=0,
            projected_at=projected_at,
        )
        self.session.add(position)
        self.session.flush()

    def project_position(
        self,
        *,
        position_id: str,
        intent_id: str,
        order_record_id: str,
        symbol: str,
        direction: str,
        execution_mode: V2ExecutionMode,
        fill_receipt: FillReceipt,
        state: V2PositionState,
        projected_at: datetime,
    ) -> None:
        """Compatibility: project only from already-persisted fill rows.

        Callers must persist fills via save_exchange_fill_receipt / save_fill_receipt
        before projection. A FillReceipt argument cannot invent fill facts.
        """
        assert_fill_receipt_valid(fill_receipt)
        order = self.session.get(V2ExchangeOrder, order_record_id)
        if order is None:
            raise ValueError(f"Order record {order_record_id!r} not found")
        if not order.exchange_order_id:
            raise ValueError(
                f"Cannot project position: order {order_record_id!r} has no exchange_order_id; "
                "fill receipt must be saved first"
            )
        existing = list(
            self.session.scalars(
                select(V2ExchangeFill).where(V2ExchangeFill.exchange_order_record_id == order_record_id)
            )
        )
        if not existing:
            raise ValueError(
                f"No confirmed fills for order {order_record_id!r}; "
                "cannot project position without v2_exchange_fills rows"
            )
        self.project_position_from_confirmed_fills(
            position_id=position_id,
            intent_id=intent_id,
            order_record_id=order_record_id,
            symbol=symbol,
            direction=direction,
            execution_mode=execution_mode,
            projected_at=projected_at,
        )
        # Caller-requested non-default state must go through legal transitions.
        from services.automated_trading.domain.enums import V2PositionState as PosState

        if state is PosState.POSITION_PROJECTED:
            return
        if state is PosState.CLOSED:
            # Compatibility seed path used by fencing/cutover guards.
            self.transition_position(
                position_id=position_id,
                expected_current=PosState.POSITION_PROJECTED,
                next_state=PosState.REDUCING,
                event_type="PositionReducingForCloseSeed",
                payload={},
            )
            self.transition_position(
                position_id=position_id,
                expected_current=PosState.REDUCING,
                next_state=PosState.CLOSED,
                event_type="PositionClosedSeed",
                payload={},
            )
            return
        self.transition_position(
            position_id=position_id,
            expected_current=PosState.POSITION_PROJECTED,
            next_state=state,
            event_type="PositionProjectedThenTransitioned",
            payload={},
        )

    def transition_position(
        self,
        position_id: str,
        expected_current: V2PositionState,
        next_state: V2PositionState,
        event_type: str,
        payload: dict,
        occurred_at: datetime | None = None,
    ) -> None:
        position = self.session.get(V2ManagedPosition, position_id)
        if position is None:
            raise ValueError(f"Position {position_id!r} not found")
        from services.automated_trading.domain.enums import V2PositionState as PosState

        current = PosState(position.state)
        if current != expected_current:
            raise ValueError(
                f"Position {position_id!r} expected state {expected_current.value}, "
                f"found {current.value} (version={position.version})"
            )
        validate_position_transition(current, next_state)
        position.state = next_state.value
        position.version = int(position.version) + 1
        if next_state is PosState.CLOSED:
            position.closed_at = occurred_at or datetime.now(UTC)
        if next_state is PosState.PROTECTED:
            position.protected_at = occurred_at or datetime.now(UTC)
        self.append_event(
            aggregate_id=position_id,
            aggregate_type="POSITION",
            event_type=event_type,
            event_payload={
                **payload,
                "from_state": current.value,
                "to_state": next_state.value,
                "version": position.version,
            },
            occurred_at=occurred_at or datetime.now(UTC),
        )
        self.session.flush()

    def repair_quarantined_position_from_confirmed_exit(
        self,
        *,
        position_id: str,
        exit_order_record_id: str,
        occurred_at: datetime,
        payload: dict,
    ) -> None:
        """Correct a quarantined projection only from persisted reduce-only fills.

        QUARANTINED remains terminal for ordinary state transitions. This
        narrowly-scoped reconciliation correction requires an exact exit order
        and enough persisted reduce-only fill quantity to flatten the projected
        position.
        """
        from services.automated_trading.domain.enums import V2PositionState as PosState

        position = self.session.get(V2ManagedPosition, position_id)
        if position is None:
            raise ValueError(f"Position {position_id!r} not found")
        if PosState(position.state) is not PosState.QUARANTINED:
            raise ValueError(f"Position {position_id!r} is not QUARANTINED; use the normal state machine")
        order = self.session.get(V2ExchangeOrder, exit_order_record_id)
        if order is None or not order.exchange_order_id:
            raise ValueError("quarantine repair requires an acknowledged exit order")
        fills = tuple(
            self.session.scalars(
                select(V2ExchangeFill).where(V2ExchangeFill.exchange_order_record_id == exit_order_record_id)
            )
        )
        if not fills or any(not fill.reduce_only for fill in fills):
            raise ValueError("quarantine repair requires persisted reduce-only fills")
        if any(fill.symbol != position.symbol for fill in fills):
            raise ValueError("quarantine repair fill symbol does not match position")
        reduced_quantity = sum(
            (fill.filled_quantity for fill in fills),
            Decimal("0"),
        )
        if reduced_quantity < position.quantity:
            raise ValueError(
                f"quarantine repair fill quantity {reduced_quantity} is below projected quantity {position.quantity}"
            )

        current = PosState.QUARANTINED
        position.state = PosState.CLOSED.value
        position.closed_at = occurred_at
        position.version = int(position.version) + 1
        self.append_event(
            aggregate_id=position_id,
            aggregate_type="POSITION",
            event_type="QuarantinedProjectionCorrectedFromConfirmedExit",
            event_payload={
                **payload,
                "exit_order_record_id": exit_order_record_id,
                "exchange_order_id": order.exchange_order_id,
                "trade_ids": [fill.trade_id for fill in fills],
                "from_state": current.value,
                "to_state": PosState.CLOSED.value,
                "version": position.version,
            },
            occurred_at=occurred_at,
        )
        self.session.flush()

    # ------------------------------------------------------------------
    # Protection
    # ------------------------------------------------------------------

    def save_protection(
        self,
        *,
        protection_id: str,
        position_id: str,
        stop_loss_price: float,
        take_profit_price: float | None,
        stop_client_order_id: str,
        tp_client_order_id: str | None,
        state: V2ProtectionState,
        stop_exchange_order_id: str | None = None,
        tp_exchange_order_id: str | None = None,
        original_stop_loss_price: float | None = None,
        policy: str = "P1",
    ) -> None:
        """Create a protection intent record.

        ACTIVE may only be reached via transition_protection / update_protection_active
        after exchange conditional order IDs are attached.
        """
        from services.automated_trading.domain.enums import V2ProtectionState as ProtState

        if state is ProtState.PROTECTION_ACTIVE:
            raise ValueError(
                "save_protection cannot create PROTECTION_ACTIVE; "
                "persist PROTECTION_INTENT then transition after exchange order ids"
            )
        if state is not ProtState.PROTECTION_INTENT:
            raise ValueError(
                f"save_protection only accepts PROTECTION_INTENT, got {state.value}; "
                "use transition_protection for later states"
            )
        protection = V2ProtectionRecord(
            protection_id=protection_id,
            position_id=position_id,
            stop_loss_price=stop_loss_price,
            original_stop_loss_price=original_stop_loss_price
            if original_stop_loss_price is not None
            else stop_loss_price,
            take_profit_price=take_profit_price,
            stop_client_order_id=stop_client_order_id,
            tp_client_order_id=tp_client_order_id,
            stop_exchange_order_id=stop_exchange_order_id,
            tp_exchange_order_id=tp_exchange_order_id,
            state=state.value,
            version=0,
            policy=policy,
        )
        self.session.add(protection)
        self.session.flush()

    def update_protection_stop(
        self,
        *,
        protection_id: str,
        stop_loss_price: Decimal,
        stop_client_order_id: str,
        stop_exchange_order_id: str,
        occurred_at: datetime | None = None,
    ) -> None:
        """Persist a confirmed one-way stop tightening."""
        protection = self.session.get(V2ProtectionRecord, protection_id)
        if protection is None:
            raise ValueError(f"Protection {protection_id!r} not found")
        protection.stop_loss_price = stop_loss_price
        protection.stop_client_order_id = stop_client_order_id
        protection.stop_exchange_order_id = stop_exchange_order_id
        protection.version = int(protection.version) + 1
        self.append_event(
            aggregate_id=protection_id,
            aggregate_type="PROTECTION",
            event_type="ProfitProtectionStopTightened",
            event_payload={
                "stop_loss_price": str(stop_loss_price),
                "stop_exchange_order_id": stop_exchange_order_id,
                "version": protection.version,
            },
            occurred_at=occurred_at or datetime.now(UTC),
        )
        self.session.flush()

    def transition_protection(
        self,
        protection_id: str,
        expected_current: V2ProtectionState,
        next_state: V2ProtectionState,
        event_type: str,
        payload: dict,
        occurred_at: datetime | None = None,
    ) -> None:
        from services.automated_trading.domain.enums import V2ProtectionState as ProtState

        protection = self.session.get(V2ProtectionRecord, protection_id)
        if protection is None:
            raise ValueError(f"Protection {protection_id!r} not found")
        current = ProtState(protection.state)
        if current != expected_current:
            raise ValueError(
                f"Protection {protection_id!r} expected state {expected_current.value}, "
                f"found {current.value} (version={protection.version})"
            )
        validate_protection_transition(current, next_state)
        if next_state is ProtState.PROTECTION_ACTIVE:
            assert_protection_active_requires_exchange_order_id(protection.stop_exchange_order_id)
        protection.state = next_state.value
        protection.version = int(protection.version) + 1
        if next_state is ProtState.PROTECTION_ACTIVE:
            protection.activated_at = occurred_at or datetime.now(UTC)
        self.append_event(
            aggregate_id=protection_id,
            aggregate_type="PROTECTION",
            event_type=event_type,
            event_payload={
                **payload,
                "from_state": current.value,
                "to_state": next_state.value,
                "version": protection.version,
            },
            occurred_at=occurred_at or datetime.now(UTC),
        )
        self.session.flush()

    def update_protection_active(
        self,
        protection_id: str,
        receipt: ProtectionReceipt,
        new_state: V2ProtectionState,
        activated_at: datetime,
    ) -> None:
        """Attach exchange IDs then force a validated transition to ACTIVE."""
        from services.automated_trading.domain.enums import V2ProtectionState as ProtState

        protection = self.session.get(V2ProtectionRecord, protection_id)
        if protection is None:
            raise ValueError(f"Protection {protection_id!r} not found")
        assert_protection_active_requires_exchange_order_id(receipt.stop_exchange_order_id)
        protection.stop_exchange_order_id = receipt.stop_exchange_order_id
        protection.tp_exchange_order_id = receipt.tp_exchange_order_id
        self.session.flush()
        current = ProtState(protection.state)
        if current is ProtState.PROTECTION_INTENT:
            self.transition_protection(
                protection_id=protection_id,
                expected_current=ProtState.PROTECTION_INTENT,
                next_state=ProtState.PROTECTION_SUBMITTING,
                event_type="ProtectionSubmitting",
                payload={},
                occurred_at=activated_at,
            )
            current = ProtState.PROTECTION_SUBMITTING
        self.transition_protection(
            protection_id=protection_id,
            expected_current=current,
            next_state=new_state,
            event_type="ProtectionActivated",
            payload={
                "stop_exchange_order_id": receipt.stop_exchange_order_id,
                "tp_exchange_order_id": receipt.tp_exchange_order_id,
            },
            occurred_at=activated_at,
        )

    # ------------------------------------------------------------------
    # Events / recon / incidents / runtime control
    # ------------------------------------------------------------------

    def append_event(
        self,
        *,
        aggregate_id: str,
        aggregate_type: str,
        event_type: str,
        event_payload: dict,
        occurred_at: datetime,
    ) -> str:
        event = V2ExecutionEvent(
            aggregate_id=aggregate_id,
            aggregate_type=aggregate_type,
            event_type=event_type,
            event_payload=event_payload,
            occurred_at=occurred_at,
        )
        self.session.add(event)
        self.session.flush()
        return event.event_id

    def record_reconciliation(
        self,
        *,
        execution_mode: V2ExecutionMode,
        exchange_positions: dict,
        exchange_open_orders: dict,
        local_positions: dict,
        discrepancies: dict,
        status: str,
        captured_at: datetime,
        cycle_id: str | None = None,
    ) -> str:
        snapshot = V2ReconciliationSnapshot(
            cycle_id=cycle_id,
            execution_mode=execution_mode.value,
            exchange_positions=exchange_positions,
            exchange_open_orders=exchange_open_orders,
            local_positions=local_positions,
            discrepancies=discrepancies,
            status=status,
            captured_at=captured_at,
        )
        self.session.add(snapshot)
        self.session.flush()
        return snapshot.snapshot_id

    def record_incident(
        self,
        *,
        incident_type: str,
        severity: str,
        related_aggregate_id: str | None,
        description: str,
        context: dict,
        intent_id: str | None = None,
        position_id: str | None = None,
    ) -> str:
        incident = V2ExecutionIncident(
            incident_type=incident_type,
            severity=severity,
            related_aggregate_id=related_aggregate_id,
            intent_id=intent_id,
            position_id=position_id,
            description=description,
            context=context,
        )
        self.session.add(incident)
        self.session.flush()
        return incident.incident_id

    def resolve_reconciliation_incidents(self, *, resolved_at: datetime) -> int:
        """Resolve mismatch incidents only after a HEALTHY exchange reconciliation."""
        incidents = tuple(
            self.session.scalars(
                select(V2ExecutionIncident).where(
                    V2ExecutionIncident.resolved.is_(False),
                    V2ExecutionIncident.incident_type.in_(
                        [
                            "LOCAL_GHOST_QUARANTINED",
                            "EXTERNAL_POSITION_QUARANTINED",
                        ]
                    ),
                )
            )
        )
        for incident in incidents:
            incident.resolved = True
            incident.resolved_at = resolved_at
        self.session.flush()
        return len(incidents)

    def get_runtime_control(self, scope: str = "global") -> V2RuntimeControl:
        control = self.session.get(V2RuntimeControl, scope)
        if control is None:
            control = V2RuntimeControl(
                scope=scope,
                entry_enabled=False,
                reason="default_disabled_until_gate5",
                updated_by="system",
                version=0,
            )
            self.session.add(control)
            self.session.flush()
        return control

    def set_runtime_control(
        self,
        *,
        scope: str,
        entry_enabled: bool,
        reason: str | None,
        updated_by: str | None,
        expected_version: int | None = None,
    ) -> V2RuntimeControl:
        control = self.get_runtime_control(scope)
        if expected_version is not None and int(control.version) != expected_version:
            raise ValueError(
                f"Runtime control {scope!r} version conflict: expected {expected_version}, found {control.version}"
            )
        control.entry_enabled = entry_enabled
        control.reason = reason
        control.updated_by = updated_by
        control.version = int(control.version) + 1
        control.updated_at = datetime.now(UTC)
        self.session.flush()
        return control

    def get_open_positions(self, execution_mode: V2ExecutionMode, symbol: str | None = None) -> list[V2ManagedPosition]:
        stmt = select(V2ManagedPosition).where(
            V2ManagedPosition.execution_mode == execution_mode.value,
            V2ManagedPosition.state.not_in(["CLOSED", "QUARANTINED"]),
        )
        if symbol:
            stmt = stmt.where(V2ManagedPosition.symbol == symbol)
        return list(self.session.scalars(stmt))

    def list_managed_positions(
        self,
        *,
        execution_mode: V2ExecutionMode,
        limit: int = 200,
        include_terminal: bool = True,
    ) -> list[V2ManagedPosition]:
        """List V2 position facts for runtime observability.

        Runtime Truth must read the same projection used by the V2 cycle, rather
        than the legacy compatibility position table.
        """
        stmt = (
            select(V2ManagedPosition)
            .where(V2ManagedPosition.execution_mode == execution_mode.value)
            .order_by(V2ManagedPosition.projected_at.desc())
            .limit(limit)
        )
        if not include_terminal:
            stmt = stmt.where(V2ManagedPosition.state.not_in(["CLOSED", "QUARANTINED"]))
        return list(self.session.scalars(stmt))

    def list_exchange_orders(
        self,
        *,
        execution_mode: V2ExecutionMode,
        symbol: str | None = None,
        limit: int = 200,
    ) -> list[tuple[V2ExchangeOrder, V2ExecutionIntent]]:
        """List V2 order facts with their intent attribution for Runtime Truth."""
        stmt = (
            select(V2ExchangeOrder, V2ExecutionIntent)
            .join(V2ExecutionIntent, V2ExchangeOrder.intent_id == V2ExecutionIntent.intent_id)
            .where(V2ExecutionIntent.execution_mode == execution_mode.value)
            .order_by(V2ExchangeOrder.created_at.desc())
            .limit(limit)
        )
        if symbol is not None:
            stmt = stmt.where(V2ExecutionIntent.symbol == symbol)
        return [(order, intent) for order, intent in self.session.execute(stmt)]

    def list_protection_records(
        self,
        *,
        execution_mode: V2ExecutionMode,
        limit: int = 200,
    ) -> list[tuple[V2ProtectionRecord, V2ManagedPosition]]:
        """List V2 protection facts with their managed-position attribution."""
        stmt = (
            select(V2ProtectionRecord, V2ManagedPosition)
            .join(V2ManagedPosition, V2ProtectionRecord.position_id == V2ManagedPosition.position_id)
            .where(V2ManagedPosition.execution_mode == execution_mode.value)
            .order_by(V2ProtectionRecord.created_at.desc())
            .limit(limit)
        )
        return [(protection, position) for protection, position in self.session.execute(stmt)]

    def list_exchange_fills(
        self,
        *,
        execution_mode: V2ExecutionMode,
        limit: int = 200,
    ) -> list[tuple[V2ExchangeFill, V2ExecutionIntent]]:
        """List V2 fill receipts with their execution intent attribution."""
        stmt = (
            select(V2ExchangeFill, V2ExecutionIntent)
            .join(V2ExecutionIntent, V2ExchangeFill.intent_id == V2ExecutionIntent.intent_id)
            .where(V2ExecutionIntent.execution_mode == execution_mode.value)
            .order_by(V2ExchangeFill.received_at.desc())
            .limit(limit)
        )
        return [(fill, intent) for fill, intent in self.session.execute(stmt)]

    def list_exchange_unknown_intents(self, *, execution_mode: V2ExecutionMode) -> list[V2ExecutionIntent]:
        stmt = select(V2ExecutionIntent).where(
            V2ExecutionIntent.execution_mode == execution_mode.value,
            V2ExecutionIntent.state == "EXCHANGE_UNKNOWN",
        )
        return list(self.session.scalars(stmt))

    def get_position_by_id(self, position_id: str) -> V2ManagedPosition | None:
        return self.session.get(V2ManagedPosition, position_id)

    def get_latest_cycle(self) -> V2ExecutionCycle | None:
        stmt = select(V2ExecutionCycle).order_by(V2ExecutionCycle.started_at.desc()).limit(1)
        return self.session.scalar(stmt)

    def list_recent_cycles(self, limit: int = 20) -> list[V2ExecutionCycle]:
        stmt = select(V2ExecutionCycle).order_by(V2ExecutionCycle.started_at.desc()).limit(limit)
        return list(self.session.scalars(stmt))

    def list_recent_decisions(
        self,
        *,
        symbol: str | None = None,
        since: datetime | None = None,
        limit: int = 50,
    ) -> list[tuple[V2ExecutionDecision, V2ExecutionCycle]]:
        stmt = select(V2ExecutionDecision, V2ExecutionCycle).join(
            V2ExecutionCycle, V2ExecutionDecision.cycle_id == V2ExecutionCycle.cycle_id
        )
        if symbol:
            stmt = stmt.where(V2ExecutionCycle.symbol == symbol)
        if since:
            stmt = stmt.where(V2ExecutionDecision.created_at >= since)
        stmt = stmt.order_by(V2ExecutionDecision.created_at.desc()).limit(limit)
        return [(decision, cycle) for decision, cycle in self.session.execute(stmt)]

    def list_recent_incidents(self, limit: int = 20) -> list[V2ExecutionIncident]:
        stmt = select(V2ExecutionIncident).order_by(V2ExecutionIncident.created_at.desc()).limit(limit)
        return list(self.session.scalars(stmt))

    def get_latest_reconciliation_snapshot(self) -> V2ReconciliationSnapshot | None:
        stmt = select(V2ReconciliationSnapshot).order_by(V2ReconciliationSnapshot.captured_at.desc()).limit(1)
        return self.session.scalar(stmt)

    def commit(self) -> None:
        self.session.commit()

    def rollback(self) -> None:
        self.session.rollback()
