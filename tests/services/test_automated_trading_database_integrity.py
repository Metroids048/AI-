"""Gate 1: V2 database integrity and forced state-machine transitions.

These tests hit the production AutomatedTradingRepository and real SQLite
schema (create_all + foreign_keys=ON + partial unique index). Fake repositories
are forbidden.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from services.automated_trading.domain.enums import (
    V2CandidateType,
    V2ExecutionMode,
    V2IntentState,
    V2PositionState,
    V2ProtectionState,
)
from services.automated_trading.infrastructure.models import Base, V2ManagedPosition  # noqa: F401
from services.automated_trading.infrastructure.repository import AutomatedTradingRepository


@pytest.fixture
def integrity_db() -> Session:
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_conn, _connection_record):  # type: ignore[no-untyped-def]
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    # Ensure partial unique index exists even if create_all omitted dialect opts
    with engine.begin() as conn:
        conn.execute(text("DROP INDEX IF EXISTS ix_v2_position_one_open_per_symbol_direction_mode"))
        conn.execute(
            text(
                """
                CREATE UNIQUE INDEX ix_v2_position_one_open_per_symbol_direction_mode
                ON v2_managed_positions (symbol, direction, execution_mode)
                WHERE state NOT IN ('CLOSED', 'QUARANTINED')
                """
            )
        )
    session = Session(engine)
    yield session
    session.close()


@pytest.fixture
def repo(integrity_db: Session) -> AutomatedTradingRepository:
    return AutomatedTradingRepository(integrity_db)


def _seed_cycle(repo: AutomatedTradingRepository, cycle_id: str = "cycle-1") -> None:
    repo.create_cycle(
        cycle_id=cycle_id,
        symbol="BTC/USDT",
        timeframe="15m",
        bar_timestamp=datetime(2026, 7, 28, 10, 0, tzinfo=UTC),
        execution_mode=V2ExecutionMode.BINANCE_TESTNET,
        fencing_token=f"fence-{cycle_id}",
    )


def _seed_decision(
    repo: AutomatedTradingRepository,
    *,
    decision_id: str,
    cycle_id: str = "cycle-1",
) -> None:
    repo.create_decision(
        decision_id=decision_id,
        cycle_id=cycle_id,
        candidate_key="primary_mature",
        terminal_reason=None,
        payload={},
    )


def _seed_intent(
    repo: AutomatedTradingRepository,
    *,
    intent_id: str,
    cycle_id: str = "cycle-1",
    state: V2IntentState = V2IntentState.INTENT_CREATED,
    decision_id: str | None = None,
) -> None:
    repo.create_intent(
        intent_id=intent_id,
        cycle_id=cycle_id,
        symbol="BTC/USDT",
        direction="long",
        candidate_key="primary_mature",
        candidate_type=V2CandidateType.PRIMARY,
        execution_mode=V2ExecutionMode.BINANCE_TESTNET,
        decision_bar_timestamp=datetime(2026, 7, 28, 10, 0, tzinfo=UTC),
        decision_funnel_id=None,
        state=state,
        decision_id=decision_id,
    )


def _seed_filled_order(
    repo: AutomatedTradingRepository,
    *,
    intent_id: str,
    client_order_id: str,
    exchange_order_id: str,
    trade_id: str,
) -> str:
    order_id = repo.save_order_submission(
        intent_id=intent_id,
        client_order_id=client_order_id,
        quantity=0.001,
        leverage=20,
        submitted_at=datetime(2026, 7, 28, 10, 0, tzinfo=UTC),
    )
    repo.save_exchange_order_receipt(
        order_record_id=order_id,
        exchange_order_id=exchange_order_id,
        acknowledged_at=datetime(2026, 7, 28, 10, 0, 30, tzinfo=UTC),
    )
    repo.save_exchange_fill_receipt(
        intent_id=intent_id,
        exchange_order_record_id=order_id,
        account_id="binance_testnet",
        exchange_order_id=exchange_order_id,
        trade_id=trade_id,
        symbol="BTC/USDT",
        side="BUY",
        reduce_only=False,
        filled_quantity=Decimal("0.001"),
        fill_price=Decimal("65000.0"),
        commission=Decimal("0.65"),
        commission_asset="USDT",
        exchange_event_time=datetime(2026, 7, 28, 10, 1, tzinfo=UTC),
        received_at=datetime(2026, 7, 28, 10, 1, tzinfo=UTC),
        raw_hash=f"hash-{trade_id}",
    )
    return order_id


def test_repository_rejects_filled_to_intent_created_transition(repo: AutomatedTradingRepository) -> None:
    _seed_cycle(repo)
    _seed_intent(repo, intent_id="intent-filled", state=V2IntentState.INTENT_CREATED)
    repo.transition_intent(
        intent_id="intent-filled",
        expected_current=V2IntentState.INTENT_CREATED,
        next_state=V2IntentState.EXCHANGE_SUBMITTING,
        event_type="IntentSubmitting",
        payload={},
    )
    repo.transition_intent(
        intent_id="intent-filled",
        expected_current=V2IntentState.EXCHANGE_SUBMITTING,
        next_state=V2IntentState.FILLED,
        event_type="IntentFilled",
        payload={},
    )
    with pytest.raises(ValueError, match="Illegal V2 intent transition"):
        repo.transition_intent(
            intent_id="intent-filled",
            expected_current=V2IntentState.FILLED,
            next_state=V2IntentState.INTENT_CREATED,
            event_type="Illegal",
            payload={},
        )


def test_repository_rejects_closed_position_reopen(repo: AutomatedTradingRepository) -> None:
    _seed_cycle(repo)
    _seed_decision(repo, decision_id="dec-pos", cycle_id="cycle-1")
    _seed_intent(repo, intent_id="intent-pos", decision_id="dec-pos")
    order_id = _seed_filled_order(
        repo,
        intent_id="intent-pos",
        client_order_id="co-pos",
        exchange_order_id="xo-pos",
        trade_id="trade-pos",
    )
    repo.project_position_from_confirmed_fills(
        position_id="pos-1",
        intent_id="intent-pos",
        order_record_id=order_id,
        symbol="BTC/USDT",
        direction="long",
        execution_mode=V2ExecutionMode.BINANCE_TESTNET,
        projected_at=datetime(2026, 7, 28, 10, 2, tzinfo=UTC),
    )
    repo.transition_position(
        position_id="pos-1",
        expected_current=V2PositionState.POSITION_PROJECTED,
        next_state=V2PositionState.PROTECTED,
        event_type="PositionProtected",
        payload={},
    )
    repo.transition_position(
        position_id="pos-1",
        expected_current=V2PositionState.PROTECTED,
        next_state=V2PositionState.CLOSED,
        event_type="PositionClosed",
        payload={},
    )
    with pytest.raises(ValueError, match="Illegal V2 position transition"):
        repo.transition_position(
            position_id="pos-1",
            expected_current=V2PositionState.CLOSED,
            next_state=V2PositionState.POSITION_PROJECTED,
            event_type="IllegalReopen",
            payload={},
        )


def test_repository_rejects_active_protection_without_exchange_order_id(
    repo: AutomatedTradingRepository,
) -> None:
    _seed_cycle(repo)
    _seed_decision(repo, decision_id="dec-prot", cycle_id="cycle-1")
    _seed_intent(repo, intent_id="intent-prot", decision_id="dec-prot")
    order_id = _seed_filled_order(
        repo,
        intent_id="intent-prot",
        client_order_id="co-prot",
        exchange_order_id="xo-prot",
        trade_id="trade-prot",
    )
    repo.project_position_from_confirmed_fills(
        position_id="pos-prot",
        intent_id="intent-prot",
        order_record_id=order_id,
        symbol="BTC/USDT",
        direction="long",
        execution_mode=V2ExecutionMode.BINANCE_TESTNET,
        projected_at=datetime(2026, 7, 28, 10, 2, tzinfo=UTC),
    )
    repo.save_protection(
        protection_id="prot-1",
        position_id="pos-prot",
        stop_loss_price=64000.0,
        take_profit_price=67000.0,
        stop_client_order_id="stop-1",
        tp_client_order_id="tp-1",
        state=V2ProtectionState.PROTECTION_INTENT,
    )
    repo.transition_protection(
        protection_id="prot-1",
        expected_current=V2ProtectionState.PROTECTION_INTENT,
        next_state=V2ProtectionState.PROTECTION_SUBMITTING,
        event_type="ProtectionSubmitting",
        payload={},
    )
    with pytest.raises(ValueError, match="exchange"):
        repo.transition_protection(
            protection_id="prot-1",
            expected_current=V2ProtectionState.PROTECTION_SUBMITTING,
            next_state=V2ProtectionState.PROTECTION_ACTIVE,
            event_type="ProtectionActive",
            payload={},
        )


def test_save_protection_rejects_active_without_exchange_order_id(
    repo: AutomatedTradingRepository,
) -> None:
    """Independent-review counterexample: create-path must not bypass ACTIVE invariant."""
    _seed_cycle(repo)
    _seed_decision(repo, decision_id="dec-save-active", cycle_id="cycle-1")
    _seed_intent(repo, intent_id="intent-save-active", decision_id="dec-save-active")
    order_id = _seed_filled_order(
        repo,
        intent_id="intent-save-active",
        client_order_id="co-save-active",
        exchange_order_id="xo-save-active",
        trade_id="trade-save-active",
    )
    repo.project_position_from_confirmed_fills(
        position_id="pos-save-active",
        intent_id="intent-save-active",
        order_record_id=order_id,
        symbol="BTC/USDT",
        direction="long",
        execution_mode=V2ExecutionMode.BINANCE_TESTNET,
        projected_at=datetime(2026, 7, 28, 10, 2, tzinfo=UTC),
    )
    with pytest.raises(ValueError, match="PROTECTION_INTENT|exchange"):
        repo.save_protection(
            protection_id="prot-active-bypass",
            position_id="pos-save-active",
            stop_loss_price=64000.0,
            take_profit_price=67000.0,
            stop_client_order_id="stop-bypass",
            tp_client_order_id="tp-bypass",
            state=V2ProtectionState.PROTECTION_ACTIVE,
        )


def test_db_rejects_active_protection_without_exchange_order_id(
    repo: AutomatedTradingRepository,
    integrity_db: Session,
) -> None:
    """Independent-review counterexample: DB CHECK must reject ACTIVE without XO."""
    from sqlalchemy import text

    _seed_cycle(repo)
    _seed_decision(repo, decision_id="dec-sql-active", cycle_id="cycle-1")
    _seed_intent(repo, intent_id="intent-sql-active", decision_id="dec-sql-active")
    order_id = _seed_filled_order(
        repo,
        intent_id="intent-sql-active",
        client_order_id="co-sql-active",
        exchange_order_id="xo-sql-active",
        trade_id="trade-sql-active",
    )
    repo.project_position_from_confirmed_fills(
        position_id="pos-sql-active",
        intent_id="intent-sql-active",
        order_record_id=order_id,
        symbol="BTC/USDT",
        direction="long",
        execution_mode=V2ExecutionMode.BINANCE_TESTNET,
        projected_at=datetime(2026, 7, 28, 10, 2, tzinfo=UTC),
    )
    integrity_db.commit()
    with pytest.raises(IntegrityError):
        integrity_db.execute(
            text(
                """
                INSERT INTO v2_protection_records (
                    protection_id, position_id, stop_loss_price, take_profit_price,
                    stop_client_order_id, tp_client_order_id,
                    stop_exchange_order_id, tp_exchange_order_id,
                    state, version, created_at, activated_at
                ) VALUES (
                    'prot-sql-active', 'pos-sql-active', 64000, 67000,
                    'stop-sql', 'tp-sql', NULL, NULL,
                    'PROTECTION_ACTIVE', 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                """
            )
        )
        integrity_db.commit()
    integrity_db.rollback()


def test_project_position_rejects_fabricated_fill_without_persisted_rows(
    repo: AutomatedTradingRepository,
) -> None:
    """Caller-supplied FillReceipt must not invent fill rows during projection."""
    from services.automated_trading.domain.receipts import FillReceipt

    _seed_cycle(repo)
    _seed_intent(repo, intent_id="intent-fabricated")
    order_id = repo.save_order_submission(
        intent_id="intent-fabricated",
        client_order_id="co-fabricated",
        quantity=0.001,
        leverage=20,
        submitted_at=datetime(2026, 7, 28, 10, 0, tzinfo=UTC),
    )
    repo.save_exchange_order_receipt(
        order_record_id=order_id,
        exchange_order_id="xo-fabricated",
        acknowledged_at=datetime(2026, 7, 28, 10, 0, 30, tzinfo=UTC),
    )
    fake = FillReceipt(
        intent_id="intent-fabricated",
        exchange_order_id="xo-fabricated",
        trade_ids=("fabricated-trade",),
        filled_quantity=Decimal("0.001"),
        average_fill_price=Decimal("999999.0"),
        total_fee=Decimal("0.01"),
        fill_timestamp=datetime(2026, 7, 28, 10, 1, tzinfo=UTC),
    )
    with pytest.raises(ValueError, match="confirmed fills|v2_exchange_fills"):
        repo.project_position(
            position_id="pos-fabricated",
            intent_id="intent-fabricated",
            order_record_id=order_id,
            symbol="BTC/USDT",
            direction="long",
            execution_mode=V2ExecutionMode.BINANCE_TESTNET,
            fill_receipt=fake,
            state=V2PositionState.POSITION_PROJECTED,
            projected_at=datetime(2026, 7, 28, 10, 2, tzinfo=UTC),
        )


def test_duplicate_exchange_order_id_is_rejected(repo: AutomatedTradingRepository, integrity_db: Session) -> None:
    _seed_cycle(repo)
    _seed_intent(repo, intent_id="intent-a")
    _seed_intent(repo, intent_id="intent-b")
    order_a = repo.save_order_submission(
        intent_id="intent-a",
        client_order_id="co-a",
        quantity=0.001,
        leverage=20,
        submitted_at=datetime(2026, 7, 28, 10, 0, tzinfo=UTC),
    )
    order_b = repo.save_order_submission(
        intent_id="intent-b",
        client_order_id="co-b",
        quantity=0.001,
        leverage=20,
        submitted_at=datetime(2026, 7, 28, 10, 0, tzinfo=UTC),
    )
    repo.save_exchange_order_receipt(
        order_record_id=order_a,
        exchange_order_id="dup-xo",
        acknowledged_at=datetime(2026, 7, 28, 10, 0, 30, tzinfo=UTC),
    )
    with pytest.raises(IntegrityError):
        repo.save_exchange_order_receipt(
            order_record_id=order_b,
            exchange_order_id="dup-xo",
            acknowledged_at=datetime(2026, 7, 28, 10, 0, 31, tzinfo=UTC),
        )
        repo.commit()
    integrity_db.rollback()


def test_duplicate_exchange_trade_id_is_rejected(repo: AutomatedTradingRepository, integrity_db: Session) -> None:
    _seed_cycle(repo)
    _seed_intent(repo, intent_id="intent-dup-trade")
    order_id = repo.save_order_submission(
        intent_id="intent-dup-trade",
        client_order_id="co-dup-trade",
        quantity=0.002,
        leverage=20,
        submitted_at=datetime(2026, 7, 28, 10, 0, tzinfo=UTC),
    )
    repo.save_exchange_order_receipt(
        order_record_id=order_id,
        exchange_order_id="xo-dup-trade",
        acknowledged_at=datetime(2026, 7, 28, 10, 0, 30, tzinfo=UTC),
    )
    kwargs = {
        "intent_id": "intent-dup-trade",
        "exchange_order_record_id": order_id,
        "account_id": "binance_testnet",
        "exchange_order_id": "xo-dup-trade",
        "trade_id": "same-trade",
        "symbol": "BTC/USDT",
        "side": "BUY",
        "reduce_only": False,
        "filled_quantity": Decimal("0.001"),
        "fill_price": Decimal("65000.0"),
        "commission": Decimal("0.32"),
        "commission_asset": "USDT",
        "exchange_event_time": datetime(2026, 7, 28, 10, 1, tzinfo=UTC),
        "received_at": datetime(2026, 7, 28, 10, 1, tzinfo=UTC),
        "raw_hash": "hash-1",
    }
    repo.save_exchange_fill_receipt(**kwargs)
    with pytest.raises(IntegrityError):
        repo.save_exchange_fill_receipt(**{**kwargs, "raw_hash": "hash-2"})
        repo.commit()
    integrity_db.rollback()


def test_second_open_position_same_symbol_direction_mode_is_rejected(
    repo: AutomatedTradingRepository,
    integrity_db: Session,
) -> None:
    _seed_cycle(repo, "cycle-open")
    _seed_decision(repo, decision_id="dec-open-1", cycle_id="cycle-open")
    _seed_intent(repo, intent_id="intent-open-1", cycle_id="cycle-open", decision_id="dec-open-1")
    order_1 = _seed_filled_order(
        repo,
        intent_id="intent-open-1",
        client_order_id="co-open-1",
        exchange_order_id="xo-open-1",
        trade_id="trade-open-1",
    )
    repo.project_position_from_confirmed_fills(
        position_id="pos-open-1",
        intent_id="intent-open-1",
        order_record_id=order_1,
        symbol="BTC/USDT",
        direction="long",
        execution_mode=V2ExecutionMode.BINANCE_TESTNET,
        projected_at=datetime(2026, 7, 28, 10, 2, tzinfo=UTC),
    )
    # Transition first position to PROTECTED — still open; second open must fail
    repo.transition_position(
        position_id="pos-open-1",
        expected_current=V2PositionState.POSITION_PROJECTED,
        next_state=V2PositionState.PROTECTED,
        event_type="Protected",
        payload={},
    )
    _seed_decision(repo, decision_id="dec-open-2", cycle_id="cycle-open")
    _seed_intent(repo, intent_id="intent-open-2", cycle_id="cycle-open", decision_id="dec-open-2")
    order_2 = _seed_filled_order(
        repo,
        intent_id="intent-open-2",
        client_order_id="co-open-2",
        exchange_order_id="xo-open-2",
        trade_id="trade-open-2",
    )
    with pytest.raises(IntegrityError):
        repo.project_position_from_confirmed_fills(
            position_id="pos-open-2",
            intent_id="intent-open-2",
            order_record_id=order_2,
            symbol="BTC/USDT",
            direction="long",
            execution_mode=V2ExecutionMode.BINANCE_TESTNET,
            projected_at=datetime(2026, 7, 28, 10, 3, tzinfo=UTC),
        )
        repo.commit()
    integrity_db.rollback()


def test_order_receipt_must_belong_to_same_intent(repo: AutomatedTradingRepository) -> None:
    _seed_cycle(repo)
    _seed_intent(repo, intent_id="intent-owner")
    _seed_intent(repo, intent_id="intent-other")
    order_id = repo.save_order_submission(
        intent_id="intent-owner",
        client_order_id="co-owner",
        quantity=0.001,
        leverage=20,
        submitted_at=datetime(2026, 7, 28, 10, 0, tzinfo=UTC),
    )
    with pytest.raises(ValueError, match="intent"):
        repo.save_exchange_fill_receipt(
            intent_id="intent-other",
            exchange_order_record_id=order_id,
            account_id="binance_testnet",
            exchange_order_id="xo-owner",
            trade_id="trade-mismatch",
            symbol="BTC/USDT",
            side="BUY",
            reduce_only=False,
            filled_quantity=Decimal("0.001"),
            fill_price=Decimal("65000.0"),
            commission=Decimal("0.65"),
            commission_asset="USDT",
            exchange_event_time=datetime(2026, 7, 28, 10, 1, tzinfo=UTC),
            received_at=datetime(2026, 7, 28, 10, 1, tzinfo=UTC),
            raw_hash="hash-mismatch",
        )


def test_fill_receipt_must_match_saved_exchange_order(repo: AutomatedTradingRepository) -> None:
    _seed_cycle(repo)
    _seed_intent(repo, intent_id="intent-match")
    order_id = repo.save_order_submission(
        intent_id="intent-match",
        client_order_id="co-match",
        quantity=0.001,
        leverage=20,
        submitted_at=datetime(2026, 7, 28, 10, 0, tzinfo=UTC),
    )
    repo.save_exchange_order_receipt(
        order_record_id=order_id,
        exchange_order_id="xo-real",
        acknowledged_at=datetime(2026, 7, 28, 10, 0, 30, tzinfo=UTC),
    )
    with pytest.raises(ValueError, match="exchange_order_id"):
        repo.save_exchange_fill_receipt(
            intent_id="intent-match",
            exchange_order_record_id=order_id,
            account_id="binance_testnet",
            exchange_order_id="xo-wrong",
            trade_id="trade-wrong",
            symbol="BTC/USDT",
            side="BUY",
            reduce_only=False,
            filled_quantity=Decimal("0.001"),
            fill_price=Decimal("65000.0"),
            commission=Decimal("0.65"),
            commission_asset="USDT",
            exchange_event_time=datetime(2026, 7, 28, 10, 1, tzinfo=UTC),
            received_at=datetime(2026, 7, 28, 10, 1, tzinfo=UTC),
            raw_hash="hash-wrong",
        )


def test_recovery_required_reconciliation_can_be_persisted(repo: AutomatedTradingRepository) -> None:
    snapshot_id = repo.record_reconciliation(
        execution_mode=V2ExecutionMode.BINANCE_TESTNET,
        exchange_positions=[],
        exchange_open_orders=[],
        local_positions=[],
        discrepancies={"reason": "unknown_order"},
        status="RECOVERY_REQUIRED",
        captured_at=datetime(2026, 7, 28, 10, 10, tzinfo=UTC),
    )
    assert snapshot_id is not None
    repo.commit()


def test_state_transition_appends_event_in_same_transaction(
    repo: AutomatedTradingRepository,
    integrity_db: Session,
) -> None:
    from sqlalchemy import select as sa_select

    from services.automated_trading.infrastructure.models import V2ExecutionEvent

    _seed_cycle(repo)
    _seed_intent(repo, intent_id="intent-evt")
    repo.transition_intent(
        intent_id="intent-evt",
        expected_current=V2IntentState.INTENT_CREATED,
        next_state=V2IntentState.EXCHANGE_SUBMITTING,
        event_type="IntentSubmitting",
        payload={"source": "integrity_test"},
    )
    events = list(
        integrity_db.scalars(sa_select(V2ExecutionEvent).where(V2ExecutionEvent.aggregate_id == "intent-evt"))
    )
    assert len(events) == 1
    assert events[0].event_type == "IntentSubmitting"
    assert not hasattr(repo, "update_intent_state")
    assert not hasattr(repo, "update_position_state")
