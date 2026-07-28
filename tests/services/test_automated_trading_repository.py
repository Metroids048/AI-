"""Tests for V2 automated trading repository.

Validates Exchange-First persistence invariants:
- Cannot project managed position without fill receipt
- Client order IDs and exchange order IDs are unique
- Trade IDs are immutable evidence
- State transitions require domain validation before persistence
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from services.automated_trading.domain.enums import (
    V2CandidateType,
    V2ExecutionMode,
    V2IntentState,
    V2PositionState,
    V2ProtectionState,
)
from services.automated_trading.domain.receipts import FillReceipt, ProtectionReceipt
from services.automated_trading.infrastructure.models import Base
from services.automated_trading.infrastructure.repository import AutomatedTradingRepository


@pytest.fixture
def in_memory_db() -> Session:
    """Create an in-memory SQLite database for testing."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    yield session
    session.close()


@pytest.fixture
def repo(in_memory_db: Session) -> AutomatedTradingRepository:
    """Create repository instance."""
    return AutomatedTradingRepository(in_memory_db)


class TestCycleManagement:
    def test_create_cycle(self, repo: AutomatedTradingRepository) -> None:
        cycle = repo.create_cycle(
            cycle_id="cycle-001",
            symbol="BTC/USDT",
            timeframe="15m",
            bar_timestamp=datetime(2026, 7, 28, 10, 0, tzinfo=UTC),
            execution_mode=V2ExecutionMode.BINANCE_TESTNET,
            fencing_token="fence-001",
        )
        assert cycle.cycle_id == "cycle-001"
        assert cycle.decision_terminal is None

    def test_complete_cycle(self, repo: AutomatedTradingRepository) -> None:
        repo.create_cycle(
            cycle_id="cycle-002",
            symbol="BTC/USDT",
            timeframe="15m",
            bar_timestamp=datetime(2026, 7, 28, 10, 15, tzinfo=UTC),
            execution_mode=V2ExecutionMode.BINANCE_TESTNET,
            fencing_token="fence-002",
        )
        repo.complete_cycle(
            cycle_id="cycle-002",
            decision_terminal="ENTRY_CREATED",
            completed_at=datetime(2026, 7, 28, 10, 16, tzinfo=UTC),
        )
        repo.commit()


class TestIntentManagement:
    def test_create_intent(self, repo: AutomatedTradingRepository) -> None:
        repo.create_intent(
            intent_id="intent-001",
            cycle_id="cycle-003",
            symbol="BTC/USDT",
            direction="long",
            candidate_key="primary_mature",
            candidate_type=V2CandidateType.PRIMARY,
            execution_mode=V2ExecutionMode.BINANCE_TESTNET,
            decision_bar_timestamp=datetime(2026, 7, 28, 10, 0, tzinfo=UTC),
            decision_funnel_id=None,
            state=V2IntentState.INTENT_CREATED,
        )
        repo.commit()

    def test_update_intent_state(self, repo: AutomatedTradingRepository) -> None:
        repo.create_intent(
            intent_id="intent-002",
            cycle_id="cycle-004",
            symbol="BTC/USDT",
            direction="long",
            candidate_key="primary_mature",
            candidate_type=V2CandidateType.PRIMARY,
            execution_mode=V2ExecutionMode.BINANCE_TESTNET,
            decision_bar_timestamp=datetime(2026, 7, 28, 10, 0, tzinfo=UTC),
            decision_funnel_id=None,
            state=V2IntentState.INTENT_CREATED,
        )
        repo.update_intent_state("intent-002", V2IntentState.EXCHANGE_SUBMITTING)
        repo.commit()


class TestOrderAndFillReceipt:
    def test_save_order_submission(self, repo: AutomatedTradingRepository) -> None:
        order_id = repo.save_order_submission(
            intent_id="intent-003",
            client_order_id="client-order-001",
            quantity=0.001,
            leverage=20,
            submitted_at=datetime(2026, 7, 28, 10, 0, tzinfo=UTC),
        )
        assert order_id is not None
        repo.commit()

    def test_save_fill_receipt(self, repo: AutomatedTradingRepository) -> None:
        order_id = repo.save_order_submission(
            intent_id="intent-004",
            client_order_id="client-order-002",
            quantity=0.001,
            leverage=20,
            submitted_at=datetime(2026, 7, 28, 10, 0, tzinfo=UTC),
        )
        receipt = FillReceipt(
            intent_id="intent-004",
            exchange_order_id="binance-order-123",
            trade_ids=("trade-001", "trade-002"),
            filled_quantity=Decimal("0.001"),
            average_fill_price=Decimal("65000.0"),
            total_fee=Decimal("0.65"),
            fill_timestamp=datetime(2026, 7, 28, 10, 1, tzinfo=UTC),
        )
        repo.save_fill_receipt(order_id, receipt)
        repo.commit()

    def test_client_order_id_unique_constraint(self, repo: AutomatedTradingRepository, in_memory_db: Session) -> None:
        """Client order IDs must be unique."""
        repo.save_order_submission(
            intent_id="intent-005",
            client_order_id="duplicate-client-order",
            quantity=0.001,
            leverage=20,
            submitted_at=datetime(2026, 7, 28, 10, 0, tzinfo=UTC),
        )
        repo.commit()

        # Attempt duplicate client order ID — IntegrityError raised at flush
        with pytest.raises(IntegrityError):
            repo.save_order_submission(
                intent_id="intent-006",
                client_order_id="duplicate-client-order",
                quantity=0.002,
                leverage=20,
                submitted_at=datetime(2026, 7, 28, 10, 1, tzinfo=UTC),
            )
            repo.commit()
        in_memory_db.rollback()


