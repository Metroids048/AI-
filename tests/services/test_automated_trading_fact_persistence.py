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
from services.automated_trading.application.fact_persistence import persist_entry_and_protection
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
