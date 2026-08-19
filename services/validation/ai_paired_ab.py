"""Eligibility gate for AI ranking evaluated against paired deterministic proposals."""

from __future__ import annotations

from typing import Any, Literal

from shared.models import PlatformModel


class AIPairedABMetrics(PlatformModel):
    paired_expectancy_lcb: float
    deterministic_profit_factor: float
    ai_profit_factor: float
    deterministic_max_drawdown: float
    ai_max_drawdown: float
    json_success_rate: float
    invocation_coverage: float
    lookahead_detected: bool


class AIExecutionEligibility(PlatformModel):
    execution_eligible: bool
    mode: Literal["EXPLANATION_ONLY", "RANKING_ELIGIBLE"]
    failed_requirements: tuple[str, ...]


class ResearchCouncilABResult(PlatformModel):
    """Paired baseline/council evidence; this is advisory and never an order command."""

    baseline_candidate_id: str
    council_candidate_id: str
    baseline_metrics: dict[str, Any]
    council_metrics: dict[str, Any]
    verdict: Literal["insufficient_evidence", "baseline_preferred", "council_preferred"]
    evidence_refs: tuple[str, ...] = ()
    order_side_effects: bool = False


def evaluate_ai_execution_eligibility(metrics: AIPairedABMetrics) -> AIExecutionEligibility:
    """Allow AI ranking only after the complete paired A/B safety case passes."""

    failures: list[str] = []
    if metrics.paired_expectancy_lcb <= 0:
        failures.append("paired_expectancy_lcb_not_positive")
    if metrics.ai_profit_factor <= metrics.deterministic_profit_factor:
        failures.append("profit_factor_not_improved")
    if metrics.ai_max_drawdown - metrics.deterministic_max_drawdown > 0.02:
        failures.append("max_drawdown_degraded_over_2pp")
    if metrics.json_success_rate < 0.99:
        failures.append("json_success_rate_below_99_percent")
    if metrics.invocation_coverage < 0.95:
        failures.append("invocation_coverage_below_95_percent")
    if metrics.lookahead_detected:
        failures.append("lookahead_detected")
    return AIExecutionEligibility(
        execution_eligible=not failures,
        mode="RANKING_ELIGIBLE" if not failures else "EXPLANATION_ONLY",
        failed_requirements=tuple(failures),
    )