class TestManagedPositionInvariants:
    def test_project_position_with_fill_receipt(self, repo: AutomatedTradingRepository) -> None:
        """Exchange-First: Can project position with valid fill receipt."""
        order_id = repo.save_order_submission(
            intent_id="intent-007",
            client_order_id="client-order-003",
            quantity=0.001,
            leverage=20,
            submitted_at=datetime(2026, 7, 28, 10, 0, tzinfo=UTC),
        )
        receipt = FillReceipt(
            intent_id="intent-007",
            exchange_order_id="binance-order-456",
            trade_ids=("trade-003",),
            filled_quantity=Decimal("0.001"),
            average_fill_price=Decimal("65000.0"),
            total_fee=Decimal("0.65"),
            fill_timestamp=datetime(2026, 7, 28, 10, 1, tzinfo=UTC),
        )
        repo.save_fill_receipt(order_id, receipt)

        repo.project_position(
            position_id="pos-001",
            intent_id="intent-007",
            order_record_id=order_id,
            symbol="BTC/USDT",
            direction="long",
            execution_mode=V2ExecutionMode.BINANCE_TESTNET,
            fill_receipt=receipt,
            state=V2PositionState.POSITION_PROJECTED,
            projected_at=datetime(2026, 7, 28, 10, 2, tzinfo=UTC),
        )
        repo.commit()

    def test_cannot_project_position_without_fill_receipt(self, repo: AutomatedTradingRepository) -> None:
        """Exchange-First Invariant: Cannot project position without fill receipt."""
        order_id = repo.save_order_submission(
            intent_id="intent-008",
            client_order_id="client-order-004",
            quantity=0.001,
            leverage=20,
            submitted_at=datetime(2026, 7, 28, 10, 0, tzinfo=UTC),
        )
        # Do NOT save fill receipt

        receipt = FillReceipt(
            intent_id="intent-008",
            exchange_order_id="binance-order-789",
            trade_ids=("trade-004",),
            filled_quantity=Decimal("0.001"),
            average_fill_price=Decimal("65000.0"),
            total_fee=Decimal("0.65"),
            fill_timestamp=datetime(2026, 7, 28, 10, 1, tzinfo=UTC),
        )

        with pytest.raises(ValueError, match="has no exchange_order_id"):
            repo.project_position(
                position_id="pos-002",
                intent_id="intent-008",
                order_record_id=order_id,
                symbol="BTC/USDT",
                direction="long",
                execution_mode=V2ExecutionMode.BINANCE_TESTNET,
                fill_receipt=receipt,
                state=V2PositionState.POSITION_PROJECTED,
                projected_at=datetime(2026, 7, 28, 10, 2, tzinfo=UTC),
            )


class TestProtectionManagement:
    def test_save_protection_and_activate(self, repo: AutomatedTradingRepository) -> None:
        repo.save_protection(
            protection_id="prot-001",
            position_id="pos-003",
            stop_loss_price=64000.0,
            take_profit_price=67000.0,
            stop_client_order_id="stop-client-001",
            tp_client_order_id="tp-client-001",
            state=V2ProtectionState.PROTECTION_INTENT,
        )
        receipt = ProtectionReceipt(
            position_id="pos-003",
            stop_exchange_order_id="conditional-stop-123",
            tp_exchange_order_id="conditional-tp-456",
            submission_timestamp=datetime(2026, 7, 28, 10, 5, tzinfo=UTC),
        )
        repo.update_protection_active(
            protection_id="prot-001",
            receipt=receipt,
            new_state=V2ProtectionState.PROTECTION_ACTIVE,
            activated_at=datetime(2026, 7, 28, 10, 5, tzinfo=UTC),
        )
        repo.commit()


class TestEventLog:
    def test_append_event(self, repo: AutomatedTradingRepository) -> None:
        event_id = repo.append_event(
            aggregate_id="intent-009",
            aggregate_type="INTENT",
            event_type="IntentCreatedEvent",
            event_payload={"symbol": "BTC/USDT", "direction": "long"},
            occurred_at=datetime(2026, 7, 28, 10, 0, tzinfo=UTC),
        )
        assert event_id is not None
        repo.commit()


class TestReconciliationAndIncidents:
    def test_record_reconciliation(self, repo: AutomatedTradingRepository) -> None:
        snapshot_id = repo.record_reconciliation(
            execution_mode=V2ExecutionMode.BINANCE_TESTNET,
            exchange_positions=[],
            exchange_open_orders=[],
            local_positions=[],
            discrepancies={},
            status="HEALTHY",
            captured_at=datetime(2026, 7, 28, 10, 10, tzinfo=UTC),
        )
        assert snapshot_id is not None
        repo.commit()

    def test_record_incident(self, repo: AutomatedTradingRepository) -> None:
        incident_id = repo.record_incident(
            incident_type="PROTECTION_SUBMISSION_FAILED",
            severity="HIGH",
            related_aggregate_id="pos-004",
            description="Stop-loss submission failed after fill",
            context={"error": "timeout"},
        )
        assert incident_id is not None
        repo.commit()
