from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from services.execution.v2_scheduler_entry import _research_shadow_payload
from services.strategy_library import proposal_pipeline as proposal_pipeline_module
from services.strategy_library.canonical import canonical_hash, canonical_json
from services.strategy_library.context import MarketContextBuilder
from services.strategy_library.proposal_pipeline import PIPELINE_VERSION, run_proposal_pipeline
from shared.models import Exchange, OHLCVBar, Timeframe


def _bars(timeframe: Timeframe, *, count: int, end_at: datetime) -> list[OHLCVBar]:
    minutes = {Timeframe.M1: 1, Timeframe.M5: 5, Timeframe.M15: 15, Timeframe.H1: 60, Timeframe.H4: 240}[timeframe]
    return [
        OHLCVBar(
            symbol="BTC/USDT",
            exchange=Exchange.BINANCE,
            timeframe=timeframe,
            time=end_at - timedelta(minutes=minutes * (count - index)),
            open=Decimal("100"),
            high=Decimal("101"),
            low=Decimal("99"),
            close=Decimal("100"),
            volume=Decimal("10"),
        )
        for index in range(count)
    ]


def _context():  # noqa: ANN202
    decision_time = datetime(2025, 1, 2, tzinfo=UTC)
    return MarketContextBuilder().build(
        symbol="BTC/USDT",
        decision_time=decision_time,
        bars_by_timeframe={
            "1m": _bars(Timeframe.M1, count=2, end_at=decision_time),
            "5m": _bars(Timeframe.M5, count=2, end_at=decision_time),
            "15m": _bars(Timeframe.M15, count=80, end_at=decision_time),
            "1h": _bars(Timeframe.H1, count=80, end_at=decision_time),
            "4h": _bars(Timeframe.H4, count=80, end_at=decision_time),
        },
        source_ids=("test",),
    )


def test_canonical_hash_ignores_mapping_field_order() -> None:
    left = {"outer": {"b": 2, "a": 1}, "items": [3, 4]}
    right = {"items": [3, 4], "outer": {"a": 1, "b": 2}}

    assert canonical_json(left) == canonical_json(right)
    assert canonical_hash(left) == canonical_hash(right)
    assert canonical_hash(left) != canonical_hash({"outer": {"a": 9, "b": 2}, "items": [3, 4]})


def test_canonical_hash_preserves_scalar_types_and_non_string_mapping_keys() -> None:
    assert canonical_hash(Decimal("1")) != canonical_hash("1")
    assert canonical_hash({1: "value"}) != canonical_hash({"1": "value"})
    assert canonical_hash(datetime(2025, 1, 1, tzinfo=UTC)) != canonical_hash("2025-01-01T00:00:00+00:00")


def test_canonical_hash_preserves_list_and_tuple_types() -> None:
    assert canonical_hash([1, 2]) != canonical_hash((1, 2))


def test_pipeline_is_deterministic_and_emits_explicit_candidate_rejections() -> None:
    context = _context()

    first = run_proposal_pipeline(context)
    second = run_proposal_pipeline(context)

    assert first == second
    assert first.pipeline_version == PIPELINE_VERSION
    assert len(first.context_hash) == 64
    assert set(first.rejection_reasons).issuperset(
        {"trend_pullback_v2", "range_sweep_reversion_v1", "failed_breakout_reversal_v1"}
    )


def test_v2_shadow_serializes_the_exact_shared_pipeline_result() -> None:
    context = _context()

    assert _research_shadow_payload(context) == run_proposal_pipeline(context).model_dump(mode="json")


def test_replay_candidate_filter_uses_same_pipeline_without_evaluating_other_candidates() -> None:
    result = run_proposal_pipeline(_context(), candidate_ids=frozenset(("trend_pullback_v2",)))

    assert set(result.rejection_reasons) == {"trend_pullback_v2"}


def test_pipeline_rejects_unknown_candidate_filter() -> None:
    with pytest.raises(ValueError, match="unknown proposal candidate ids"):
        run_proposal_pipeline(_context(), candidate_ids=frozenset(("unknown",)))


def test_proposal_pipeline_isolates_one_candidate_error(monkeypatch) -> None:
    evaluated: list[str] = []

    def fails(_context, _regime):  # noqa: ANN001, ANN202
        evaluated.append("trend_pullback_v2")
        raise RuntimeError("candidate failed\nwith unsafe multiline detail")

    def no_signal(strategy_id: str):  # noqa: ANN202
        def evaluate(_context, _regime):  # noqa: ANN001, ANN202
            evaluated.append(strategy_id)
            return None

        return evaluate

    monkeypatch.setattr(
        proposal_pipeline_module,
        "_evaluators",
        lambda: (
            ("trend_pullback_v2", fails),
            ("range_sweep_reversion_v1", no_signal("range_sweep_reversion_v1")),
            ("failed_breakout_reversal_v1", no_signal("failed_breakout_reversal_v1")),
        ),
    )

    result = run_proposal_pipeline(_context())

    assert evaluated == [
        "trend_pullback_v2",
        "range_sweep_reversion_v1",
        "failed_breakout_reversal_v1",
    ]
    assert result.evaluation_errors["trend_pullback_v2"].error_class == "RuntimeError"
    assert result.evaluation_errors["trend_pullback_v2"].safe_message == (
        "candidate failed with unsafe multiline detail"
    )
    assert set(result.rejection_reasons) == {
        "range_sweep_reversion_v1",
        "failed_breakout_reversal_v1",
    }
