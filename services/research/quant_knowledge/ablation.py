"""Small, paired research helpers for primitive incremental-value tests."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from pydantic import Field

from services.strategy_library.event_edge import EdgeEvent, gate_metrics
from shared.models import PlatformModel

from .contracts import QuantPrimitive


class AblationComparison(PlatformModel):
    base_event: str
    primitive_ids: list[str] = Field(default_factory=list)
    baseline: dict[str, Any] = Field(default_factory=dict)
    candidate: dict[str, Any] = Field(default_factory=dict)
    delta: dict[str, Any] = Field(default_factory=dict)
    paired: bool = True


def compare_paired_events(
    baseline_events: Iterable[EdgeEvent],
    candidate_events: Iterable[EdgeEvent],
    *,
    base_event: str,
    primitive_ids: list[str] | None = None,
    cost_multiple: float = 1.0,
) -> AblationComparison:
    """Compare identical event definitions with one added primitive condition."""
    baseline = gate_metrics(tuple(baseline_events), cost_multiple=cost_multiple)
    candidate = gate_metrics(tuple(candidate_events), cost_multiple=cost_multiple)
    baseline_payload = {
        "trade_count": baseline.trades,
        "expectancy": baseline.expectancy,
        "profit_factor": baseline.profit_factor,
        "max_drawdown_r": baseline.max_drawdown_r,
        "expectancy_lcb95": baseline.expectancy_lcb95,
    }
    candidate_payload = {
        "trade_count": candidate.trades,
        "expectancy": candidate.expectancy,
        "profit_factor": candidate.profit_factor,
        "max_drawdown_r": candidate.max_drawdown_r,
        "expectancy_lcb95": candidate.expectancy_lcb95,
    }
    delta = {key: candidate_payload[key] - baseline_payload[key] for key in baseline_payload}
    return AblationComparison(
        base_event=base_event,
        primitive_ids=primitive_ids or [],
        baseline=baseline_payload,
        candidate=candidate_payload,
        delta=delta,
    )


def compose_research_candidate(
    base_event: str,
    *,
    filters: list[QuantPrimitive] | None = None,
    confirmations: list[QuantPrimitive] | None = None,
) -> dict[str, Any]:
    """Create a bounded candidate; broad combinatorial hyperopt is forbidden."""
    filters = filters or []
    confirmations = confirmations or []
    if len(filters) > 1 or len(confirmations) > 1:
        raise ValueError("CANDIDATE_COMPOSITION_LIMIT: max 1 FILTER + 1 CONFIRMATION")
    invalid = [item.primitive_id for item in [*filters, *confirmations] if item.role not in {"FILTER", "CONFIRMATION"}]
    if invalid:
        raise ValueError(f"CANDIDATE_COMPOSITION_ROLE_INVALID: {','.join(invalid)}")
    return {
        "base_event": base_event,
        "filters": [item.primitive_id for item in filters],
        "confirmations": [item.primitive_id for item in confirmations],
        "research_only": True,
        "promotion_authorized": False,
    }


__all__ = ["AblationComparison", "compare_paired_events", "compose_research_candidate"]
