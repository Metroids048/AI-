"""Pre-register the small Gate 5 strategy set from failure-decomposition evidence."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from services.validation.strategy_promotion import ResearchTrial, ResearchTrialRegistry

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DECOMPOSITION = ROOT / ".local/gate5-failure-decomposition.json"
DEFAULT_OUTPUT = ROOT / ".local/gate5-research-plan.json"
DEFAULT_REGISTRY = ROOT / ".local/gate5-research-trials.jsonl"


def hypotheses_from_evidence(decomposition: dict) -> tuple[ResearchTrial, ...]:
    ranking = decomposition.get("failure_root_cause_ranking", [])
    regime = decomposition.get("slices", {}).get("market_regime", {})
    if not ranking or ranking[0].get("root_cause") != "DIRECTION_FAILURE":
        raise ValueError("Gate 5 hypotheses require DIRECTION_FAILURE as the evidenced primary cause")
    expansion = regime.get("EXPANSION", {})
    if int(expansion.get("episodes", 0)) < 1 or float(expansion.get("net_before_funding_usdt", 0)) <= 0:
        raise ValueError("no positive expansion slice exists to justify an expansion-family hypothesis")
    prior_trials = 6
    return (
        ResearchTrial(
            hypothesis_id="G5-H1-EXPANSION-REGIME-V1",
            hypothesis_family="REGIME_SELECTION",
            exact_change=(
                "Evaluate the existing volatility_expansion_v1 closed-bar compression/volume expansion mechanism; "
                "do not alter stop, target, cost, risk, or acceptance thresholds."
            ),
            economic_rationale=(
                "The only positive real-lifecycle regime slice was EXPANSION (9 episodes, positive pre-funding PnL), "
                "while direction failure is the largest loss taxonomy."
            ),
            development_period="2023-01-29T00:00:00Z..2025-07-29T00:00:00Z",
            validation_period="2025-07-29T00:00:00Z..2026-01-29T00:00:00Z",
            final_holdout_accessed=False,
            created_before_result=True,
            number_of_prior_trials=prior_trials,
        ),
        ResearchTrial(
            hypothesis_id="G5-H2-BREAKOUT-RETEST-V1",
            hypothesis_family="ENTRY_STRUCTURE",
            exact_change=(
                "Evaluate the existing breakout_retest_v1 held-retest structure mechanism as a distinct entry family; "
                "do not tune the rejected E1 pullback parameters or alter geometry/cost/risk thresholds."
            ),
            economic_rationale=(
                "Expansion evidence supports testing a non-chasing structure confirmation mechanism, while the prior "
                "simple entry-timing hypothesis failed and is explicitly not reused."
            ),
            development_period="2023-01-29T00:00:00Z..2025-07-29T00:00:00Z",
            validation_period="2025-07-29T00:00:00Z..2026-01-29T00:00:00Z",
            final_holdout_accessed=False,
            created_before_result=True,
            number_of_prior_trials=prior_trials,
        ),
    )


def build_plan(*, decomposition_path: Path, registry_path: Path) -> dict:
    decomposition = json.loads(decomposition_path.read_text(encoding="utf-8"))
    registry = ResearchTrialRegistry(registry_path)
    trials = hypotheses_from_evidence(decomposition)
    for trial in trials:
        registry.register(trial)
    return {
        "status": "PRE_REGISTERED_DEVELOPMENT_ONLY",
        "holdout_accessed": False,
        "decomposition": str(decomposition_path),
        "trials": [trial.model_dump(mode="json") for trial in trials],
        "selection_bias_control": registry.selection_bias_control(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--decomposition", type=Path, default=DEFAULT_DECOMPOSITION)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    plan = build_plan(decomposition_path=args.decomposition, registry_path=args.registry)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": plan["status"], "trial_count": plan["selection_bias_control"]["trial_count"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
