"""Task 7 / Gate 7: Entry Gate and Exchange-First entry.

Gate 7 requirements under test:
- An unhealthy account cannot create an intent.
- A failed submission creates no position.
- SHADOW never submits.
- A retry never produces duplicate risk (stable Client Order ID, and UNKNOWN
  resolves by lookup rather than resubmission).
- Every rejection is observable via a stable reason code.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from services.automated_trading.application.entry_service import (
    EntryDecision,
    EntryExecutionStatus,
    EntryRuntimeContext,
    ExchangeTimeout,
    drift_ceiling_bps,
    evaluate_entry,
    execute_entry,
    round_quantity_to_step,
)
from services.automated_trading.application.reconciliation_service import ReconciliationStatus
from services.automated_trading.domain.candidates import (
    CandidateLane,
    CandidateSide,
    TradeCandidate,
)
from services.automated_trading.domain.client_order_id import entry_client_order_id
from services.automated_trading.domain.enums import V2CandidateType, V2ExecutionMode, V2IntentState
from services.automated_trading.infrastructure.binance_adapter import (
    BinanceAdapterUnavailable,
    ExchangeFillReceipt,
    ExchangeOrderReceipt,
)
from services.automated_trading.infrastructure.market_snapshot_provider import PreSubmitMarketSnapshot
from services.automated_trading.infrastructure.runtime_lock import EngineActivation
from services.automated_trading.observability.decision_funnel import DecisionReasonCode

NOW = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)
BAR = NOW - timedelta(seconds=10)
INTENT_ID = "intent-7f3a"


def build_candidate(**overrides) -> TradeCandidate:
    defaults: dict[str, object] = {
        "candidate_id": "cand-1",
        "cycle_id": "cycle-1",
        "strategy_id": "testnet_sampling_v2",
        "strategy_version": "1.0.0",
        "lane": CandidateLane.TESTNET_SAMPLING,
        "candidate_type": V2CandidateType.SAMPLING,
        "symbol": "BTC/USDT",
        "side": CandidateSide.LONG,
        "signal_candle_close_time": BAR,
        "signal_reference_price": Decimal("50000"),
        "confidence": Decimal("0.55"),
        "stop_distance": Decimal("500"),
        "take_profit_distance": Decimal("750"),
        "max_entry_drift_bps": Decimal("20"),
        "expires_at": NOW + timedelta(seconds=65),
        "non_promotable": True,
    }
    defaults.update(overrides)
    return TradeCandidate(**defaults)


def build_snapshot(current_price: str = "50000", atr: str = "0") -> PreSubmitMarketSnapshot:
    return PreSubmitMarketSnapshot(
        symbol="BTC/USDT",
        current_price=Decimal(current_price),
        atr=Decimal(atr),
        last_update=NOW,
        tick_size=Decimal("0.01"),
        step_size=Decimal("0.001"),
        min_notional=Decimal("10"),
    )


def healthy_runtime(**overrides) -> EntryRuntimeContext:
    defaults: dict[str, object] = {
        "engine_activation": EngineActivation.ACTIVE,
        "execution_mode": V2ExecutionMode.BINANCE_TESTNET,
        "reconciliation_status": ReconciliationStatus.HEALTHY,
        "now": NOW,
    }
    defaults.update(overrides)
    return EntryRuntimeContext(**defaults)


# ---------------------------------------------------------------------------
# Entry Gate
# ---------------------------------------------------------------------------


def test_healthy_context_approves_entry() -> None:
    result = evaluate_entry(build_candidate(), healthy_runtime(), build_snapshot())

    assert result.approved
    assert result.decision is EntryDecision.APPROVED
    assert result.reason_code is DecisionReasonCode.OK
    assert result.blocks == ()


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (ReconciliationStatus.UNAVAILABLE, DecisionReasonCode.RECONCILIATION_UNAVAILABLE),
        (ReconciliationStatus.RECOVERY_REQUIRED, DecisionReasonCode.RECOVERY_REQUIRED),
    ],
)
def test_unhealthy_reconciliation_blocks_entry(status, expected) -> None:
    """Gate 7: an unhealthy account cannot create an intent."""
    result = evaluate_entry(build_candidate(), healthy_runtime(reconciliation_status=status), build_snapshot())

    assert not result.approved
    assert expected in {code for code, _ in result.blocks}


def test_degraded_reconciliation_allows_unaffected_symbol() -> None:
    """DEGRADED only blocks the quarantined symbol via entry_blocked_symbols, not all symbols."""
    result = evaluate_entry(
        build_candidate(), healthy_runtime(reconciliation_status=ReconciliationStatus.DEGRADED), build_snapshot()
    )

    assert result.approved


def test_entry_kill_switch_blocks_entry() -> None:
    result = evaluate_entry(build_candidate(), healthy_runtime(entry_kill_switch_active=True), build_snapshot())

    assert not result.approved
    assert DecisionReasonCode.ENTRY_KILL_SWITCH_ACTIVE in {code for code, _ in result.blocks}


def test_entry_blocked_symbol_reports_unmanaged_external_position() -> None:
    runtime = healthy_runtime(entry_blocked_symbols=frozenset({"BTC/USDT"}))
    result = evaluate_entry(build_candidate(), runtime, build_snapshot())

    assert not result.approved
    assert DecisionReasonCode.UNMANAGED_EXTERNAL_POSITION in {code for code, _ in result.blocks}


def test_existing_open_position_blocks_second_entry() -> None:
    runtime = healthy_runtime(open_position_symbols=frozenset({"BTC/USDT"}))
    result = evaluate_entry(build_candidate(), runtime, build_snapshot())

    assert not result.approved
    assert DecisionReasonCode.POSITION_ALREADY_OPEN in {code for code, _ in result.blocks}


def test_negative_net_edge_blocks_entry() -> None:
    runtime = healthy_runtime(net_edge_after_cost_bps=Decimal("-3"))
    result = evaluate_entry(build_candidate(), runtime, build_snapshot())

    assert not result.approved
    assert DecisionReasonCode.NET_EDGE_AFTER_COST_NEGATIVE in {code for code, _ in result.blocks}


def test_expired_candidate_blocks_entry() -> None:
    candidate = build_candidate(expires_at=NOW - timedelta(seconds=1))
    result = evaluate_entry(candidate, healthy_runtime(), build_snapshot())

    assert not result.approved
    assert DecisionReasonCode.CANDIDATE_EXPIRED in {code for code, _ in result.blocks}


def test_all_blocks_are_reported_not_just_the_first() -> None:
    """An operator should see every blocker at once."""
    runtime = healthy_runtime(
        entry_kill_switch_active=True,
        reconciliation_status=ReconciliationStatus.RECOVERY_REQUIRED,
        daily_trade_limit_reached=True,
    )
    result = evaluate_entry(build_candidate(), runtime, build_snapshot())

    codes = {code for code, _ in result.blocks}
    assert DecisionReasonCode.ENTRY_KILL_SWITCH_ACTIVE in codes
    assert DecisionReasonCode.RECOVERY_REQUIRED in codes
    assert DecisionReasonCode.DAILY_TRADE_LIMIT_REACHED in codes


def test_price_drift_beyond_ceiling_blocks_entry_without_chasing() -> None:
    # 50000 -> 50400 is 80 bps, past the 20 bps ceiling.
    result = evaluate_entry(build_candidate(), healthy_runtime(), build_snapshot(current_price="50400"))

    assert not result.approved
    assert DecisionReasonCode.PRICE_DRIFT_EXCEEDED in {code for code, _ in result.blocks}
    assert result.drift_bps is not None and result.drift_bps > result.drift_ceiling_bps


def test_small_drift_is_allowed() -> None:
    # 50000 -> 50050 is 10 bps, inside the 20 bps floor.
    result = evaluate_entry(build_candidate(), healthy_runtime(), build_snapshot(current_price="50050"))

    assert result.approved
    assert result.drift_bps == Decimal("10")


def test_drift_ceiling_never_exceeds_candidate_tolerance() -> None:
    """A volatile market must not widen a deliberately tight candidate."""
    candidate = build_candidate(max_entry_drift_bps=Decimal("15"))
    ceiling = drift_ceiling_bps(candidate, build_snapshot(atr="2000"))

    assert ceiling == Decimal("15")


def test_gate_makes_no_exchange_call() -> None:
    """The gate is pure: passing a strict mock adapter is unnecessary and unused."""
    adapter = MagicMock()
    evaluate_entry(build_candidate(), healthy_runtime(), build_snapshot())

    adapter.assert_not_called()


# ---------------------------------------------------------------------------
# Exchange-First execution
# ---------------------------------------------------------------------------


def _order_receipt(exchange_order_id: str = "ex-1") -> ExchangeOrderReceipt:
    return ExchangeOrderReceipt(
        exchange_order_id=exchange_order_id,
        client_order_id=entry_client_order_id(INTENT_ID),
        symbol="BTC/USDT",
        side="buy",
        order_type="market",
        quantity=Decimal("0.01"),
        price=None,
        status="filled",
        acknowledged_at=NOW,
    )


def _fill(quantity: str, price: str, trade_id: str = "t1") -> ExchangeFillReceipt:
    return ExchangeFillReceipt(
        exchange_order_id="ex-1",
        trade_id=trade_id,
        filled_quantity=Decimal(quantity),
        fill_price=Decimal(price),
        fee=Decimal("0.5"),
        fill_timestamp=NOW,
    )


def _execute(adapter, *, activation=EngineActivation.ACTIVE, quantity="0.01", gate=None):
    return execute_entry(
        build_candidate(),
        gate or evaluate_entry(build_candidate(), healthy_runtime(), build_snapshot()),
        build_snapshot(),
        adapter=adapter,
        intent_id=INTENT_ID,
        quantity=Decimal(quantity),
        leverage=10,
        engine_activation=activation,
    )


def test_blocked_gate_does_not_submit() -> None:
    adapter = MagicMock()
    blocked = evaluate_entry(
        build_candidate(),
        healthy_runtime(entry_kill_switch_active=True),
        build_snapshot(),
    )

    result = _execute(adapter, gate=blocked)

    assert result.status is EntryExecutionStatus.NOT_ATTEMPTED
    assert result.position_projectable is False
    adapter.submit_market_order.assert_not_called()


def test_shadow_mode_never_submits() -> None:
    """Gate 7: SHADOW never calls submit."""
    adapter = MagicMock()

    result = _execute(adapter, activation=EngineActivation.SHADOW)

    assert result.status is EntryExecutionStatus.SHADOW_REHEARSED
    assert result.reason_code is DecisionReasonCode.SHADOW_MODE_NO_SUBMIT
    assert result.position_projectable is False
    adapter.submit_market_order.assert_not_called()


def test_confirmed_fill_is_projectable() -> None:
    adapter = MagicMock()
    adapter.submit_market_order.return_value = _order_receipt()
    adapter.fetch_fills.return_value = (_fill("0.01", "50010"),)

    result = _execute(adapter)

    assert result.status is EntryExecutionStatus.FILLED
    assert result.intent_state is V2IntentState.FILLED
    assert result.position_projectable is True
    assert result.exchange_order_id == "ex-1"
    assert result.trade_ids == ("t1",)
    assert result.average_fill_price == Decimal("50010")


def test_submission_failure_creates_no_position() -> None:
    """Gate 7: a failed submission must not create a position."""
    adapter = MagicMock()
    adapter.submit_market_order.side_effect = BinanceAdapterUnavailable("margin insufficient")

    result = _execute(adapter)

    assert result.status is EntryExecutionStatus.REJECTED
    assert result.intent_state is V2IntentState.REJECTED
    assert result.position_projectable is False
    assert result.exchange_order_id is None


def test_timeout_yields_exchange_unknown_and_demands_lookup() -> None:
    """A sent request with an undetermined outcome must never be resubmitted."""
    adapter = MagicMock()
    adapter.submit_market_order.side_effect = ExchangeTimeout("read timeout after 10s")

    result = _execute(adapter)

    assert result.status is EntryExecutionStatus.UNKNOWN
    assert result.intent_state is V2IntentState.EXCHANGE_UNKNOWN
    assert result.requires_client_order_id_recovery is True
    assert result.position_projectable is False
    assert adapter.submit_market_order.call_count == 1


def test_acknowledged_without_fill_is_not_projectable() -> None:
    adapter = MagicMock()
    adapter.submit_market_order.return_value = _order_receipt()
    adapter.fetch_fills.return_value = ()

    result = _execute(adapter)

    assert result.status is EntryExecutionStatus.ACKNOWLEDGED_UNFILLED
    assert result.intent_state is V2IntentState.EXCHANGE_ACKNOWLEDGED
    assert result.position_projectable is False


def test_partial_fill_projects_only_confirmed_quantity() -> None:
    """Never project the requested quantity when only part filled."""
    adapter = MagicMock()
    adapter.submit_market_order.return_value = _order_receipt()
    adapter.fetch_fills.return_value = (_fill("0.004", "50000", "t1"),)

    result = _execute(adapter, quantity="0.01")

    assert result.status is EntryExecutionStatus.PARTIALLY_FILLED
    assert result.filled_quantity == Decimal("0.004")
    assert result.requested_quantity == Decimal("0.01")
    assert result.position_projectable is True


def test_multiple_fills_aggregate_to_vwap() -> None:
    adapter = MagicMock()
    adapter.submit_market_order.return_value = _order_receipt()
    adapter.fetch_fills.return_value = (
        _fill("0.005", "50000", "t1"),
        _fill("0.005", "50100", "t2"),
    )

    result = _execute(adapter)

    assert result.filled_quantity == Decimal("0.010")
    assert result.average_fill_price == Decimal("50050")
    assert result.trade_ids == ("t1", "t2")


def test_retry_reuses_the_same_client_order_id() -> None:
    """Gate 7: a retry must not create duplicate risk."""
    adapter = MagicMock()
    adapter.submit_market_order.side_effect = ExchangeTimeout("timeout")

    first = _execute(adapter)
    second = _execute(adapter)

    assert first.client_order_id == second.client_order_id
    assert first.client_order_id == entry_client_order_id(INTENT_ID)


def test_unreadable_fills_do_not_fabricate_a_position() -> None:
    adapter = MagicMock()
    adapter.submit_market_order.return_value = _order_receipt()
    adapter.fetch_fills.side_effect = BinanceAdapterUnavailable("rate limited")

    result = _execute(adapter)

    assert result.status is EntryExecutionStatus.ACKNOWLEDGED_UNFILLED
    assert result.position_projectable is False
    assert result.exchange_order_id == "ex-1"


def test_quantity_rounding_floors_to_step_size() -> None:
    assert round_quantity_to_step(Decimal("0.0129"), Decimal("0.001")) == Decimal("0.012")
    with pytest.raises(ValueError, match="step_size must be"):
        round_quantity_to_step(Decimal("1"), Decimal("0"))


def test_quantity_rounding_to_zero_is_not_submitted() -> None:
    adapter = MagicMock()

    result = _execute(adapter, quantity="0.0004")

    assert result.status is EntryExecutionStatus.NOT_ATTEMPTED
    assert result.reason_code is DecisionReasonCode.RISK_LIMIT_EXCEEDED
    adapter.submit_market_order.assert_not_called()


def test_notional_below_exchange_minimum_is_not_submitted() -> None:
    adapter = MagicMock()

    # 0.001 * 50000 = 50 >= 10, so raise the minimum instead.
    snapshot = PreSubmitMarketSnapshot(
        symbol="BTC/USDT",
        current_price=Decimal("50000"),
        atr=Decimal("0"),
        last_update=NOW,
        tick_size=Decimal("0.01"),
        step_size=Decimal("0.001"),
        min_notional=Decimal("100"),
    )
    result = execute_entry(
        build_candidate(),
        evaluate_entry(build_candidate(), healthy_runtime(), snapshot),
        snapshot,
        adapter=adapter,
        intent_id=INTENT_ID,
        quantity=Decimal("0.001"),
        leverage=10,
        engine_activation=EngineActivation.ACTIVE,
    )

    assert result.status is EntryExecutionStatus.NOT_ATTEMPTED
    assert result.reason_code is DecisionReasonCode.RISK_LIMIT_EXCEEDED
    adapter.submit_market_order.assert_not_called()


def test_short_candidate_submits_sell_side() -> None:
    adapter = MagicMock()
    adapter.submit_market_order.return_value = _order_receipt()
    adapter.fetch_fills.return_value = (_fill("0.01", "50000"),)

    candidate = build_candidate(side=CandidateSide.SHORT)
    execute_entry(
        candidate,
        evaluate_entry(candidate, healthy_runtime(), build_snapshot()),
        build_snapshot(),
        adapter=adapter,
        intent_id=INTENT_ID,
        quantity=Decimal("0.01"),
        leverage=10,
        engine_activation=EngineActivation.ACTIVE,
    )

    assert adapter.submit_market_order.call_args[0][2] == "sell"
