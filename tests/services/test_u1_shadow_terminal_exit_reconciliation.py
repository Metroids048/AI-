"""U1 regression: SHADOW must still reconcile ALREADY-managed V2 exposure.

Root cause being locked down: ``request.persist_facts`` gated every repair path
(terminal exit projection, exit-gap recovery, protection retirement,
reconciliation persistence) while destructive local-ghost quarantine stayed
enabled.  A BTC position opened under ACTIVE kept live exchange protection, the
engine was downgraded to SHADOW, its native stop filled, and the local
projection was quarantined instead of closed -- permanently unreconciled.

Activation controls NEW execution authority.  It must never disable
reconciliation of exposure already created while ACTIVE.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from services.automated_trading.application.cycle_service import (
    CycleRequest,
    CycleResult,
    _project_confirmed_protection_exits,
    run_automated_trading_cycle,
)
from services.automated_trading.application.decision_service import BarView, TimeframeView
from services.automated_trading.application.exit_service import ExitReason
from services.automated_trading.application.fact_persistence import (
    persist_entry_and_protection,
)
from services.automated_trading.application.reconciliation_service import LocalStateView
from services.automated_trading.application.recovery_service import RecoveryActionType
from services.automated_trading.domain.enums import (
    V2CandidateType,
    V2ExecutionMode,
    V2IntentState,
    V2PositionState,
)
from services.automated_trading.infrastructure.binance_adapter import ExchangeFillReceipt
from services.automated_trading.infrastructure.market_snapshot_provider import (
    AuthoritativeAccountSnapshot,
)
from services.automated_trading.infrastructure.models import (
    V2ExchangeFill,
    V2ManagedPosition,
    V2ProtectionRecord,
)
from services.automated_trading.infrastructure.runtime_lock import EngineActivation
from services.database import get_session_factory

NOW = datetime(2026, 8, 9, 2, 47, 8, 700000, tzinfo=UTC)
ENTRY_AT = NOW - timedelta(hours=16)

# Real U1 numbers, so the regression carries the incident's own arithmetic.
QTY = Decimal("0.0388")
ENTRY_PRICE = Decimal("64996.2")
EXIT_PRICE = Decimal("64768.6")
ENTRY_FEE = Decimal("1.00874102")
EXIT_FEE = Decimal("1.00520867")
GROSS_PNL = Decimal("-8.83088000")
NET_PNL = Decimal("-10.84482969")

STOP_ALGO_ID = "1000000160188648"
TP_ALGO_ID = "1000000160188659"
EXIT_ORDER_ID = "28533281387"
EXIT_TRADE_ID = "525914324"


def _empty_timeframe() -> TimeframeView:
    price = Decimal("64996.2")
    return TimeframeView(
        timeframe="15m",
        bars=(
            BarView(
                timestamp=ENTRY_AT,
                open=price,
                high=price,
                low=price,
                close=price,
                volume=Decimal("1"),
            ),
        ),
    )


def _request(*, activation: EngineActivation, persist_facts: bool, cycle_id: str) -> CycleRequest:
    return CycleRequest(
        cycle_id=cycle_id,
        symbol="BTC/USDT",
        timeframe="15m",
        entry_timeframe=_empty_timeframe(),
        execution_mode=V2ExecutionMode.BINANCE_TESTNET,
        engine_activation=activation,
        fencing_token="btcusdt@BINANCE_TESTNET@u1@20260809@abcd1234",
        now=NOW,
        persist_facts=persist_facts,
    )


def _flat_snapshot() -> AuthoritativeAccountSnapshot:
    """Exchange is flat: the native protection leg already closed the position."""
    return AuthoritativeAccountSnapshot(
        balance=Decimal("10000"),
        equity=Decimal("10000"),
        positions=[],
        pending_orders=[],
        snapshot_timestamp=NOW,
    )


def _seed_managed_position(position_id: str, *, suffix: str) -> None:
    """Create a real ACTIVE-era managed position with live protection, then quarantine it."""
    persist_entry_and_protection(
        cycle_id=f"cycle-entry-{suffix}",
        decision_id=f"decision-entry-{suffix}",
        intent_id=f"intent-entry-{suffix}",
        symbol="BTC/USDT",
        direction="long",
        candidate_key="testnet_sampling_v2",
        candidate_type=V2CandidateType.SAMPLING,
        execution_mode=V2ExecutionMode.BINANCE_TESTNET,
        decision_bar_timestamp=ENTRY_AT,
        fencing_token=f"fence-entry-{suffix}",
        leverage=40,
        entry_result=SimpleNamespace(
            status=SimpleNamespace(value="FILLED"),
            intent_state=V2IntentState.FILLED,
            client_order_id=f"A2E-{suffix}",
            exchange_order_id=f"28531748285-{suffix}",
            trade_ids=(f"525715723-{suffix}",),
            requested_quantity=QTY,
            filled_quantity=QTY,
            average_fill_price=ENTRY_PRICE,
            total_fee=ENTRY_FEE,
            fill_timestamp=ENTRY_AT,
            position_projectable=True,
        ),
        position_id=position_id,
        protection_result=SimpleNamespace(
            is_active=True,
            stop_exchange_order_id=f"{STOP_ALGO_ID}-{suffix}",
            tp_exchange_order_id=f"{TP_ALGO_ID}-{suffix}",
        ),
        stop_loss_price=Decimal("64768.9"),
        take_profit_price=Decimal("65337.2"),
        stop_client_order_id=f"A2S-{suffix}",
        tp_client_order_id=f"A2T-{suffix}",
    )
    # Reproduce the incident: destructive recovery already quarantined it while
    # every repair path was gated off.
    with get_session_factory()() as session:
        position = session.get(V2ManagedPosition, position_id)
        assert position is not None
        position.state = V2PositionState.QUARANTINED.value
        position.version += 1
        session.commit()


def _leg_adapter(*, filled_leg: str, suffix: str) -> MagicMock:
    """Adapter where exactly one protection leg has an authoritative fill.

    ``fetch_fills`` receives the ALGO id and resolves it to the real execution
    order id, mirroring ``fapiPrivateGetAlgoOrder.actualOrderId``.  The sibling
    leg returns no fills at all: Binance purges the OCO-cancelled leg.
    """
    adapter = MagicMock()
    filled_ref = f"{filled_leg}-{suffix}"

    def fetch_fills(_symbol: str, order_id: str):
        if order_id != filled_ref:
            return ()
        return (
            ExchangeFillReceipt(
                exchange_order_id=EXIT_ORDER_ID,
                trade_id=EXIT_TRADE_ID,
                filled_quantity=QTY,
                fill_price=EXIT_PRICE,
                fee=EXIT_FEE,
                fill_timestamp=NOW,
            ),
        )

    adapter.fetch_fills.side_effect = fetch_fills
    return adapter


def _assert_reconciled(position_id: str, *, expect_protection: str) -> None:
    with get_session_factory()() as session:
        position = session.get(V2ManagedPosition, position_id)
        assert position is not None

        # A7 / A8: closed, stamped with the EXCHANGE fill time (not "now").
        assert position.state == V2PositionState.CLOSED.value
        assert position.closed_at is not None
        assert position.closed_at.replace(tzinfo=UTC) == NOW

        # A3-A6: exactly one real reduce-only exit fill, exchange values verbatim.
        fills = session.query(V2ExchangeFill).filter_by(trade_id=EXIT_TRADE_ID).all()
        assert len(fills) == 1
        fill = fills[0]
        assert fill.exchange_order_id == EXIT_ORDER_ID
        # sqlite stores these as floats, so compare within storage epsilon.
        assert Decimal(str(fill.filled_quantity)) == pytest.approx(QTY, abs=Decimal("0.00000001"))
        assert Decimal(str(fill.fill_price)) == pytest.approx(EXIT_PRICE, abs=Decimal("0.0001"))
        assert Decimal(str(fill.commission)) == pytest.approx(EXIT_FEE, abs=Decimal("0.00000001"))
        assert bool(fill.reduce_only) is True

        # A11: dead protection is no longer claiming to guard anything.
        protection = session.query(V2ProtectionRecord).filter_by(position_id=position_id).one()
        assert protection.state == expect_protection
        assert protection.state != "PROTECTION_ACTIVE"

        # A9 / A10: realized_pnl follows the project's single existing contract
        # (fact_persistence gross - entry_fee - exit_fee).  Fees stay on the real
        # fill rows, so net is derivable and never double-charged.
        realized = Decimal(str(position.realized_pnl))
        assert realized == pytest.approx(NET_PNL, abs=Decimal("0.0001"))
        derived_gross = realized + Decimal(str(position.entry_fee)) + Decimal(str(fill.commission))
        assert derived_gross == pytest.approx(GROSS_PNL, abs=Decimal("0.0001"))


def test_shadow_reconciles_native_stop_loss_terminal_exit() -> None:
    """Case 1: ACTIVE-era position + SHADOW engine + native SL fill -> CLOSED."""
    position_id = "pos-u1-shadow-sl"
    _seed_managed_position(position_id, suffix="u1sl")
    adapter = _leg_adapter(filled_leg=STOP_ALGO_ID, suffix="u1sl")
    request = _request(
        activation=EngineActivation.SHADOW,
        persist_facts=False,
        cycle_id="cycle-u1-shadow-sl",
    )
    result = CycleResult(cycle_id=request.cycle_id, symbol=request.symbol)

    # The gate that caused U1: SHADOW must still be allowed to converge
    # already-managed exposure.
    assert request.reconcile_existing_positions is True
    assert request.persist_facts is False

    changed = _project_confirmed_protection_exits(
        request,
        adapter,
        _flat_snapshot(),
        LocalStateView(),
        result,
    )

    assert changed is True
    assert result.exit_submitted is True
    _assert_reconciled(position_id, expect_protection="PROTECTION_FILLED")

    # SHADOW gains no new execution authority from this fix.
    adapter.submit_market_order.assert_not_called()
    adapter.submit_protection.assert_not_called()


def test_shadow_reconciles_native_take_profit_terminal_exit() -> None:
    """Case 2: same contract for a native TP terminal, sibling SL purged."""
    position_id = "pos-u1-shadow-tp"
    _seed_managed_position(position_id, suffix="u1tp")
    adapter = _leg_adapter(filled_leg=TP_ALGO_ID, suffix="u1tp")
    request = _request(
        activation=EngineActivation.SHADOW,
        persist_facts=False,
        cycle_id="cycle-u1-shadow-tp",
    )
    result = CycleResult(cycle_id=request.cycle_id, symbol=request.symbol)

    changed = _project_confirmed_protection_exits(
        request,
        adapter,
        _flat_snapshot(),
        LocalStateView(),
        result,
    )

    assert changed is True
    _assert_reconciled(position_id, expect_protection="PROTECTION_FILLED")
    adapter.submit_market_order.assert_not_called()
    adapter.submit_protection.assert_not_called()


def test_algo_order_id_is_resolved_to_actual_execution_order_id() -> None:
    """Case 3: the algo id is never the executed order id.

    ``fetch_order(algo_id)`` answers -2013 Order does not exist even while the
    protection is healthy, so a -2013 must never be read as protection failure.
    Reconciliation must key off the resolved execution order instead.
    """
    position_id = "pos-u1-algo-resolution"
    _seed_managed_position(position_id, suffix="u1algo")
    adapter = _leg_adapter(filled_leg=STOP_ALGO_ID, suffix="u1algo")
    request = _request(
        activation=EngineActivation.SHADOW,
        persist_facts=False,
        cycle_id="cycle-u1-algo-resolution",
    )
    result = CycleResult(cycle_id=request.cycle_id, symbol=request.symbol)

    _project_confirmed_protection_exits(
        request,
        adapter,
        _flat_snapshot(),
        LocalStateView(),
        result,
    )

    # Lookup went out on the ALGO id ...
    looked_up = {call.args[1] for call in adapter.fetch_fills.call_args_list}
    assert f"{STOP_ALGO_ID}-u1algo" in looked_up
    # ... and what got persisted is the ACTUAL execution order id.
    with get_session_factory()() as session:
        fill = session.query(V2ExchangeFill).filter_by(trade_id=EXIT_TRADE_ID).one()
        assert fill.exchange_order_id == EXIT_ORDER_ID
        assert fill.exchange_order_id != f"{STOP_ALGO_ID}-u1algo"
    # No plain fetch_order(algo_id) was used to judge protection health.
    adapter.fetch_order.assert_not_called()


def test_oco_sibling_disappearance_is_a_normal_terminal_state() -> None:
    """Case 4: sibling gone + brother filled + exchange flat = normal terminal."""
    position_id = "pos-u1-oco-sibling"
    _seed_managed_position(position_id, suffix="u1oco")
    adapter = _leg_adapter(filled_leg=STOP_ALGO_ID, suffix="u1oco")
    # Binance purges the cancelled sibling from queryable history.
    adapter.query_order_by_client_id.return_value = None
    request = _request(
        activation=EngineActivation.SHADOW,
        persist_facts=False,
        cycle_id="cycle-u1-oco-sibling",
    )
    result = CycleResult(cycle_id=request.cycle_id, symbol=request.symbol)

    changed = _project_confirmed_protection_exits(
        request,
        adapter,
        _flat_snapshot(),
        LocalStateView(),
        result,
    )

    # The vanished sibling must not be reported as a protection failure ...
    assert changed is True
    assert not [error for error in result.errors if "protection" in error.lower()]
    # ... and it must not leave the position quarantined.
    _assert_reconciled(position_id, expect_protection="PROTECTION_FILLED")
    with get_session_factory()() as session:
        position = session.get(V2ManagedPosition, position_id)
        assert position is not None
        assert position.state != V2PositionState.QUARANTINED.value


def test_repeated_reconciliation_of_the_same_exit_is_idempotent() -> None:
    """Case 5: running the same real reconciliation twice adds nothing.

    The failure this guards against is re-recording the same loss on every
    subsequent cycle.
    """
    position_id = "pos-u1-idempotent"
    _seed_managed_position(position_id, suffix="u1idem")
    adapter = _leg_adapter(filled_leg=STOP_ALGO_ID, suffix="u1idem")
    request = _request(
        activation=EngineActivation.SHADOW,
        persist_facts=False,
        cycle_id="cycle-u1-idempotent",
    )

    first = CycleResult(cycle_id=request.cycle_id, symbol=request.symbol)
    assert _project_confirmed_protection_exits(request, adapter, _flat_snapshot(), LocalStateView(), first) is True
    _assert_reconciled(position_id, expect_protection="PROTECTION_FILLED")

    with get_session_factory()() as session:
        position = session.get(V2ManagedPosition, position_id)
        assert position is not None
        after_first = {
            "state": position.state,
            "closed_at": position.closed_at,
            "realized_pnl": Decimal(str(position.realized_pnl)),
            "version": position.version,
            "fills": session.query(V2ExchangeFill).filter_by(trade_id=EXIT_TRADE_ID).count(),
            "all_fills": session.query(V2ExchangeFill).count(),
        }

    second = CycleResult(cycle_id="cycle-u1-idempotent-2", symbol=request.symbol)
    _project_confirmed_protection_exits(
        _request(
            activation=EngineActivation.SHADOW,
            persist_facts=False,
            cycle_id="cycle-u1-idempotent-2",
        ),
        adapter,
        _flat_snapshot(),
        LocalStateView(),
        second,
    )

    with get_session_factory()() as session:
        position = session.get(V2ManagedPosition, position_id)
        assert position is not None
        # 0 new fills, 0 second close, 0 duplicate PnL, no further mutation.
        assert session.query(V2ExchangeFill).filter_by(trade_id=EXIT_TRADE_ID).count() == after_first["fills"]
        assert session.query(V2ExchangeFill).count() == after_first["all_fills"]
        assert position.state == after_first["state"]
        assert position.closed_at == after_first["closed_at"]
        assert Decimal(str(position.realized_pnl)) == after_first["realized_pnl"]
        assert position.version == after_first["version"]
        protection = session.query(V2ProtectionRecord).filter_by(position_id=position_id).one()
        assert protection.state == "PROTECTION_FILLED"


def test_quarantine_is_deferred_when_terminal_exit_projection_is_incomplete() -> None:
    """A failed exchange lookup must not let ghost quarantine freeze the row.

    Without this, an unread authoritative fill is misread as "local position has
    no exchange counterpart" and the position is quarantined out of
    ``get_open_positions`` -- exactly how U1 became permanent.
    """
    position_id = "pos-u1-deferred-quarantine"
    _seed_managed_position(position_id, suffix="u1defer")
    adapter = MagicMock()
    adapter.fetch_fills.side_effect = RuntimeError("binance unavailable")
    request = _request(
        activation=EngineActivation.SHADOW,
        persist_facts=False,
        cycle_id="cycle-u1-deferred-quarantine",
    )
    result = CycleResult(cycle_id=request.cycle_id, symbol=request.symbol)

    changed = _project_confirmed_protection_exits(
        request,
        adapter,
        _flat_snapshot(),
        LocalStateView(),
        result,
    )

    assert changed is False
    assert result.terminal_exit_projection_incomplete is True
    assert any("protection fill lookup failed" in error for error in result.errors)


def test_active_lane_keeps_full_persistence_authority() -> None:
    """ACTIVE must keep both authorities; the fix only widened reconciliation."""
    active = _request(
        activation=EngineActivation.ACTIVE,
        persist_facts=True,
        cycle_id="cycle-u1-active",
    )
    assert active.persist_facts is True
    assert active.reconcile_existing_positions is True

    # Offline / unit rehearsal keeps writing nothing at all.
    offline = _request(
        activation=EngineActivation.ACTIVE,
        persist_facts=False,
        cycle_id="cycle-u1-offline",
    )
    assert offline.reconcile_existing_positions is False


def test_reconciled_sampling_exit_keeps_sampling_attribution() -> None:
    """A repaired sampling exit stays in the sampling lane.

    The BTC position behind U1 came from ``testnet_sampling_v2``.  Reconciling
    its loss must not let it be attributed to the primary candidate.
    """
    from services.automated_trading.infrastructure.models import V2ExecutionIntent

    position_id = "pos-u1-attribution"
    _seed_managed_position(position_id, suffix="u1attr")
    adapter = _leg_adapter(filled_leg=STOP_ALGO_ID, suffix="u1attr")
    request = _request(
        activation=EngineActivation.SHADOW,
        persist_facts=False,
        cycle_id="cycle-u1-attribution",
    )
    result = CycleResult(cycle_id=request.cycle_id, symbol=request.symbol)

    _project_confirmed_protection_exits(request, adapter, _flat_snapshot(), LocalStateView(), result)

    with get_session_factory()() as session:
        position = session.get(V2ManagedPosition, position_id)
        assert position is not None
        entry_intent = session.get(V2ExecutionIntent, position.intent_id)
        assert entry_intent is not None
        # Entry attribution untouched by the repair.
        assert entry_intent.candidate_key == "testnet_sampling_v2"
        assert entry_intent.candidate_type == V2CandidateType.SAMPLING.value
        # The exit intent inherits the sampling lane, never a primary key.
        exit_intents = (
            session.query(V2ExecutionIntent).filter(V2ExecutionIntent.candidate_key.like(f"exit:{position_id}:%")).all()
        )
        assert exit_intents
        for intent in exit_intents:
            assert intent.candidate_type == V2CandidateType.SAMPLING.value
            assert not intent.candidate_key.startswith("operator_heuristic")


@patch("services.automated_trading.application.cycle_service.execute_recovery_actions")
@patch("services.automated_trading.application.cycle_service._build_local_state")
def test_full_cycle_defers_ghost_quarantine_when_projection_could_not_finish(
    mock_local_state,
    mock_recovery,
) -> None:
    """End-to-end: a failed terminal-exit lookup must not reach quarantine.

    This is the ordering guarantee that made U1 permanent: destructive recovery
    ran in the same cycle that repair had been skipped in.
    """
    from services.automated_trading.application.reconciliation_service import LocalPositionView

    local_state = LocalStateView(
        positions=(
            LocalPositionView(
                position_id="pos-u1-cycle-defer",
                symbol="BTC/USDT",
                direction="long",
                quantity=QTY,
                state="PROTECTED",
                claim_keys=frozenset(),
                has_active_protection=True,
                protection_exchange_order_ids=frozenset({STOP_ALGO_ID}),
                protection_order_refs=((STOP_ALGO_ID, "A2S-cycle-defer", ExitReason.HARD_STOP.value),),
            ),
        )
    )
    mock_local_state.return_value = SimpleNamespace(
        status="OK",
        state=local_state,
        error_code=None,
        exchange_position_claim_refs={},
    )

    adapter = MagicMock()
    # Exchange is flat (the stop already fired) but the fill lookup is down.
    adapter.fetch_authoritative_snapshot.return_value = _flat_snapshot()
    adapter.fetch_fills.side_effect = RuntimeError("binance unavailable")
    adapter.fetch_market_snapshot.return_value = MagicMock(
        current_price=ENTRY_PRICE,
        atr=Decimal("0"),
        tick_size=Decimal("0.1"),
        step_size=Decimal("0.001"),
        min_notional=Decimal("5"),
    )

    result = run_automated_trading_cycle(
        _request(
            activation=EngineActivation.SHADOW,
            persist_facts=False,
            cycle_id="cycle-u1-cycle-defer",
        ),
        adapter,
    )

    assert result.terminal_exit_projection_incomplete is True
    quarantined = [
        action
        for call in mock_recovery.call_args_list
        for action in call.args[0]
        if action.action_type is RecoveryActionType.QUARANTINE_LOCAL_GHOST_POSITION
    ]
    assert quarantined == []
