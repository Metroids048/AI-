"""Task 10: V2 Cycle Service — orchestrated Entry→Protection in Strict Fake.

Uses the same Strict Fake exchange pattern the plan requires: a fake adapter
that only accepts valid V2 submissions and refuses any legacy paper call.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from services.automated_trading.application.cycle_service import (
    CycleRequest,
    CycleResult,
    LocalStateLoadResult,
    _calculate_quantity,
    _project_confirmed_protection_exits,
    _recover_confirmed_v2_exit_gaps,
    run_automated_trading_cycle,
)
from services.automated_trading.application.decision_service import BarView, TimeframeView
from services.automated_trading.application.exit_service import (
    ExitDecision,
    ExitExecutionResult,
    ExitExecutionStatus,
    ExitReason,
    ExitVerdict,
)
from services.automated_trading.application.fact_persistence import (
    persist_entry_and_protection,
)
from services.automated_trading.application.reconciliation_service import (
    LocalPositionView,
    LocalStateView,
)
from services.automated_trading.application.recovery_service import RecoveryActionType
from services.automated_trading.domain.client_order_id import exit_client_order_id
from services.automated_trading.domain.enums import (
    V2CandidateType,
    V2ExecutionMode,
    V2IntentState,
    V2PositionState,
)
from services.automated_trading.infrastructure.binance_adapter import (
    ExchangeFillReceipt,
    ExchangeOrderReceipt,
)
from services.automated_trading.infrastructure.market_snapshot_provider import (
    AuthoritativeAccountSnapshot,
    ExchangePositionSnapshot,
)
from services.automated_trading.infrastructure.models import (
    V2ExchangeFill,
    V2ManagedPosition,
    V2ProtectionRecord,
)
from services.automated_trading.infrastructure.runtime_lock import EngineActivation
from services.database import get_session_factory

BAR_START = datetime(2026, 7, 27, 0, 0, tzinfo=UTC)
# 50 bars (0-49). Last bar closes at BAR_START + 49*15min.
# CYCLE_NOW must be within data_stale_after_seconds=120 of the last bar.
LAST_BAR_TS = BAR_START + timedelta(minutes=15 * 49)
CYCLE_NOW = LAST_BAR_TS + timedelta(seconds=10)

LONG_CLOSES = [100.0] * 30 + [
    100.3,
    100.1,
    100.4,
    100.2,
    100.5,
    100.3,
    100.6,
    100.4,
    100.7,
    100.5,
    100.8,
    100.6,
    100.9,
    100.7,
    101.0,
    100.8,
    101.1,
    100.9,
    101.2,
    101.0,
]


def build_timeframe(closes: list[float]) -> TimeframeView:
    bars = []
    for i, c in enumerate(closes):
        p = Decimal(str(c))
        bars.append(
            BarView(
                timestamp=BAR_START + timedelta(minutes=15 * i),
                open=p,
                high=p * Decimal("1.003"),
                low=p * Decimal("0.997"),
                close=p,
                volume=Decimal("100"),
            )
        )
    return TimeframeView(timeframe="15m", bars=tuple(bars))


def _snapshot(positions=None) -> AuthoritativeAccountSnapshot:
    return AuthoritativeAccountSnapshot(
        balance=Decimal("10000"),
        equity=Decimal("10000"),
        positions=positions or [],
        pending_orders=[],
        snapshot_timestamp=CYCLE_NOW,
    )


def _order_receipt(oid: str) -> ExchangeOrderReceipt:
    return ExchangeOrderReceipt(
        exchange_order_id=oid,
        client_order_id="A2E-abc",
        symbol="BTC/USDT",
        side="buy",
        order_type="market",
        quantity=Decimal("0.001"),
        price=None,
        status="filled",
        acknowledged_at=CYCLE_NOW,
    )


def _fill(oid: str) -> ExchangeFillReceipt:
    return ExchangeFillReceipt(
        exchange_order_id=oid,
        trade_id="trade-1",
        filled_quantity=Decimal("0.001"),
        fill_price=Decimal("101.0"),
        fee=Decimal("0.001"),
        fill_timestamp=CYCLE_NOW,
    )


def _protection_receipt(oid: str) -> ExchangeOrderReceipt:
    return ExchangeOrderReceipt(
        exchange_order_id=oid,
        client_order_id="A2S-abc",
        symbol="BTC/USDT",
        side="sell",
        order_type="stop_market",
        quantity=Decimal("0.001"),
        price=Decimal("95"),
        status="new",
        acknowledged_at=CYCLE_NOW,
    )


def build_request(**overrides) -> CycleRequest:
    defaults: dict = {
        "cycle_id": "cycle-test-1",
        "symbol": "BTC/USDT",
        "timeframe": "15m",
        "entry_timeframe": build_timeframe(LONG_CLOSES),
        "execution_mode": V2ExecutionMode.BINANCE_TESTNET,
        "engine_activation": EngineActivation.ACTIVE,
        "fencing_token": "btcusdt@BINANCE_TESTNET@default@20260728@abc12345",
        "now": CYCLE_NOW,
        "sampling_fallback_enabled": True,
    }
    defaults.update(overrides)
    return CycleRequest(**defaults)


def build_adapter_with_successful_cycle() -> MagicMock:
    adapter = MagicMock()
    adapter.fetch_authoritative_snapshot.return_value = _snapshot()
    adapter.fetch_market_snapshot.return_value = MagicMock(
        current_price=Decimal("101.0"),
        atr=Decimal("0"),
        tick_size=Decimal("0.01"),
        step_size=Decimal("0.001"),
        min_notional=Decimal("5"),
    )
    adapter.submit_market_order.return_value = _order_receipt("ex-entry-1")
    adapter.fetch_fills.return_value = (_fill("ex-entry-1"),)
    adapter.submit_protection.return_value = (_protection_receipt("ex-stop-1"), None)
    return adapter


def test_shadow_mode_never_submits_to_exchange() -> None:
    """Gate 10: SHADOW runs the full funnel but never calls submit."""
    adapter = build_adapter_with_successful_cycle()
    request = build_request(engine_activation=EngineActivation.SHADOW)

    result = run_automated_trading_cycle(request, adapter)

    adapter.submit_market_order.assert_not_called()
    adapter.submit_protection.assert_not_called()
    assert not result.entry_submitted


def test_active_sampling_candidate_is_decision_trace_only() -> None:
    """S-105: non-promotable sampling must not obtain position authority."""
    adapter = build_adapter_with_successful_cycle()
    result = run_automated_trading_cycle(build_request(), adapter)

    adapter.submit_market_order.assert_not_called()
    adapter.submit_protection.assert_not_called()
    assert not result.entry_submitted
    assert not result.position_projected
    assert not result.protection_active
    assert result.funnel_payload["execution_policy"] == "DECISION_TRACE_ONLY"


def test_sampling_fallback_is_disabled_by_default() -> None:
    """S-101: an absent operator opt-in must not evaluate the sampling lane."""
    adapter = build_adapter_with_successful_cycle()

    result = run_automated_trading_cycle(build_request(sampling_fallback_enabled=False), adapter)

    adapter.submit_market_order.assert_not_called()
    assert result.funnel_payload["execution_policy"] == "SAMPLING_DISABLED"


def test_risk_sizing_has_no_price_linked_sampling_floor() -> None:
    """S-104: V2 risk sizing is a notional, never reference_price * 0.0015."""
    request = build_request(risk_per_trade=Decimal("0.0015"))

    btc_notional = _calculate_quantity(request, _snapshot())
    eth_notional = _calculate_quantity(build_request(symbol="ETH/USDT", risk_per_trade=Decimal("0.0015")), _snapshot())

    assert btc_notional == Decimal("15")
    assert eth_notional == Decimal("15")


def test_operator_fixed_notional_has_precedence_then_respects_exposure_cap() -> None:
    """S-202: order_notional_usdt is a notional, not a margin or fallback hint."""
    request = build_request(
        risk_per_trade=Decimal("0.05"),
        order_notional_usdt=Decimal("123"),
        max_position_fraction=Decimal("0.11"),
    )

    assert _calculate_quantity(request, _snapshot()) == Decimal("123")
    assert _calculate_quantity(
        build_request(order_notional_usdt=Decimal("2000"), max_position_fraction=Decimal("0.11")),
        _snapshot(),
    ) == Decimal("1100")


def test_flat_market_never_enters() -> None:
    adapter = build_adapter_with_successful_cycle()
    flat_closes = [100.0] * 50
    request = build_request(entry_timeframe=build_timeframe(flat_closes))

    result = run_automated_trading_cycle(request, adapter)

    adapter.submit_market_order.assert_not_called()
    assert not result.entry_submitted
    assert result.funnel_payload


def test_unhealthy_snapshot_fetch_returns_unavailable() -> None:
    adapter = MagicMock()
    adapter.fetch_authoritative_snapshot.side_effect = Exception("gateway down")

    result = run_automated_trading_cycle(build_request(), adapter)

    assert result.reconciliation_status.value == "UNAVAILABLE"
    assert result.errors


def test_degraded_reconciliation_is_persisted_and_executes_external_quarantine(monkeypatch) -> None:
    from services.automated_trading.application import cycle_service

    external_position = ExchangePositionSnapshot(
        symbol="BTC/USDT",
        direction="short",
        quantity=Decimal("0.0324"),
        entry_price=Decimal("118000"),
        mark_price=Decimal("119000"),
        unrealized_pnl=Decimal("-32.4"),
        leverage=None,
    )
    adapter = build_adapter_with_successful_cycle()
    adapter.fetch_authoritative_snapshot.return_value = _snapshot([external_position])
    persist_reconciliation = MagicMock()
    execute_recovery = MagicMock(return_value=SimpleNamespace(errors=()))
    monkeypatch.setattr(
        cycle_service,
        "_build_local_state",
        lambda *_args, **_kwargs: LocalStateLoadResult(status="OK", state=LocalStateView()),
    )
    monkeypatch.setattr(cycle_service, "_runtime_entry_enabled", lambda: False)
    monkeypatch.setattr(cycle_service, "_persist_reconciliation_fact", persist_reconciliation, raising=False)
    monkeypatch.setattr(cycle_service, "execute_recovery_actions", execute_recovery)

    result = run_automated_trading_cycle(build_request(persist_facts=True), adapter)

    assert result.reconciliation_status.value == "DEGRADED"
    persist_reconciliation.assert_called_once()
    actions = execute_recovery.call_args.args[0]
    assert any(action.action_type is RecoveryActionType.QUARANTINE_EXTERNAL_POSITION for action in actions)


def test_failed_entry_never_projects_position() -> None:
    adapter = build_adapter_with_successful_cycle()
    from services.automated_trading.infrastructure.binance_adapter import BinanceAdapterUnavailable

    adapter.submit_market_order.side_effect = BinanceAdapterUnavailable("rejected")

    result = run_automated_trading_cycle(build_request(), adapter)

    assert not result.position_projected
    assert not result.protection_active


def test_funnel_payload_is_always_returned() -> None:
    adapter = build_adapter_with_successful_cycle()

    result = run_automated_trading_cycle(build_request(), adapter)

    assert isinstance(result.funnel_payload, dict)
    assert "symbol" in result.funnel_payload


def test_disabled_engine_blocks_entry() -> None:
    adapter = build_adapter_with_successful_cycle()
    request = build_request(engine_activation=EngineActivation.DISABLED)

    result = run_automated_trading_cycle(request, adapter)

    adapter.submit_market_order.assert_not_called()
    assert not result.entry_submitted


def _open_local_position() -> LocalStateView:
    return LocalStateView(
        positions=(
            LocalPositionView(
                position_id="pos-exit-cycle-1",
                symbol="BTC/USDT",
                direction="long",
                quantity=Decimal("0.01"),
                state="PROTECTED",
                claim_keys=frozenset({"pos-exit-cycle-1"}),
                has_active_protection=True,
            ),
        )
    )


def _exchange_position(quantity: str = "0.01") -> ExchangePositionSnapshot:
    return ExchangePositionSnapshot(
        symbol="BTC/USDT",
        direction="long",
        quantity=Decimal(quantity),
        entry_price=Decimal("50000"),
        mark_price=Decimal("50000"),
        unrealized_pnl=Decimal("0"),
        leverage=10,
    )


@patch("services.automated_trading.application.cycle_service._build_local_state")
def test_approved_exit_invokes_execute_reduce_only_exit(mock_local_state) -> None:
    """Gate 3: cycle must execute reduce-only exits, not only evaluate them."""
    mock_local_state.return_value = LocalStateLoadResult(status="OK", state=_open_local_position())
    adapter = build_adapter_with_successful_cycle()
    adapter.fetch_authoritative_snapshot.return_value = _snapshot(positions=[_exchange_position()])
    adapter.submit_reduce_only_exit.return_value = ExchangeOrderReceipt(
        exchange_order_id="ex-exit-1",
        client_order_id="A2X-exit",
        symbol="BTC/USDT",
        side="sell",
        order_type="market",
        quantity=Decimal("0.01"),
        price=None,
        status="filled",
        acknowledged_at=CYCLE_NOW,
    )
    adapter.fetch_fills.return_value = (
        ExchangeFillReceipt(
            exchange_order_id="ex-exit-1",
            trade_id="trade-exit-1",
            filled_quantity=Decimal("0.01"),
            fill_price=Decimal("50000"),
            fee=Decimal("0.2"),
            fill_timestamp=CYCLE_NOW,
        ),
    )

    result = run_automated_trading_cycle(
        build_request(forced_exit_reason=ExitReason.TIME_EXIT),
        adapter,
    )

    adapter.submit_reduce_only_exit.assert_called_once()
    assert result.exit_submitted


@patch("services.automated_trading.application.cycle_service.execute_reduce_only_exit")
@patch("services.automated_trading.application.cycle_service._build_local_state")
def test_cycle_calls_execute_reduce_only_exit_on_approved_verdict(
    mock_local_state,
    mock_execute_exit,
) -> None:
    mock_local_state.return_value = LocalStateLoadResult(status="OK", state=_open_local_position())
    mock_execute_exit.return_value = ExitExecutionResult(
        status=ExitExecutionStatus.CLOSED,
        position_state=V2PositionState.CLOSED,
        client_order_id="A2X-exit",
        exchange_order_id="ex-exit-1",
    )
    adapter = build_adapter_with_successful_cycle()
    adapter.fetch_authoritative_snapshot.return_value = _snapshot(positions=[_exchange_position()])

    result = run_automated_trading_cycle(
        build_request(forced_exit_reason=ExitReason.TIME_EXIT),
        adapter,
    )

    mock_execute_exit.assert_called_once()
    decision = mock_execute_exit.call_args.args[0]
    assert isinstance(decision, ExitDecision)
    assert decision.verdict is ExitVerdict.APPROVED
    assert decision.reason is ExitReason.TIME_EXIT
    assert result.exit_submitted


@patch("services.automated_trading.application.cycle_service._record_trade_review_skip")
@patch("services.automated_trading.application.cycle_service._persist_reconciliation_fact")
@patch("services.automated_trading.application.cycle_service.persist_exit_submission_result")
@patch("services.automated_trading.application.cycle_service.persist_exit_intent_before_submission")
@patch("services.automated_trading.application.cycle_service.execute_reduce_only_exit")
@patch("services.automated_trading.application.cycle_service._build_local_state")
def test_cycle_persists_exit_identity_before_submit_and_ack_after_submit(
    mock_local_state,
    mock_execute_exit,
    mock_persist_before,
    mock_persist_submission,
    _mock_persist_reconciliation,
    _mock_review_skip,
) -> None:
    mock_local_state.return_value = LocalStateLoadResult(
        status="OK",
        state=_open_local_position(),
    )
    mock_execute_exit.return_value = ExitExecutionResult(
        status=ExitExecutionStatus.SUBMITTED_UNCONFIRMED,
        position_state=V2PositionState.REDUCING,
        client_order_id="A2X-exit-ack-1",
        exchange_order_id="ex-exit-ack-1",
    )
    adapter = build_adapter_with_successful_cycle()
    adapter.fetch_authoritative_snapshot.return_value = _snapshot(positions=[_exchange_position()])

    result = run_automated_trading_cycle(
        build_request(
            forced_exit_reason=ExitReason.TIME_EXIT,
            persist_facts=True,
        ),
        adapter,
    )

    mock_persist_before.assert_called_once()
    mock_execute_exit.assert_called_once()
    mock_persist_submission.assert_called_once_with(
        position_id="pos-exit-cycle-1",
        result=mock_execute_exit.return_value,
    )
    assert result.exit_submitted


def test_cycle_recovery_projects_exact_delayed_exit_fill_before_ghost_quarantine() -> None:
    position_id = "pos-delayed-exit-recovery"
    persist_entry_and_protection(
        cycle_id="cycle-entry-delayed-exit",
        decision_id="decision-entry-delayed-exit",
        intent_id="intent-entry-delayed-exit",
        symbol="ETH/USDT",
        direction="long",
        candidate_key="testnet_sampling_v2",
        candidate_type=V2CandidateType.SAMPLING,
        execution_mode=V2ExecutionMode.BINANCE_TESTNET,
        decision_bar_timestamp=CYCLE_NOW - timedelta(minutes=15),
        fencing_token="fence-entry-delayed-exit",
        leverage=40,
        entry_result=SimpleNamespace(
            status=SimpleNamespace(value="FILLED"),
            intent_state=V2IntentState.FILLED,
            client_order_id="A2E-delayed-exit",
            exchange_order_id="xo-entry-delayed-exit",
            trade_ids=("trade-entry-delayed-exit",),
            requested_quantity=Decimal("0.164"),
            filled_quantity=Decimal("0.164"),
            average_fill_price=Decimal("1919.51"),
            total_fee=Decimal("0.12591985"),
            fill_timestamp=CYCLE_NOW - timedelta(minutes=15),
            position_projectable=True,
        ),
        position_id=position_id,
        protection_result=SimpleNamespace(
            is_active=True,
            stop_exchange_order_id="xo-stop-delayed-exit",
            tp_exchange_order_id="xo-tp-delayed-exit",
        ),
        stop_loss_price=Decimal("1900"),
        take_profit_price=Decimal("1950"),
        stop_client_order_id="A2S-delayed-exit",
        tp_client_order_id="A2T-delayed-exit",
    )
    with get_session_factory()() as session:
        position = session.get(V2ManagedPosition, position_id)
        assert position is not None
        position.state = V2PositionState.QUARANTINED.value
        position.version += 1
        session.commit()

    client_order_id = exit_client_order_id(position_id, attempt=2)
    adapter = MagicMock()
    adapter.query_order_by_client_id.side_effect = lambda _symbol, candidate_client_order_id: (
        ExchangeOrderReceipt(
            exchange_order_id="14909451503",
            client_order_id=client_order_id,
            symbol="ETH/USDT",
            side="sell",
            order_type="market",
            quantity=Decimal("0.164"),
            price=None,
            status="closed",
            acknowledged_at=CYCLE_NOW,
        )
        if candidate_client_order_id == client_order_id
        else None
    )
    adapter.fetch_fills.return_value = (
        ExchangeFillReceipt(
            exchange_order_id="14909451503",
            trade_id="308716592",
            filled_quantity=Decimal("0.164"),
            fill_price=Decimal("1918.5"),
            fee=Decimal("0.1258536"),
            fill_timestamp=CYCLE_NOW,
        ),
    )
    request = build_request(
        cycle_id="cycle-recover-delayed-exit",
        symbol="ETH/USDT",
        persist_facts=True,
    )
    cycle_result = CycleResult(
        cycle_id=request.cycle_id,
        symbol=request.symbol,
    )

    changed = _recover_confirmed_v2_exit_gaps(
        request,
        adapter,
        _snapshot(),
        cycle_result,
    )

    assert changed is True
    assert cycle_result.exit_submitted is True
    with get_session_factory()() as session:
        position = session.get(V2ManagedPosition, position_id)
        assert position is not None
        assert position.state == V2PositionState.CLOSED.value
        fill = session.query(V2ExchangeFill).filter_by(trade_id="308716592").one()
        assert fill.reduce_only is True


@patch("services.automated_trading.application.cycle_service.persist_exit_result")
def test_protection_fill_repairs_quarantined_position(mock_persist_exit) -> None:
    position = LocalPositionView(
        position_id="pos-protection-quarantined",
        symbol="ETH/USDT",
        direction="long",
        quantity=Decimal("0.164"),
        state="QUARANTINED",
        claim_keys=frozenset(),
        has_active_protection=True,
        protection_exchange_order_ids=frozenset({"algo-stop", "algo-tp"}),
        protection_order_refs=(("algo-stop", "A2S-quarantined", ExitReason.HARD_STOP.value),),
    )
    adapter = MagicMock()
    adapter.fetch_fills.return_value = (
        ExchangeFillReceipt(
            exchange_order_id="exit-order-from-stop",
            trade_id="exit-trade-from-stop",
            filled_quantity=Decimal("0.164"),
            fill_price=Decimal("1909.20"),
            fee=Decimal("0.125"),
            fill_timestamp=CYCLE_NOW,
        ),
    )
    request = build_request(
        cycle_id="cycle-protection-quarantined",
        symbol="ETH/USDT",
        persist_facts=True,
    )
    result = CycleResult(cycle_id=request.cycle_id, symbol=request.symbol)

    changed = _project_confirmed_protection_exits(
        request,
        adapter,
        _snapshot(),
        LocalStateView(positions=(position,)),
        result,
    )

    assert changed is True
    assert result.exit_submitted is True
    mock_persist_exit.assert_called_once()


def test_protection_fill_loads_quarantined_position_excluded_from_local_state() -> None:
    position_id = "pos-quarantined-protection-db"
    persist_entry_and_protection(
        cycle_id="cycle-entry-quarantined-protection",
        decision_id="decision-entry-quarantined-protection",
        intent_id="intent-entry-quarantined-protection",
        symbol="ETH/USDT",
        direction="long",
        candidate_key="testnet_sampling_v2",
        candidate_type=V2CandidateType.SAMPLING,
        execution_mode=V2ExecutionMode.BINANCE_TESTNET,
        decision_bar_timestamp=CYCLE_NOW - timedelta(minutes=15),
        fencing_token="fence-entry-quarantined-protection",
        leverage=40,
        entry_result=SimpleNamespace(
            status=SimpleNamespace(value="FILLED"),
            intent_state=V2IntentState.FILLED,
            client_order_id="A2E-quarantined-protection",
            exchange_order_id="xo-entry-quarantined-protection",
            trade_ids=("trade-entry-quarantined-protection",),
            requested_quantity=Decimal("0.164"),
            filled_quantity=Decimal("0.164"),
            average_fill_price=Decimal("1917.09"),
            total_fee=Decimal("0.12591985"),
            fill_timestamp=CYCLE_NOW - timedelta(minutes=15),
            position_projectable=True,
        ),
        position_id=position_id,
        protection_result=SimpleNamespace(
            is_active=True,
            stop_exchange_order_id="algo-stop-quarantined-db",
            tp_exchange_order_id="algo-tp-quarantined-db",
        ),
        stop_loss_price=Decimal("1909.20"),
        take_profit_price=Decimal("1928.70"),
        stop_client_order_id="A2S-quarantined-db",
        tp_client_order_id="A2T-quarantined-db",
    )
    with get_session_factory()() as session:
        position = session.get(V2ManagedPosition, position_id)
        assert position is not None
        position.state = V2PositionState.QUARANTINED.value
        position.version += 1
        session.commit()

    adapter = MagicMock()
    adapter.fetch_fills.side_effect = lambda _symbol, order_id: (
        (
            ExchangeFillReceipt(
                exchange_order_id="actual-stop-order-quarantined-db",
                trade_id="trade-stop-quarantined-db",
                filled_quantity=Decimal("0.164"),
                fill_price=Decimal("1909.20"),
                fee=Decimal("0.125"),
                fill_timestamp=CYCLE_NOW,
            ),
        )
        if order_id == "algo-stop-quarantined-db"
        else ()
    )
    request = build_request(
        cycle_id="cycle-repair-quarantined-protection",
        symbol="ETH/USDT",
        persist_facts=True,
    )
    result = CycleResult(cycle_id=request.cycle_id, symbol=request.symbol)

    changed = _project_confirmed_protection_exits(
        request,
        adapter,
        _snapshot(
            positions=[
                ExchangePositionSnapshot(
                    symbol="ETH/USDT",
                    direction="long",
                    quantity=Decimal("0.164"),
                    entry_price=Decimal("1913.23"),
                    mark_price=Decimal("1910"),
                    unrealized_pnl=Decimal("-0.53"),
                    leverage=40,
                )
            ]
        ),
        LocalStateView(),
        result,
    )

    assert changed is True
    with get_session_factory()() as session:
        position = session.get(V2ManagedPosition, position_id)
        protection = session.query(V2ProtectionRecord).filter_by(position_id=position_id).one()
        assert position is not None
        assert position.state == V2PositionState.CLOSED.value
        assert protection.state == "PROTECTION_FILLED"
        exit_fill = session.query(V2ExchangeFill).filter_by(trade_id="trade-stop-quarantined-db").one()
        assert exit_fill.exchange_order_id == "actual-stop-order-quarantined-db"


@patch("services.automated_trading.application.cycle_service.execute_reduce_only_exit")
@patch("services.automated_trading.application.cycle_service._build_local_state")
def test_cycle_does_not_blind_time_exit_without_forced_reason(
    mock_local_state,
    mock_execute_exit,
) -> None:
    mock_local_state.return_value = LocalStateLoadResult(status="OK", state=_open_local_position())
    adapter = build_adapter_with_successful_cycle()
    adapter.fetch_authoritative_snapshot.return_value = _snapshot(positions=[_exchange_position()])

    result = run_automated_trading_cycle(build_request(), adapter)

    mock_execute_exit.assert_not_called()
    assert not result.exit_submitted


@patch("services.automated_trading.application.cycle_service.execute_recovery_actions")
@patch("services.automated_trading.application.cycle_service._build_local_state")
def test_cycle_uses_persisted_exchange_identity_to_claim_managed_position(
    mock_local_state,
    mock_recovery,
) -> None:
    local_state = LocalStateView(
        positions=(
            LocalPositionView(
                position_id="pos-exit-cycle-1",
                symbol="BTC/USDT",
                direction="long",
                quantity=Decimal("0.01"),
                state="PROTECTED",
                claim_keys=frozenset({"entry-order-1", "entry-trade-1"}),
                has_active_protection=True,
            ),
        )
    )
    mock_local_state.return_value = SimpleNamespace(
        status="OK",
        state=local_state,
        error_code=None,
        exchange_position_claim_refs={
            "BTC/USDT:long": frozenset({"entry-order-1", "entry-trade-1"}),
        },
    )
    adapter = build_adapter_with_successful_cycle()
    adapter.fetch_authoritative_snapshot.return_value = _snapshot(positions=[_exchange_position()])

    result = run_automated_trading_cycle(build_request(), adapter)

    assert result.reconciliation_status.value == "HEALTHY"
    mock_recovery.assert_not_called()


@patch("services.automated_trading.application.cycle_service.persist_exit_result")
@patch("services.automated_trading.application.cycle_service.execute_recovery_actions")
@patch("services.automated_trading.application.cycle_service._build_local_state")
def test_cycle_projects_confirmed_exchange_protection_fill_before_ghost_recovery(
    mock_local_state,
    mock_recovery,
    mock_persist_exit,
) -> None:
    protected = LocalStateView(
        positions=(
            LocalPositionView(
                position_id="pos-protection-fill",
                symbol="BTC/USDT",
                direction="long",
                quantity=Decimal("0.01"),
                state="PROTECTED",
                claim_keys=frozenset({"trade-entry"}),
                has_active_protection=True,
                protection_exchange_order_ids=frozenset({"xo-stop-fill", "xo-tp-open"}),
                protection_order_refs=(
                    ("xo-stop-fill", "A2S-stop-fill", ExitReason.HARD_STOP.value),
                    ("xo-tp-open", "A2T-tp-open", ExitReason.TAKE_PROFIT.value),
                ),
            ),
        )
    )
    mock_local_state.side_effect = [
        LocalStateLoadResult(status="OK", state=protected),
        LocalStateLoadResult(status="OK", state=LocalStateView()),
    ]
    adapter = build_adapter_with_successful_cycle()
    adapter.fetch_authoritative_snapshot.side_effect = [_snapshot(), _snapshot()]
    adapter.fetch_fills.side_effect = [
        (
            ExchangeFillReceipt(
                exchange_order_id="xo-stop-fill",
                trade_id="trade-stop-fill",
                filled_quantity=Decimal("0.01"),
                fill_price=Decimal("49000"),
                fee=Decimal("0.2"),
                fill_timestamp=CYCLE_NOW,
            ),
        ),
    ]

    result = run_automated_trading_cycle(build_request(persist_facts=True), adapter)

    mock_persist_exit.assert_called_once()
    assert mock_persist_exit.call_args.kwargs["reason"] is ExitReason.HARD_STOP
    adapter.cancel_order.assert_called_once_with("BTC/USDT", "xo-tp-open")
    assert result.reconciliation_status.value == "HEALTHY"
    mock_recovery.assert_not_called()
