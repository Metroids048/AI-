"""Conclude the bounded Gate 5 loop without opening the Final Holdout on failure."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from services.validation.strategy_promotion import ResearchTrialRegistry

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DEVELOPMENT = ROOT / "artifacts/gate5-bounded-development-2026-08-19/proposal-research-report.json"
DEFAULT_REGISTRY = ROOT / ".local/gate5-research-trials.jsonl"
DEFAULT_OUTPUT = ROOT / ".local/gate5-research-conclusion.json"
HYPOTHESIS_CANDIDATES = {
    "G5-H1-EXPANSION-REGIME-V1": "volatility_expansion_v1",
    "G5-H2-BREAKOUT-RETEST-V1": "breakout_retest_v1",
}


def development_failures(result: dict[str, Any]) -> list[str]:
    portfolio = result["portfolio"]
    failures: list[str] = []
    if float(portfolio["net_expectancy"]) <= 0:
        failures.append("net_expectancy_not_positive")
    if float(portfolio["profit_factor"]) < 1.35:
        failures.append("profit_factor_below_existing_promotion_contract")
    if float(portfolio["max_drawdown"]) > 0.30:
        failures.append("max_drawdown_exceeds_existing_promotion_contract")
    if not bool(portfolio["promotion_observations_complete"]):
        failures.append("cost_observations_incomplete")
    for symbol, metrics in result["symbols"].items():
        if float(metrics["net_expectancy"]) <= 0 or float(metrics["profit_factor"]) < 1.35:
            failures.append(f"{symbol}:not_independently_eligible")
    return failures


def conclude(*, development: dict[str, Any], registry: ResearchTrialRegistry) -> dict[str, Any]:
    outcomes: list[dict[str, Any]] = []
    for trial in registry.read_all():
        candidate_id = HYPOTHESIS_CANDIDATES.get(trial.hypothesis_id)
        if candidate_id is None:
            raise ValueError(f"no candidate binding for registered hypothesis {trial.hypothesis_id}")
        result = development["results"].get(candidate_id)
        if result is None:
            raise ValueError(f"missing development result for {candidate_id}")
        failures = development_failures(result)
        outcomes.append(
            {
                "hypothesis_id": trial.hypothesis_id,
                "candidate_id": candidate_id,
                "status": "HYPOTHESIS_FAILED" if failures else "DEVELOPMENT_PASSED",
                "failures": failures,
            }
        )
    passed = [outcome for outcome in outcomes if outcome["status"] == "DEVELOPMENT_PASSED"]
    if passed:
        status = "VALIDATION_REQUIRED"
        reason = "only development-passing candidates may enter validation; Final Holdout remains sealed"
    else:
        status = "NO_VALIDATED_EDGE"
        reason = (
            "all evidence-backed pre-registered families failed Development; the remaining nominal budget is not used "
            "for parameter fishing, stop widening, or a repeat of E1"
        )
    return {
        "status": status,
        "reason": reason,
        "final_holdout_accessed": False,
        "validation_accessed": False,
        "promotion_attempted": False,
        "entry_authority": "NONE_PENDING_PRODUCTION_STRATEGY",
        "outcomes": outcomes,
        "selection_bias_control": registry.selection_bias_control(),
        "research_budget_state": "EVIDENCE_EXHAUSTED_NO_DEFENSIBLE_NEW_FAMILY" if not passed else "VALIDATION_PENDING",
        "remaining_families_not_registered": [
            "STOP_EXIT_GEOMETRY: prohibited because direction quality is not acceptable and only 2 episodes support stop geometry attribution",
            "SYMBOL_SIDE_SPECIALIZATION: not registered because both BTC/ETH and both sides are negative in the authoritative lifecycle slices",
            "E1_PARAMETER_VARIANTS: prohibited because E1 was rejected and repeat tuning would contaminate selection",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--development", type=Path, default=DEFAULT_DEVELOPMENT)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    development = json.loads(args.development.read_text(encoding="utf-8"))
    conclusion = conclude(development=development, registry=ResearchTrialRegistry(args.registry))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(conclusion, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": conclusion["status"], "holdout": conclusion["final_holdout_accessed"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
