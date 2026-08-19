from __future__ import annotations

from services.automated_trading.audit.behavior_freeze import compare_golden_behavior


def _behavior(*, candidates: int = 22) -> dict:
    return {
        "unique_closed_bar_decisions": 100,
        "signals": 42,
        "candidates": candidates,
        "reason_distribution": {"NO_SIGNAL": 58, "CANDIDATE_READY": candidates},
        "directions": {"LONG": 12, "SHORT": 10},
        "stop_geometry": {"atr_multiple": "2"},
        "target_geometry": {"risk_reward": "2"},
        "dry_run_intents": candidates,
    }


def test_behavior_freeze_fails_on_candidate_starvation_even_when_decisions_match() -> None:
    comparison = compare_golden_behavior(baseline=_behavior(candidates=22), observed=_behavior(candidates=1))

    assert comparison.status == "BEHAVIOR_REGRESSION=FAIL"
    assert comparison.baseline_candidate_rate == "0.22"
    assert comparison.observed_candidate_rate == "0.01"
    assert "candidates" in comparison.differences


def test_behavior_freeze_passes_only_exact_strategy_behavior() -> None:
    comparison = compare_golden_behavior(baseline=_behavior(), observed=_behavior())

    assert comparison.status == "BEHAVIOR_REGRESSION=PASS"
    assert comparison.differences == {}
