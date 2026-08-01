"""Single pure proposal pipeline shared by runtime shadow and replay."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from services.strategy_library.candidates.failed_breakout_reversal_v1 import evaluate_failed_breakout_reversal
from services.strategy_library.candidates.range_sweep_reversion_v1 import evaluate_range_sweep_reversion
from services.strategy_library.candidates.trend_pullback_v2 import evaluate_trend_pullback_v2
from services.strategy_library.canonical import canonical_hash
from services.strategy_library.context import FrozenContract, MarketContext
from services.strategy_library.ensemble.selector_v2 import CandidateSelectorV2, SelectionResult
from services.strategy_library.proposals import StrategyProposal
from services.strategy_library.regime.scorer_v2 import RegimeScore, RegimeScorerV2

CandidateEvaluator = Callable[[MarketContext, RegimeScore], StrategyProposal | None]


class ProposalPipelineResult(FrozenContract):
    """Complete, serializable output of one point-in-time proposal evaluation."""

    pipeline_version: str
    context_hash: str
    regime: RegimeScore
    proposals: tuple[StrategyProposal, ...]
    selection: SelectionResult
    rejection_reasons: dict[str, str]

    def for_strategy(self, strategy_id: str) -> StrategyProposal | None:
        """Return the candidate for a replay lane without rerunning candidates."""

        return next((proposal for proposal in self.proposals if proposal.strategy_id == strategy_id), None)


PIPELINE_VERSION = "proposal-pipeline-v1"
PROPOSAL_CONTEXT_WINDOW_LENGTHS: dict[str, int] = {
    "1m": 2,
    "5m": 2,
    "15m": 80,
    "1h": 80,
    "4h": 80,
}


def _evaluators() -> tuple[tuple[str, CandidateEvaluator], ...]:
    return (
        ("trend_pullback_v2", evaluate_trend_pullback_v2),
        ("range_sweep_reversion_v1", evaluate_range_sweep_reversion),
        ("failed_breakout_reversal_v1", evaluate_failed_breakout_reversal),
    )


def run_proposal_pipeline(
    context: MarketContext,
    *,
    now: datetime | None = None,
    selector: CandidateSelectorV2 | None = None,
    candidate_ids: frozenset[str] | None = None,
) -> ProposalPipelineResult:
    """Evaluate all candidates exactly once from one immutable market context."""

    regime = RegimeScorerV2().score(context)
    proposals: list[StrategyProposal] = []
    rejection_reasons: dict[str, str] = {}
    available_ids = frozenset(strategy_id for strategy_id, _ in _evaluators())
    if candidate_ids is not None and not candidate_ids <= available_ids:
        unknown = ",".join(sorted(candidate_ids - available_ids))
        raise ValueError(f"unknown proposal candidate ids: {unknown}")
    for strategy_id, evaluator in _evaluators():
        if candidate_ids is not None and strategy_id not in candidate_ids:
            continue
        proposal = evaluator(context, regime)
        if proposal is None:
            rejection_reasons[strategy_id] = "candidate_conditions_not_met"
            continue
        proposals.append(proposal)
    evaluation_time = (now or context.decision_time).astimezone(UTC)
    selection = (selector or CandidateSelectorV2()).select(proposals, now=evaluation_time)
    rejection_reasons.update(selection.rejected_reasons)
    return ProposalPipelineResult(
        pipeline_version=PIPELINE_VERSION,
        context_hash=canonical_hash({"context": context}),
        regime=regime,
        proposals=tuple(proposals),
        selection=selection,
        rejection_reasons=rejection_reasons,
    )


def proposal_pipeline_payload(result: ProposalPipelineResult) -> dict[str, Any]:
    """Convert a result into the JSON payload stored on a shadow decision."""

    return result.model_dump(mode="json")
