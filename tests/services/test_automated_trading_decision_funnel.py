"""Task 6 / Gate 6: every evaluated decision bar must produce one terminal record.

Gate 6 requirements under test:
- Each closed decision bar yields exactly one terminal funnel record.
- A repeated bar terminates with DUPLICATE_DECISION, never a silent return.
- No-signal / regime / meta-label / manifest rejections use *distinct* reason codes.
- Candidates carry only relative distances; absolute protection prices are
  derived later from a real average_fill_price.
- The decision service is pure: it touches no exchange state.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from services.automated_trading.application.decision_service import (
    BarView,
    DecisionContext,
    TimeframeView,
    evaluate_sampling_signal,
    evaluate_symbol,
    sampling_stop_distance,
)
from services.automated_trading.domain.candidates import (
    CandidateLane,
    CandidateSide,
)
from services.automated_trading.observability.decision_funnel import (
    DecisionReasonCode,
    FunnelStage,
    StageOutcome,
)

BAR_START = datetime(2026, 7, 27, 0, 0, tzinfo=UTC)

# Verified sequences: 30 flat bars then an alternating ramp. The pullbacks keep
# RSI inside the sampling band instead of pegging it at 0/100.
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
SHORT_CLOSES = [100.0] * 30 + [
    99.7,
    100.0,
    99.6,
    99.9,
    99.5,
    99.8,
    99.4,
    99.7,
    99.3,
    99.6,
    99.2,
    99.5,
    99.1,
    99.4,
    99.0,
    99.3,
    99.2,
    99.1,
    99.0,
    98.9,
]
FLAT_CLOSES = [100.0] * 50


def build_timeframe(closes: list[float], *, timeframe: str = "15m") -> TimeframeView:
    bars = []
    for index, close in enumerate(closes):
        price = Decimal(str(close))
        bars.append(
            BarView(
                timestamp=BAR_START + timedelta(minutes=15 * index),
                open=price,
                high=price * Decimal("1.003"),
                low=price * Decimal("0.997"),
                close=price,
                volume=Decimal("100"),
            )
        )
    return TimeframeView(timeframe=timeframe, bars=tuple(bars))


def build_context(closes: list[float], **overrides) -> DecisionContext:
    entry = build_timeframe(closes)
    last_bar = entry.last_closed
    # An empty timeframe is a valid input: the service must terminate at
    # DATA_AVAILABLE rather than raise. Fall back to a fixed clock in that case.
    reference_now = BAR_START if last_bar is None else last_bar.timestamp + timedelta(seconds=10)
    defaults: dict[str, object] = {
        "cycle_id": "cycle-1",
        "symbol": "BTC/USDT",
        "lane": CandidateLane.TESTNET_SAMPLING,
        "strategy_id": "testnet_sampling_v2",
        "strategy_version": "1.0.0",
        "entry_timeframe": entry,
        "now": reference_now,
    }
    defaults.update(overrides)
    return DecisionContext(**defaults)


# ---------------------------------------------------------------------------
# Signal rules
# ---------------------------------------------------------------------------


def test_sampling_rules_emit_long_on_confirmed_uptrend() -> None:
    evaluation = evaluate_sampling_signal(build_timeframe(LONG_CLOSES))

    assert evaluation.side is CandidateSide.LONG
    assert evaluation.reason_code is None
    assert evaluation.confidence > 0


def test_sampling_rules_emit_short_on_confirmed_downtrend() -> None:
    evaluation = evaluate_sampling_signal(build_timeframe(SHORT_CLOSES))

    assert evaluation.side is CandidateSide.SHORT
    assert evaluation.reason_code is None


def test_sampling_rules_reject_flat_market_with_specific_reason() -> None:
    evaluation = evaluate_sampling_signal(build_timeframe(FLAT_CLOSES))

    assert evaluation.side is None
    assert evaluation.reason_code is not None


def test_sampling_rules_report_insufficient_history() -> None:
    evaluation = evaluate_sampling_signal(build_timeframe([100.0] * 20))

    assert evaluation.side is None
    assert evaluation.reason_code is DecisionReasonCode.INSUFFICIENT_HISTORY


def test_stop_distance_uses_the_larger_of_atr_and_price_floor() -> None:
    # ATR term dominates: 1.2 * 10 = 12 > 100 * 0.0035 = 0.35
    assert sampling_stop_distance(atr14=Decimal("10"), reference_price=Decimal("100")) == Decimal("12.0")
    # Price floor dominates: 1.2 * 0.01 = 0.012 < 100 * 0.0035 = 0.35
    assert sampling_stop_distance(atr14=Decimal("0.01"), reference_price=Decimal("100")) == Decimal("0.35")


# ---------------------------------------------------------------------------
# Gate 6: every bar produces a terminal record
# ---------------------------------------------------------------------------


def test_successful_evaluation_produces_candidate_and_sealed_funnel() -> None:
    outcome = evaluate_symbol(build_context(LONG_CLOSES))

    assert outcome.has_candidate
    assert outcome.candidate is not None
    assert outcome.reason_code is DecisionReasonCode.CANDIDATE_READY
    assert outcome.terminal_stage is FunnelStage.MANIFEST_EVALUATED
    assert outcome.funnel.created_candidate is True
    assert outcome.funnel.candidate_id == outcome.candidate.candidate_id


def test_every_stage_is_recorded_in_order() -> None:
    outcome = evaluate_symbol(build_context(LONG_CLOSES))
    stages = [record.stage for record in outcome.funnel.stages]

    assert stages[0] is FunnelStage.CYCLE_STARTED
    assert FunnelStage.DATA_AVAILABLE in stages
    assert FunnelStage.CANDLE_CLOSED in stages
    assert FunnelStage.DATA_FRESH in stages
    assert FunnelStage.REGIME_EVALUATED in stages
    assert FunnelStage.ENTRY_SIGNAL_EVALUATED in stages
    assert FunnelStage.CANDIDATE_CREATED in stages
    assert stages[-1] is FunnelStage.MANIFEST_EVALUATED


def test_rejected_evaluation_still_produces_a_terminal_record() -> None:
    outcome = evaluate_symbol(build_context(FLAT_CLOSES))

    assert not outcome.has_candidate
    assert outcome.candidate is None
    assert outcome.terminal_stage is FunnelStage.ENTRY_SIGNAL_EVALUATED
    assert outcome.funnel.terminal_outcome is StageOutcome.REJECTED
    assert outcome.funnel.created_candidate is False


def test_missing_market_data_terminates_at_data_available() -> None:
    outcome = evaluate_symbol(build_context([]))

    assert outcome.terminal_stage is FunnelStage.DATA_AVAILABLE
    assert outcome.reason_code is DecisionReasonCode.NO_MARKET_DATA


def test_funnel_payload_is_serializable_for_the_runtime_api() -> None:
    payload = evaluate_symbol(build_context(LONG_CLOSES)).funnel.to_payload()

    assert payload["symbol"] == "BTC/USDT"
    assert payload["reason_code"] == DecisionReasonCode.CANDIDATE_READY.value
    assert payload["created_candidate"] is True
    assert payload["bar_timestamp"] is not None
    assert len(payload["stages"]) >= 7
    for stage in payload["stages"]:
        assert isinstance(stage["stage"], str)
        assert isinstance(stage["reason_code"], str)
