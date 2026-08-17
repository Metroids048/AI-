"""Durable E-004 initial-risk reservation contracts."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from services.automated_trading.application.entry_service import EntryExecutionResult, EntryExecutionStatus
from services.automated_trading.application.fact_persistence import persist_entry_and_protection
from services.automated_trading.domain.enums import V2CandidateType, V2ExecutionMode, V2IntentState, V2PositionState
from services.automated_trading.infrastructure.models import Base, V2ExecutionIntent, V2ManagedPosition
from services.automated_trading.infrastructure.repository import AutomatedTradingRepository


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    yield session
    session.close()


def _repo_with_cycle(session: Session) -> AutomatedTradingRepository:
    repo = AutomatedTradingRepository(session)
    repo.create_cycle(
        cycle_id="cycle-e004",
        symbol="BTC/USDT",
        timeframe="15m",
        bar_timestamp=datetime(2026, 8, 17, tzinfo=UTC),
        execution_mode=V2ExecutionMode.BINANCE_TESTNET,
        fencing_token="fence-e004",
    )
    return repo


def _create_intent(
    repo: AutomatedTradingRepository,
    *,
    intent_id: str = "intent-e004",
    state: V2IntentState = V2IntentState.EXCHANGE_SUBMITTING,
    risk: Decimal | None = Decimal("35.50"),
) -> None:
    repo.create_intent(
        intent_id=intent_id,
        cycle_id="cycle-e004",
        symbol="BTC/USDT",
        direction="long",
        candidate_key="testnet_sampling_v2",
        candidate_type=V2CandidateType.SAMPLING,
        execution_mode=V2ExecutionMode.BINANCE_TESTNET,
        decision_bar_timestamp=datetime(2026, 8, 17, tzinfo=UTC),
        decision_funnel_id=None,
        state=state,
        initial_risk_usdt=risk,
    )


def test_new_entry_risk_round_trips_after_new_session(session: Session) -> None:
    repo = _repo_with_cycle(session)
    _create_intent(repo)
    repo.commit()
    session.expire_all()

    intent = session.get(V2ExecutionIntent, "intent-e004")
    assert intent is not None
    assert Decimal(str(intent.initial_risk_usdt)) == Decimal("35.50")


@pytest.mark.parametrize("risk", [Decimal("0"), Decimal("-1"), Decimal("NaN"), Decimal("Infinity")])
def test_non_positive_or_non_finite_new_entry_risk_is_rejected(session: Session, risk: Decimal) -> None:
    repo = _repo_with_cycle(session)

    with pytest.raises(ValueError, match="initial_risk_usdt must be finite and positive"):
        _create_intent(repo, risk=risk)


def test_legacy_terminal_null_risk_is_not_reserved(session: Session) -> None:
    repo = _repo_with_cycle(session)
    _create_intent(repo, state=V2IntentState.CANCELLED, risk=None)
    repo.commit()

    exposures, has_unknown = repo.list_active_initial_risk_exposures(V2ExecutionMode.BINANCE_TESTNET)

    assert exposures == ()
    assert has_unknown is False


def test_legacy_active_null_risk_blocks_new_exposure(session: Session) -> None:
    repo = _repo_with_cycle(session)
    _create_intent(repo, risk=None)
    repo.commit()

    exposures, has_unknown = repo.list_active_initial_risk_exposures(V2ExecutionMode.BINANCE_TESTNET)

    assert exposures == ()
    assert has_unknown is True


def test_filled_intent_and_projected_position_are_counted_once(session: Session) -> None:
    repo = _repo_with_cycle(session)
    _create_intent(repo, state=V2IntentState.FILLED)
    session.add(
        V2ManagedPosition(
            position_id="position-e004",
            intent_id="intent-e004",
            order_record_id="order-e004",
            symbol="BTC/USDT",
            direction="long",
            execution_mode=V2ExecutionMode.BINANCE_TESTNET.value,
            quantity=Decimal("0.01"),
            entry_price=Decimal("100000"),
            entry_fee=Decimal("0"),
            state=V2PositionState.PROTECTED.value,
            projected_at=datetime(2026, 8, 17, tzinfo=UTC),
        )
    )
    repo.commit()

    exposures, has_unknown = repo.list_active_initial_risk_exposures(V2ExecutionMode.BINANCE_TESTNET)

    assert has_unknown is False
    assert len(exposures) == 1
    assert exposures[0].source == "position"
    assert exposures[0].initial_risk_usdt == Decimal("35.50")


def test_terminal_position_releases_legacy_filled_intent_without_risk(session: Session) -> None:
    repo = _repo_with_cycle(session)
    _create_intent(repo, state=V2IntentState.FILLED, risk=None)
    session.add(
        V2ManagedPosition(
            position_id="closed-position-e004",
            intent_id="intent-e004",
            order_record_id="closed-order-e004",
            symbol="BTC/USDT",
            direction="long",
            execution_mode=V2ExecutionMode.BINANCE_TESTNET.value,
            quantity=Decimal("0.01"),
            entry_price=Decimal("100000"),
            entry_fee=Decimal("0"),
            state=V2PositionState.CLOSED.value,
            projected_at=datetime(2026, 8, 17, tzinfo=UTC),
            closed_at=datetime(2026, 8, 17, tzinfo=UTC),
        )
    )
    repo.commit()

    exposures, has_unknown = repo.list_active_initial_risk_exposures(V2ExecutionMode.BINANCE_TESTNET)

    assert exposures == ()
    assert has_unknown is False


def test_reduce_only_exit_intent_is_not_a_reservation(session: Session) -> None:
    repo = _repo_with_cycle(session)
    _create_intent(repo, risk=None)
    intent = session.get(V2ExecutionIntent, "intent-e004")
    assert intent is not None
    intent.candidate_key = "exit:position-e004:HARD_STOP"
    repo.commit()

    exposures, has_unknown = repo.list_active_initial_risk_exposures(V2ExecutionMode.BINANCE_TESTNET)

    assert exposures == ()
    assert has_unknown is False


def test_recovery_reconstructs_legacy_risk_from_authoritative_fill_and_stop(monkeypatch) -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(
        "services.automated_trading.application.fact_persistence.get_session_factory",
        lambda: factory,
    )
    session = factory()
    try:
        repo = _repo_with_cycle(session)
        _create_intent(repo, intent_id="legacy-recovery", risk=None)
        repo.commit()
    finally:
        session.close()

    persist_entry_and_protection(
        cycle_id="cycle-e004",
        decision_id=None,
        intent_id="legacy-recovery",
        symbol="BTC/USDT",
        direction="long",
        candidate_key="testnet_sampling_v2",
        candidate_type=V2CandidateType.SAMPLING,
        execution_mode=V2ExecutionMode.BINANCE_TESTNET,
        decision_bar_timestamp=datetime(2026, 8, 17, tzinfo=UTC),
        fencing_token="fence-e004",
        leverage=10,
        entry_result=EntryExecutionResult(
            status=EntryExecutionStatus.FILLED,
            intent_state=V2IntentState.FILLED,
            client_order_id="A2E-legacy-recovery",
            exchange_order_id="xo-legacy-recovery",
            trade_ids=("trade-legacy-recovery",),
            filled_quantity=Decimal("0.5"),
            average_fill_price=Decimal("100"),
            total_fee=Decimal("0"),
            fill_timestamp=datetime(2026, 8, 17, tzinfo=UTC),
        ),
        position_id="position-legacy-recovery",
        protection_result=None,
        stop_loss_price=Decimal("90"),
        take_profit_price=None,
        stop_client_order_id=None,
        tp_client_order_id=None,
    )

    session = factory()
    try:
        intent = session.get(V2ExecutionIntent, "legacy-recovery")
        assert intent is not None
        assert Decimal(str(intent.initial_risk_usdt)) == Decimal("5")
    finally:
        session.close()
        engine.dispose()
