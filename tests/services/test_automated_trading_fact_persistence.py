"""Gate 2 fill-path: persist_entry_and_protection writes confirmed exchange facts."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker

from services.automated_trading.application.entry_service import (
    EntryExecutionResult,
    EntryExecutionStatus,
)
from services.automated_trading.application.exit_service import (
    ExitExecutionResult,
    ExitExecutionStatus,
    ExitReason,
)
from services.automated_trading.application.fact_persistence import (
    persist_entry_and_protection,
    persist_entry_intent_before_submission,
    persist_entry_submission_result,
    persist_exit_intent_before_submission,
    persist_exit_result,
    persist_exit_submission_result,
    reconcile_closed_position_protections,
)
from services.automated_trading.domain.enums import (
    V2CandidateType,
    V2ExecutionMode,
    V2IntentState,
    V2PositionState,
    V2ProtectionState,
)
from services.automated_trading.infrastructure.models import (
    Base,
    V2ExchangeFill,
    V2ExchangeOrder,
    V2ExecutionIntent,
    V2ManagedPosition,
    V2ProtectionRecord,
)
from services.automated_trading.infrastructure.repository import AutomatedTradingRepository
from services.automated_trading.observability.decision_funnel import DecisionReasonCode


@pytest.fixture
def fact_db(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'facts.db'}")

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_conn, _connection_record):  # type: ignore[no-untyped-def]
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(
        "services.automated_trading.application.fact_persistence.get_session_factory",
        lambda: factory,
    )
    yield factory
    engine.dispose()


def test_entry_intent_persistence_rechecks_runtime_pause(fact_db) -> None:
    session: Session = fact_db()
    try:
        repo = AutomatedTradingRepository(session)
        repo.set_runtime_control(
            scope="global",
            entry_enabled=False,
            reason="containment",
            updated_by="test",
        )
        session.commit()
    finally:
        session.close()

    reason = persist_entry_intent_before_submission(
        cycle_id="cycle-paused",
        decision_id="decision-paused",
        intent_id="intent-paused",
        symbol="ETH/USDT",
        direction="long",
        candidate_key="testnet_sampling_v2",
        candidate_type=V2CandidateType.SAMPLING,
        execution_mode=V2ExecutionMode.BINANCE_TESTNET,
        decision_bar_timestamp=datetime(2026, 8, 17, 15, 30, tzinfo=UTC),
        fencing_token="fence-paused",
        initial_risk_usdt=Decimal("0.60"),
    )

    assert reason == "ENTRY_PAUSED"
    session = fact_db()
    try:
        assert session.get(V2ExecutionIntent, "intent-paused") is None
    finally:
        session.close()


def test_persist_entry_and_protection_writes_full_chain(fact_db) -> None:
    entry = EntryExecutionResult(
        status=EntryExecutionStatus.FILLED,
        intent_state=V2IntentState.FILLED,
        client_order_id="A2E-fact-1",
        exchange_order_id="xo-entry-1",
        trade_ids=("trade-a", "trade-b"),
        filled_quantity=Decimal("0.002"),
        average_fill_price=Decimal("65000"),
        total_fee=Decimal("0.13"),
        fill_timestamp=datetime(2026, 7, 29, 12, 0, tzinfo=UTC),
    )
    protection = SimpleNamespace(
        is_active=True,
        stop_exchange_order_id="xo-stop-1",
        tp_exchange_order_id="xo-tp-1",
    )

    persist_entry_and_protection(
        cycle_id="cycle-fact-1",
        decision_id="decision-fact-1",
        intent_id="intent-fact-1",
        symbol="BTC/USDT",
        direction="long",
        candidate_key="testnet_sampling_v2",
        candidate_type=V2CandidateType.SAMPLING,
        execution_mode=V2ExecutionMode.BINANCE_TESTNET,
        decision_bar_timestamp=datetime(2026, 7, 29, 11, 45, tzinfo=UTC),
        fencing_token="fence-fact-1",
        leverage=10,
        entry_result=entry,
        position_id="pos-fact-1",
        protection_result=protection,
        stop_loss_price=Decimal("63000"),
        take_profit_price=Decimal("68000"),
        stop_client_order_id="A2S-fact-1",
        tp_client_order_id="A2T-fact-1",
    )

    session: Session = fact_db()
    try:
        intent = session.get(V2ExecutionIntent, "intent-fact-1")
        assert intent is not None
        assert intent.state == V2IntentState.FILLED.value

        orders = list(session.scalars(select(V2ExchangeOrder)))
        assert len(orders) == 1
        assert orders[0].exchange_order_id == "xo-entry-1"

        fills = list(session.scalars(select(V2ExchangeFill)))
        assert len(fills) == 2
        assert {f.trade_id for f in fills} == {"trade-a", "trade-b"}

        pos = session.get(V2ManagedPosition, "pos-fact-1")
        assert pos is not None
        assert pos.state == V2PositionState.PROTECTED.value
        assert Decimal(str(pos.quantity)) == Decimal("0.002")

        protections = list(session.scalars(select(V2ProtectionRecord)))
        assert len(protections) == 1
        assert protections[0].state == V2ProtectionState.PROTECTION_ACTIVE.value
        assert protections[0].stop_exchange_order_id == "xo-stop-1"
    finally:
        session.close()


def test_acknowledged_unfilled_order_is_durable_before_projection(fact_db) -> None:
    persist_entry_intent_before_submission(
        cycle_id="cycle-ack",
        decision_id="decision-ack",
        intent_id="intent-ack",
        symbol="ETH/USDT",
        direction="long",
        candidate_key="testnet_sampling_v2",
        candidate_type=V2CandidateType.SAMPLING,
        execution_mode=V2ExecutionMode.BINANCE_TESTNET,
        decision_bar_timestamp=datetime(2026, 7, 29, 9, 30, tzinfo=UTC),
        fencing_token="fence-ack",
        initial_risk_usdt=Decimal("2.50"),
    )
    session: Session = fact_db()
    try:
        intent = session.get(V2ExecutionIntent, "intent-ack")
        assert intent is not None
        assert intent.state == V2IntentState.EXCHANGE_SUBMITTING.value
        assert list(session.scalars(select(V2ExchangeOrder))) == []
    finally:
        session.close()

    persist_entry_submission_result(
        intent_id="intent-ack",
        leverage=40,
        result=EntryExecutionResult(
            status=EntryExecutionStatus.ACKNOWLEDGED_UNFILLED,
            intent_state=V2IntentState.EXCHANGE_ACKNOWLEDGED,
            client_order_id="A2E-ack-1",
            exchange_order_id="14907989375",
            reason_code=DecisionReasonCode.OK,
            requested_quantity=Decimal("0.05"),
            detail="order acknowledged with no confirmed fill",
        ),
    )

    session = fact_db()
    try:
        intent = session.get(V2ExecutionIntent, "intent-ack")
        assert intent is not None
        assert intent.state == V2IntentState.EXCHANGE_ACKNOWLEDGED.value
        order = session.scalar(select(V2ExchangeOrder))
        assert order is not None
        assert order.client_order_id == "A2E-ack-1"
        assert order.exchange_order_id == "14907989375"
        assert Decimal(str(order.quantity)) == Decimal("0.05")
        assert list(session.scalars(select(V2ExchangeFill))) == []
        assert list(session.scalars(select(V2ManagedPosition))) == []
    finally:
        session.close()


def test_confirmed_delayed_fill_corrects_cancelled_intent(fact_db) -> None:
    persist_entry_intent_before_submission(
        cycle_id="cycle-delayed",
        decision_id="decision-delayed",
        intent_id="intent-delayed",
        symbol="BTC/USDT",
        direction="long",
        candidate_key="testnet_sampling_v2",
        candidate_type=V2CandidateType.SAMPLING,
        execution_mode=V2ExecutionMode.BINANCE_TESTNET,
        decision_bar_timestamp=datetime(2026, 7, 30, 3, 0, tzinfo=UTC),
        fencing_token="fence-delayed",
        initial_risk_usdt=Decimal("5.00"),
    )
    persist_entry_submission_result(
        intent_id="intent-delayed",
        leverage=40,
        result=EntryExecutionResult(
            status=EntryExecutionStatus.ACKNOWLEDGED_UNFILLED,
            intent_state=V2IntentState.EXCHANGE_ACKNOWLEDGED,
            client_order_id="A2E-delayed",
            exchange_order_id="xo-delayed",
            reason_code=DecisionReasonCode.OK,
            requested_quantity=Decimal("0.0049"),
        ),
    )
    session: Session = fact_db()
    intent = session.get(V2ExecutionIntent, "intent-delayed")
    assert intent is not None
    intent.state = V2IntentState.CANCELLED.value
    session.commit()
    session.close()

    persist_entry_and_protection(
        cycle_id="cycle-delayed",
        decision_id="decision-delayed",
        intent_id="intent-delayed",
        symbol="BTC/USDT",
        direction="long",
        candidate_key="testnet_sampling_v2",
        candidate_type=V2CandidateType.SAMPLING,
        execution_mode=V2ExecutionMode.BINANCE_TESTNET,
        decision_bar_timestamp=datetime(2026, 7, 30, 3, 0, tzinfo=UTC),
        fencing_token="fence-delayed",
        leverage=40,
        entry_result=EntryExecutionResult(
            status=EntryExecutionStatus.FILLED,
            intent_state=V2IntentState.FILLED,
            client_order_id="A2E-delayed",
            exchange_order_id="xo-delayed",
            trade_ids=("trade-delayed",),
            reason_code=DecisionReasonCode.OK,
            requested_quantity=Decimal("0.0049"),
            filled_quantity=Decimal("0.0049"),
            average_fill_price=Decimal("64159.9"),
            total_fee=Decimal("0.1"),
            fill_timestamp=datetime(2026, 7, 30, 3, 0, 14, tzinfo=UTC),
        ),
        position_id="pos-delayed",
        protection_result=None,
        stop_loss_price=None,
        take_profit_price=None,
        stop_client_order_id=None,
        tp_client_order_id=None,
    )

    session = fact_db()
    try:
        intent = session.get(V2ExecutionIntent, "intent-delayed")
        assert intent is not None
        assert intent.state == V2IntentState.FILLED.value
        assert session.get(V2ManagedPosition, "pos-delayed") is not None
    finally:
        session.close()


def test_replaying_confirmed_entry_fill_is_idempotent(fact_db) -> None:
    """A repeated Binance recovery observation must not duplicate its fill fact."""
    entry = EntryExecutionResult(
        status=EntryExecutionStatus.FILLED,
        intent_state=V2IntentState.FILLED,
        client_order_id="A2E-replay-fill",
        exchange_order_id="xo-replay-fill",
        trade_ids=("trade-replay-fill",),
        filled_quantity=Decimal("0.01"),
        average_fill_price=Decimal("65000"),
        total_fee=Decimal("0.01"),
        fill_timestamp=datetime(2026, 8, 18, 1, 0, tzinfo=UTC),
    )
    kwargs = {
        "cycle_id": "cycle-replay-fill",
        "decision_id": "decision-replay-fill",
        "intent_id": "intent-replay-fill",
        "symbol": "BTC/USDT",
        "direction": "long",
        "candidate_key": "testnet_sampling_v2",
        "candidate_type": V2CandidateType.SAMPLING,
        "execution_mode": V2ExecutionMode.BINANCE_TESTNET,
        "decision_bar_timestamp": datetime(2026, 8, 18, 0, 45, tzinfo=UTC),
        "fencing_token": "fence-replay-fill",
        "leverage": 30,
        "entry_result": entry,
        "position_id": "pos-replay-fill",
        "protection_result": None,
        "stop_loss_price": None,
        "take_profit_price": None,
        "stop_client_order_id": None,
        "tp_client_order_id": None,
    }

    # Simulate the original race: the fill committed, but position projection
    # did not. Recovery then replays the same exchange fill and projects once.
    persist_entry_and_protection(**{**kwargs, "project_position": False})
    persist_entry_and_protection(**kwargs)

    session: Session = fact_db()
    try:
        fills = list(session.scalars(select(V2ExchangeFill)))
        assert len(fills) == 1
        assert fills[0].trade_id == "trade-replay-fill"
        assert session.get(V2ManagedPosition, "pos-replay-fill") is not None
    finally:
        session.close()


def test_order_status_recovery_is_projectable_without_trade_id() -> None:
    result = EntryExecutionResult(
        status=EntryExecutionStatus.FILLED,
        intent_state=V2IntentState.FILLED,
        client_order_id="A2E-order-status",
        exchange_order_id="14907989375",
        filled_quantity=Decimal("0.445"),
        average_fill_price=Decimal("4200"),
        fill_source="BINANCE_ORDER_STATUS_RECOVERY",
    )

    assert result.position_projectable is True


def test_user_trade_fill_without_trade_id_is_not_projectable() -> None:
    result = EntryExecutionResult(
        status=EntryExecutionStatus.FILLED,
        intent_state=V2IntentState.FILLED,
        client_order_id="A2E-missing-trade",
        exchange_order_id="xo-missing-trade",
        filled_quantity=Decimal("0.445"),
        average_fill_price=Decimal("4200"),
    )

    assert result.position_projectable is False


def test_order_level_fill_recovery_projects_once_without_fake_trade_id(fact_db) -> None:
    persist_entry_intent_before_submission(
        cycle_id="cycle-order-level",
        decision_id="decision-order-level",
        intent_id="intent-order-level",
        symbol="SOL/USDT",
        direction="long",
        candidate_key="testnet_sampling_v2",
        candidate_type=V2CandidateType.SAMPLING,
        execution_mode=V2ExecutionMode.BINANCE_TESTNET,
        decision_bar_timestamp=datetime(2026, 8, 17, 15, 30, tzinfo=UTC),
        fencing_token="fence-order-level",
        initial_risk_usdt=Decimal("0.60"),
    )
    persist_entry_submission_result(
        intent_id="intent-order-level",
        leverage=5,
        result=SimpleNamespace(
            status=EntryExecutionStatus.ACKNOWLEDGED_UNFILLED,
            intent_state=V2IntentState.EXCHANGE_ACKNOWLEDGED,
            client_order_id="A2E-order-level",
            exchange_order_id="xo-order-level",
            requested_quantity=Decimal("2.26"),
        ),
    )
    session: Session = fact_db()
    try:
        repo = AutomatedTradingRepository(session)
        order = session.scalar(select(V2ExchangeOrder))
        assert order is not None
        first = repo.save_order_level_fill_recovery(
            intent_id="intent-order-level",
            exchange_order_record_id=order.order_record_id,
            account_id="binance_testnet",
            exchange_order_id="xo-order-level",
            symbol="SOL/USDT",
            side="BUY",
            reduce_only=False,
            filled_quantity=Decimal("2.26"),
            fill_price=Decimal("75.90"),
            exchange_event_time=datetime(2026, 8, 17, 15, 31, tzinfo=UTC),
            received_at=datetime(2026, 8, 17, 15, 31, tzinfo=UTC),
        )
        second = repo.save_order_level_fill_recovery(
            intent_id="intent-order-level",
            exchange_order_record_id=order.order_record_id,
            account_id="binance_testnet",
            exchange_order_id="xo-order-level",
            symbol="SOL/USDT",
            side="BUY",
            reduce_only=False,
            filled_quantity=Decimal("2.26"),
            fill_price=Decimal("75.90"),
            exchange_event_time=datetime(2026, 8, 17, 15, 31, tzinfo=UTC),
            received_at=datetime(2026, 8, 17, 15, 31, tzinfo=UTC),
        )
        repo.reconcile_intent_from_confirmed_fill("intent-order-level")
        repo.project_position_from_confirmed_fills(
            position_id="position-order-level",
            intent_id="intent-order-level",
            order_record_id=order.order_record_id,
            symbol="SOL/USDT",
            direction="long",
            execution_mode=V2ExecutionMode.BINANCE_TESTNET,
            projected_at=datetime(2026, 8, 17, 15, 31, tzinfo=UTC),
        )
        session.commit()
        assert first == second
        fills = list(session.scalars(select(V2ExchangeFill)))
        assert len(fills) == 1
        assert fills[0].trade_id is None
        assert fills[0].fill_source == "BINANCE_ORDER_STATUS_RECOVERY"
        assert session.get(V2ManagedPosition, "position-order-level") is not None
        assert session.get(V2ExecutionIntent, "intent-order-level").state == V2IntentState.FILLED.value
    finally:
        session.close()


def test_persist_skips_when_not_position_projectable(fact_db) -> None:
    entry = EntryExecutionResult(
        status=EntryExecutionStatus.UNKNOWN,
        intent_state=V2IntentState.EXCHANGE_SUBMITTING,
        client_order_id="A2E-unknown",
    )
    persist_entry_and_protection(
        cycle_id="cycle-skip",
        decision_id=None,
        intent_id="intent-skip",
        symbol="BTC/USDT",
        direction="long",
        candidate_key="x",
        candidate_type=V2CandidateType.SAMPLING,
        execution_mode=V2ExecutionMode.BINANCE_TESTNET,
        decision_bar_timestamp=datetime(2026, 7, 29, 11, 45, tzinfo=UTC),
        fencing_token="fence-skip",
        leverage=10,
        entry_result=entry,
        position_id="pos-skip",
        protection_result=None,
        stop_loss_price=None,
        take_profit_price=None,
        stop_client_order_id=None,
        tp_client_order_id=None,
    )
    session: Session = fact_db()
    try:
        assert session.get(V2ExecutionIntent, "intent-skip") is None
        assert list(session.scalars(select(V2ManagedPosition))) == []
    finally:
        session.close()


def test_persist_exit_result_writes_reduce_only_fill_and_closes_position(fact_db) -> None:
    entry = EntryExecutionResult(
        status=EntryExecutionStatus.FILLED,
        intent_state=V2IntentState.FILLED,
        client_order_id="A2E-exit-fact",
        exchange_order_id="xo-entry-exit-fact",
        trade_ids=("trade-entry-exit-fact",),
        filled_quantity=Decimal("0.002"),
        average_fill_price=Decimal("65000"),
        total_fee=Decimal("0.13"),
        fill_timestamp=datetime(2026, 7, 29, 12, 0, tzinfo=UTC),
    )
    persist_entry_and_protection(
        cycle_id="cycle-entry-exit-fact",
        decision_id="decision-entry-exit-fact",
        intent_id="intent-entry-exit-fact",
        symbol="BTC/USDT",
        direction="long",
        candidate_key="testnet_sampling_v2",
        candidate_type=V2CandidateType.SAMPLING,
        execution_mode=V2ExecutionMode.BINANCE_TESTNET,
        decision_bar_timestamp=datetime(2026, 7, 29, 11, 45, tzinfo=UTC),
        fencing_token="fence-entry-exit-fact",
        leverage=10,
        entry_result=entry,
        position_id="pos-entry-exit-fact",
        protection_result=SimpleNamespace(
            is_active=True,
            stop_exchange_order_id="xo-stop-exit-fact",
            tp_exchange_order_id="xo-tp-exit-fact",
        ),
        stop_loss_price=Decimal("63000"),
        take_profit_price=Decimal("68000"),
        stop_client_order_id="A2S-exit-fact",
        tp_client_order_id="A2T-exit-fact",
    )

    persist_exit_result(
        cycle_id="cycle-exit-fact",
        position_id="pos-entry-exit-fact",
        execution_mode=V2ExecutionMode.BINANCE_TESTNET,
        reason=ExitReason.TAKE_PROFIT,
        result=ExitExecutionResult(
            status=ExitExecutionStatus.CLOSED,
            position_state=V2PositionState.CLOSED,
            client_order_id="A2T-exit-fact",
            exchange_order_id="xo-tp-exit-fact",
            trade_ids=("trade-exit-fact",),
            reduced_quantity=Decimal("0.002"),
            average_fill_price=Decimal("68000"),
            total_fee=Decimal("0.136"),
            remaining_quantity=Decimal("0"),
            fill_timestamp=datetime(2026, 7, 29, 13, 0, tzinfo=UTC),
        ),
        fencing_token="fence-exit-fact",
    )

    session: Session = fact_db()
    try:
        position = session.get(V2ManagedPosition, "pos-entry-exit-fact")
        assert position is not None
        assert position.state == V2PositionState.CLOSED.value
        assert position.closed_at is not None
        assert Decimal(str(position.realized_pnl)) == Decimal("5.7340")

        exit_fill = session.scalar(select(V2ExchangeFill).where(V2ExchangeFill.trade_id == "trade-exit-fact"))
        assert exit_fill is not None
        assert exit_fill.reduce_only is True
        assert exit_fill.exchange_order_id == "xo-tp-exit-fact"

        protection = session.scalar(
            select(V2ProtectionRecord).where(V2ProtectionRecord.position_id == "pos-entry-exit-fact")
        )
        assert protection is not None
        assert protection.state == V2ProtectionState.PROTECTION_FILLED.value
    finally:
        session.close()


def test_acknowledged_unfilled_exit_is_durable_before_fill(fact_db) -> None:
    entry = EntryExecutionResult(
        status=EntryExecutionStatus.FILLED,
        intent_state=V2IntentState.FILLED,
        client_order_id="A2E-exit-ack",
        exchange_order_id="xo-entry-exit-ack",
        trade_ids=("trade-entry-exit-ack",),
        filled_quantity=Decimal("0.164"),
        average_fill_price=Decimal("1919.51"),
        total_fee=Decimal("0.12591985"),
        fill_timestamp=datetime(2026, 7, 29, 9, 30, tzinfo=UTC),
    )
    persist_entry_and_protection(
        cycle_id="cycle-entry-exit-ack",
        decision_id="decision-entry-exit-ack",
        intent_id="intent-entry-exit-ack",
        symbol="ETH/USDT",
        direction="long",
        candidate_key="testnet_sampling_v2",
        candidate_type=V2CandidateType.SAMPLING,
        execution_mode=V2ExecutionMode.BINANCE_TESTNET,
        decision_bar_timestamp=datetime(2026, 7, 29, 9, 30, tzinfo=UTC),
        fencing_token="fence-entry-exit-ack",
        leverage=40,
        entry_result=entry,
        position_id="pos-exit-ack",
        protection_result=SimpleNamespace(
            is_active=True,
            stop_exchange_order_id="xo-stop-exit-ack",
            tp_exchange_order_id="xo-tp-exit-ack",
        ),
        stop_loss_price=Decimal("1900"),
        take_profit_price=Decimal("1950"),
        stop_client_order_id="A2S-exit-ack",
        tp_client_order_id="A2T-exit-ack",
    )

    exit_intent_id = persist_exit_intent_before_submission(
        cycle_id="cycle-exit-ack",
        position_id="pos-exit-ack",
        execution_mode=V2ExecutionMode.BINANCE_TESTNET,
        reason=ExitReason.PROTECTION_FAILURE_EMERGENCY,
        client_order_id="A2X-exit-ack-2",
        requested_quantity=Decimal("0.164"),
        fencing_token="fence-exit-ack",
        submitted_at=datetime(2026, 7, 29, 9, 50, tzinfo=UTC),
    )

    session: Session = fact_db()
    try:
        intent = session.get(V2ExecutionIntent, exit_intent_id)
        assert intent is not None
        assert intent.state == V2IntentState.EXCHANGE_SUBMITTING.value
        order = session.scalar(select(V2ExchangeOrder).where(V2ExchangeOrder.client_order_id == "A2X-exit-ack-2"))
        assert order is not None
        assert order.exchange_order_id is None
        position = session.get(V2ManagedPosition, "pos-exit-ack")
        assert position is not None
        assert position.state == V2PositionState.PROTECTED.value
    finally:
        session.close()

    persist_exit_submission_result(
        position_id="pos-exit-ack",
        result=ExitExecutionResult(
            status=ExitExecutionStatus.SUBMITTED_UNCONFIRMED,
            position_state=V2PositionState.REDUCING,
            client_order_id="A2X-exit-ack-2",
            exchange_order_id="14909451503",
            detail="order acknowledged with no confirmed fill",
        ),
    )

    session = fact_db()
    try:
        intent = session.get(V2ExecutionIntent, exit_intent_id)
        assert intent is not None
        assert intent.state == V2IntentState.EXCHANGE_ACKNOWLEDGED.value
        order = session.scalar(select(V2ExchangeOrder).where(V2ExchangeOrder.client_order_id == "A2X-exit-ack-2"))
        assert order is not None
        assert order.exchange_order_id == "14909451503"
        assert (
            list(
                session.scalars(
                    select(V2ExchangeFill).where(V2ExchangeFill.exchange_order_record_id == order.order_record_id)
                )
            )
            == []
        )
        position = session.get(V2ManagedPosition, "pos-exit-ack")
        assert position is not None
        assert position.state == V2PositionState.REDUCING.value
    finally:
        session.close()


def test_confirmed_exit_repairs_quarantined_projection_to_closed(fact_db) -> None:
    entry = EntryExecutionResult(
        status=EntryExecutionStatus.FILLED,
        intent_state=V2IntentState.FILLED,
        client_order_id="A2E-quarantine-repair",
        exchange_order_id="xo-entry-quarantine-repair",
        trade_ids=("trade-entry-quarantine-repair",),
        filled_quantity=Decimal("0.164"),
        average_fill_price=Decimal("1919.51"),
        total_fee=Decimal("0.12591985"),
        fill_timestamp=datetime(2026, 7, 29, 9, 30, tzinfo=UTC),
    )
    persist_entry_and_protection(
        cycle_id="cycle-entry-quarantine-repair",
        decision_id="decision-entry-quarantine-repair",
        intent_id="intent-entry-quarantine-repair",
        symbol="ETH/USDT",
        direction="long",
        candidate_key="testnet_sampling_v2",
        candidate_type=V2CandidateType.SAMPLING,
        execution_mode=V2ExecutionMode.BINANCE_TESTNET,
        decision_bar_timestamp=datetime(2026, 7, 29, 9, 30, tzinfo=UTC),
        fencing_token="fence-entry-quarantine-repair",
        leverage=40,
        entry_result=entry,
        position_id="pos-quarantine-repair",
        protection_result=SimpleNamespace(
            is_active=True,
            stop_exchange_order_id="xo-stop-quarantine-repair",
            tp_exchange_order_id="xo-tp-quarantine-repair",
        ),
        stop_loss_price=Decimal("1900"),
        take_profit_price=Decimal("1950"),
        stop_client_order_id="A2S-quarantine-repair",
        tp_client_order_id="A2T-quarantine-repair",
    )
    session: Session = fact_db()
    try:
        position = session.get(V2ManagedPosition, "pos-quarantine-repair")
        assert position is not None
        position.state = V2PositionState.QUARANTINED.value
        position.version += 1
        session.commit()
    finally:
        session.close()

    persist_exit_result(
        cycle_id="cycle-exit-quarantine-repair",
        position_id="pos-quarantine-repair",
        execution_mode=V2ExecutionMode.BINANCE_TESTNET,
        reason=ExitReason.PROTECTION_FAILURE_EMERGENCY,
        result=ExitExecutionResult(
            status=ExitExecutionStatus.CLOSED,
            position_state=V2PositionState.CLOSED,
            client_order_id="A2X-quarantine-repair-2",
            exchange_order_id="14909451503",
            trade_ids=("308716592",),
            reduced_quantity=Decimal("0.164"),
            average_fill_price=Decimal("1918.5"),
            total_fee=Decimal("0.1258536"),
            remaining_quantity=Decimal("0"),
            fill_timestamp=datetime(2026, 7, 29, 9, 50, 39, tzinfo=UTC),
        ),
        fencing_token="fence-exit-quarantine-repair",
    )

    session = fact_db()
    try:
        position = session.get(V2ManagedPosition, "pos-quarantine-repair")
        assert position is not None
        assert position.state == V2PositionState.CLOSED.value
        assert position.closed_at is not None
        exit_fill = session.scalar(select(V2ExchangeFill).where(V2ExchangeFill.trade_id == "308716592"))
        assert exit_fill is not None
        assert exit_fill.reduce_only is True
        protection = session.scalar(
            select(V2ProtectionRecord).where(V2ProtectionRecord.position_id == "pos-quarantine-repair")
        )
        assert protection is not None
        assert protection.state == V2ProtectionState.PROTECTION_CANCELLED.value
    finally:
        session.close()


def test_reconciliation_cancels_absent_protection_for_closed_position(fact_db) -> None:
    entry = EntryExecutionResult(
        status=EntryExecutionStatus.FILLED,
        intent_state=V2IntentState.FILLED,
        client_order_id="A2E-closed-protection",
        exchange_order_id="xo-entry-closed-protection",
        trade_ids=("trade-entry-closed-protection",),
        filled_quantity=Decimal("0.01"),
        average_fill_price=Decimal("65000"),
        total_fee=Decimal("0.1"),
        fill_timestamp=datetime(2026, 7, 29, 10, 0, tzinfo=UTC),
    )
    persist_entry_and_protection(
        cycle_id="cycle-closed-protection",
        decision_id="decision-closed-protection",
        intent_id="intent-closed-protection",
        symbol="BTC/USDT",
        direction="long",
        candidate_key="testnet_sampling_v2",
        candidate_type=V2CandidateType.SAMPLING,
        execution_mode=V2ExecutionMode.BINANCE_TESTNET,
        decision_bar_timestamp=datetime(2026, 7, 29, 10, 0, tzinfo=UTC),
        fencing_token="fence-closed-protection",
        leverage=40,
        entry_result=entry,
        position_id="pos-closed-protection",
        protection_result=SimpleNamespace(
            is_active=True,
            stop_exchange_order_id="xo-stop-closed-protection",
            tp_exchange_order_id="xo-tp-closed-protection",
        ),
        stop_loss_price=Decimal("64000"),
        take_profit_price=Decimal("67000"),
        stop_client_order_id="A2S-closed-protection",
        tp_client_order_id="A2T-closed-protection",
    )
    session: Session = fact_db()
    try:
        position = session.get(V2ManagedPosition, "pos-closed-protection")
        assert position is not None
        position.state = V2PositionState.CLOSED.value
        position.closed_at = datetime(2026, 7, 29, 10, 5, tzinfo=UTC)
        position.version += 1
        session.commit()
    finally:
        session.close()

    changed = reconcile_closed_position_protections(
        execution_mode=V2ExecutionMode.BINANCE_TESTNET,
        symbol="BTC/USDT",
        exchange_open_order_ids=frozenset(),
        observed_at=datetime(2026, 7, 29, 10, 10, tzinfo=UTC),
    )

    assert changed == 1
    session = fact_db()
    try:
        protection = session.scalar(
            select(V2ProtectionRecord).where(V2ProtectionRecord.position_id == "pos-closed-protection")
        )
        assert protection is not None
        assert protection.state == V2ProtectionState.PROTECTION_CANCELLED.value
    finally:
        session.close()
