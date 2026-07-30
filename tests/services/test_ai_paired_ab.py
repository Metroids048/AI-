from __future__ import annotations

import pytest

from services.validation.ai_paired_ab import AIPairedABMetrics, evaluate_ai_execution_eligibility


def _metrics(**updates: float | bool) -> AIPairedABMetrics:
    values: dict[str, float | bool] = {
        "paired_expectancy_lcb": 0.0001,
        "deterministic_profit_factor": 1.2,
        "ai_profit_factor": 1.3,
        "deterministic_max_drawdown": 0.10,
        "ai_max_drawdown": 0.11,
        "json_success_rate": 0.99,
        "invocation_coverage": 0.95,
        "lookahead_detected": False,
    }
    values.update(updates)
    return AIPairedABMetrics(**values)


def test_ai_paired_ab_is_advisory_only_until_all_gates_pass() -> None:
    result = evaluate_ai_execution_eligibility(_metrics(paired_expectancy_lcb=0.0))

    assert result.execution_eligible is False
    assert result.mode == "EXPLANATION_ONLY"
    assert "paired_expectancy_lcb_not_positive" in result.failed_requirements


@pytest.mark.parametrize(
    ("updates", "reason"),
    [
        ({"ai_profit_factor": 1.2}, "profit_factor_not_improved"),
        ({"ai_max_drawdown": 0.121}, "max_drawdown_degraded_over_2pp"),
        ({"json_success_rate": 0.989}, "json_success_rate_below_99_percent"),
        ({"invocation_coverage": 0.949}, "invocation_coverage_below_95_percent"),
        ({"lookahead_detected": True}, "lookahead_detected"),
    ],
)
def test_ai_paired_ab_rejects_each_safety_requirement(updates: dict[str, float | bool], reason: str) -> None:
    result = evaluate_ai_execution_eligibility(_metrics(**updates))

    assert result.execution_eligible is False
    assert reason in result.failed_requirements


def test_ai_paired_ab_allows_ranking_only_after_all_gates_pass() -> None:
    result = evaluate_ai_execution_eligibility(_metrics())

    assert result.execution_eligible is True
    assert result.mode == "RANKING_ELIGIBLE"
