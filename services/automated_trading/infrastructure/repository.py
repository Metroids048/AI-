"""V2 execution repository: append-only event persistence.

Exchange-First persistence rules:
- Cannot create V2ManagedPosition without a fill receipt (exchange_order_id + trade_ids)
- All state mutations append events to v2_execution_events
- Client order IDs and exchange order IDs must be unique
- Trade IDs are stored as immutable evidence
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import Session

from services.automated_trading.domain.invariants import assert_fill_receipt_valid
from services.automated_trading.domain.receipts import (
    FillReceipt,
    ProtectionReceipt,
)
from services.automated_trading.infrastructure.models import (
    V2ExchangeOrder,
    V2ExecutionCycle,
    V2ExecutionEvent,
    V2ExecutionIncident,
    V2ManagedPosition,
    V2ProtectionRecord,
    V2ReconciliationSnapshot,
)

if TYPE_CHECKING:
    from services.automated_trading.domain.enums import (
        V2CandidateType,
        V2ExecutionMode,
        V2IntentState,
        V2PositionState,
        V2ProtectionState,
    )


class AutomatedTradingRepository:
    """Repository for V2 automated trading persistence.

    All writes are append-only or state transitions validated by domain layer.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

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
        """Create a new execution cycle record."""
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
        """Mark cycle as completed with decision terminal reason."""
        cycle = self.session.get(V2ExecutionCycle, cycle_id)
        if cycle is None:
            raise ValueError(f"Cycle {cycle_id!r} not found")
        cycle.decision_terminal = decision_terminal
        cycle.completed_at = completed_at
        self.session.flush()

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
    ) -> None:
        """Create entry intent record."""
        from services.automated_trading.infrastructure.models import V2ExecutionIntent

        intent = V2ExecutionIntent(
            intent_id=intent_id,
            cycle_id=cycle_id,
            symbol=symbol,
            direction=direction,
            candidate_key=candidate_key,
            candidate_type=candidate_type.value,
            execution_mode=execution_mode.value,
            decision_bar_timestamp=decision_bar_timestamp,
            decision_funnel_id=decision_funnel_id,
            state=state.value,
        )
        self.session.add(intent)
        self.session.flush()

    def update_intent_state(self, intent_id: str, new_state: V2IntentState) -> None:
        """Update intent state. State transition must be validated before calling."""
        from services.automated_trading.infrastructure.models import V2ExecutionIntent

        stmt = select(V2ExecutionIntent).where(V2ExecutionIntent.intent_id == intent_id)
        intent = self.session.scalar(stmt)
        if intent is None:
            raise ValueError(f"Intent {intent_id!r} not found")
        intent.state = new_state.value
        self.session.flush()

    def save_order_submission(
        self,
        *,
        intent_id: str,
        client_order_id: str,
        quantity: float,
        leverage: int,
        submitted_at: datetime,
    ) -> str:
        """Save exchange order submission record. Returns order_record_id."""
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

    def save_fill_receipt(
        self,
        order_record_id: str,
        receipt: FillReceipt,
    ) -> None:
        """Save exchange fill receipt.

        Exchange-First: This persists exchange_order_id + trade_ids as proof.
        """
        assert_fill_receipt_valid(receipt)

        order = self.session.get(V2ExchangeOrder, order_record_id)
        if order is None:
            raise ValueError(f"Order record {order_record_id!r} not found")

        order.exchange_order_id = receipt.exchange_order_id
        order.filled_quantity = receipt.filled_quantity
        order.average_fill_price = receipt.average_fill_price
        order.total_fee = receipt.total_fee
        order.fill_timestamp = receipt.fill_timestamp
        order.trade_ids = {"trade_ids": list(receipt.trade_ids)}
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
        """Project managed position from exchange fill.

        Exchange-First Invariant: Cannot create position without fill receipt.
        """
        assert_fill_receipt_valid(fill_receipt)

        # Verify order record has the fill receipt
        order = self.session.get(V2ExchangeOrder, order_record_id)
        if order is None:
            raise ValueError(f"Order record {order_record_id!r} not found")
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
            quantity=fill_receipt.filled_quantity,
            entry_price=fill_receipt.average_fill_price,
            entry_fee=fill_receipt.total_fee,
            state=state.value,
            projected_at=projected_at,
        )
        self.session.add(position)
        self.session.flush()

    def update_position_state(self, position_id: str, new_state: V2PositionState) -> None:
        """Update position state. State transition must be validated before calling."""
        position = self.session.get(V2ManagedPosition, position_id)
        if position is None:
            raise ValueError(f"Position {position_id!r} not found")
        position.state = new_state.value
        self.session.flush()

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
    ) -> None:
        """Save protection order intent."""
        protection = V2ProtectionRecord(
            protection_id=protection_id,
            position_id=position_id,
            stop_loss_price=stop_loss_price,
            take_profit_price=take_profit_price,
            stop_client_order_id=stop_client_order_id,
            tp_client_order_id=tp_client_order_id,
            state=state.value,
        )
        self.session.add(protection)
        self.session.flush()

    def update_protection_active(
        self,
        protection_id: str,
        receipt: ProtectionReceipt,
        new_state: V2ProtectionState,
        activated_at: datetime,
    ) -> None:
        """Mark protection as active with exchange conditional order IDs."""
        protection = self.session.get(V2ProtectionRecord, protection_id)
        if protection is None:
            raise ValueError(f"Protection {protection_id!r} not found")

        protection.stop_exchange_order_id = receipt.stop_exchange_order_id
        protection.tp_exchange_order_id = receipt.tp_exchange_order_id
        protection.state = new_state.value
        protection.activated_at = activated_at
        self.session.flush()

    def append_event(
        self,
        *,
        aggregate_id: str,
        aggregate_type: str,
        event_type: str,
        event_payload: dict,
        occurred_at: datetime,
    ) -> str:
        """Append event to immutable event log. Returns event_id."""
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
    ) -> str:
        """Record reconciliation snapshot. Returns snapshot_id."""
        snapshot = V2ReconciliationSnapshot(
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
    ) -> str:
        """Record execution incident. Returns incident_id."""
        incident = V2ExecutionIncident(
            incident_type=incident_type,
            severity=severity,
            related_aggregate_id=related_aggregate_id,
            description=description,
            context=context,
        )
        self.session.add(incident)
        self.session.flush()
        return incident.incident_id

    def get_open_positions(self, execution_mode: V2ExecutionMode, symbol: str | None = None) -> list[V2ManagedPosition]:
        """Get open positions (not CLOSED or QUARANTINED)."""
        stmt = select(V2ManagedPosition).where(
            V2ManagedPosition.execution_mode == execution_mode.value,
            V2ManagedPosition.state.not_in(["CLOSED", "QUARANTINED"]),
        )
        if symbol:
            stmt = stmt.where(V2ManagedPosition.symbol == symbol)
        return list(self.session.scalars(stmt))

    def get_position_by_id(self, position_id: str) -> V2ManagedPosition | None:
        """Get position by position_id."""
        return self.session.get(V2ManagedPosition, position_id)

    def commit(self) -> None:
        """Commit the transaction."""
        self.session.commit()

    def rollback(self) -> None:
        """Rollback the transaction."""
        self.session.rollback()
