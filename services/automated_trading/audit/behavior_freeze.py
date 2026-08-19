"""Exact behavioral regression gate for a frozen strategy replay.

The denominator is a unique closed-bar decision key, never scheduler cycles.
This catches signal starvation that ordinary unit tests and scheduler-health
checks cannot see.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

_REQUIRED_FIELDS = (
    "unique_closed_bar_decisions",
    "signals",
    "candidates",
    "reason_distribution",
    "directions",
    "stop_geometry",
    "target_geometry",
    "dry_run_intents",
)


@dataclass(frozen=True)
class BehaviorComparison:
    status: str
    baseline_candidate_rate: str
    observed_candidate_rate: str
    differences: dict[str, dict[str, Any]]


def _candidate_rate(payload: dict[str, Any]) -> Decimal:
    decisions = int(payload["unique_closed_bar_decisions"])
    if decisions <= 0:
        raise ValueError("unique_closed_bar_decisions must be positive")
    return Decimal(str(payload["candidates"])) / Decimal(decisions)


def compare_golden_behavior(*, baseline: dict[str, Any], observed: dict[str, Any]) -> BehaviorComparison:
    """Compare all stable strategy behavior fields exactly and fail on any drift."""
    missing = [field for field in _REQUIRED_FIELDS if field not in baseline or field not in observed]
    if missing:
        raise ValueError(f"behavior payload missing required fields: {', '.join(missing)}")
    baseline_rate = _candidate_rate(baseline)
    observed_rate = _candidate_rate(observed)
    differences = {
        field: {"baseline": baseline[field], "observed": observed[field]}
        for field in _REQUIRED_FIELDS
        if baseline[field] != observed[field]
    }
    if baseline_rate != observed_rate:
        differences["candidate_rate"] = {
            "baseline": format(baseline_rate.normalize(), "f"),
            "observed": format(observed_rate.normalize(), "f"),
            "denominator": "unique_closed_bar_decisions",
        }
    return BehaviorComparison(
        status="BEHAVIOR_REGRESSION=FAIL" if differences else "BEHAVIOR_REGRESSION=PASS",
        baseline_candidate_rate=format(baseline_rate.normalize(), "f"),
        observed_candidate_rate=format(observed_rate.normalize(), "f"),
        differences=differences,
    )
