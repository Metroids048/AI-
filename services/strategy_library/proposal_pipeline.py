"""Single pure proposal pipeline shared by runtime shadow and replay."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from pydantic import Field

from services.strategy_library.candidates.breakout_continuation_v1 import evaluate_breakout_continuation
from services.strategy_library.candidates.breakout_retest_v1 import evaluate_breakout_retest
from services.strategy_library.candidates.donchian_breakout_retest_v1 import evaluate_donchian_breakout_retest
from services.strategy_library.candidates.failed_breakout_reversal_v1 import evaluate_failed_breakout_reversal
from services.strategy_library.candidates.htf_trend_continuation_v1 import evaluate_htf_trend_continuation
from services.strategy_library.candidates.loss_aware_trend_pullback_v1 import evaluate_loss_aware_trend_pullback
from services.strategy_library.candidates.momentum_continuation_v1 import evaluate_momentum_continuation
from services.strategy_library.candidates.range_sweep_reversion_v1 import evaluate_range_sweep_reversion
from services.strategy_library.candidates.trend_pullback_v2 import evaluate_trend_pullback_v2
from services.strategy_library.candidates.volatility_expansion_v1 import evaluate_volatility_expansion
from services.strategy_library.canonical import canonical_hash
from services.strategy_library.context import FrozenContract, MarketContext
from services.strategy_library.ensemble.selector_v2 import CandidateSelectorV2, SelectionResult
from services.strategy_library.proposals import StrategyProposal
from services.strategy_library.regime.scorer_v2 import RegimeScore, RegimeScorerV2

CandidateEvaluator = Callable[[MarketContext, RegimeScore], StrategyProposal | None]


class CandidateEvaluationError(FrozenContract):
    """Safe, serializable failure for one research candidate evaluation."""

    error_class: str
    safe_message: str


class ProposalPipelineResult(FrozenContract):
    """Complete, serializable output of one point-in-time proposal evaluation."""

    pipeline_version: str
    context_hash: str
    regime: RegimeScore
    proposals: tuple[StrategyProposal, ...]
    selection: SelectionResult
    rejection_reasons: dict[str, str]
    strategy_versions: dict[str, str]
    evaluation_errors: dict[str, CandidateEvaluationError] = Field(default_factory=dict)

    def for_strategy(self, strategy_id: str) -> StrategyProposal | None:
        """Return the candidate for a replay lane without rerunning candidates."""

        return next((proposal for proposal in self.proposals if proposal.strategy_id == strategy_id), None)


PIPELINE_VERSION = "proposal-pipeline-v1"
RESEARCH_CANDIDATE_IDS: tuple[str, ...] = (
    "loss_aware_trend_pullback_v1",
    "trend_pullback_v2",
    "range_sweep_reversion_v1",
    "failed_breakout_reversal_v1",
    "breakout_continuation_v1",
)
RESEARCH_CANDIDATE_VERSIONS: dict[str, str] = {
    "htf_trend_continuation_v1": "1.0.0-generation-1",
    "breakout_retest_v1": "1.0.0-generation-1",
    "loss_aware_trend_pullback_v1": "1.0.0-live-loss-hypothesis",
    "trend_pullback_v2": "2.0.0-research",
    "range_sweep_reversion_v1": "1.0.0-research",
    "failed_breakout_reversal_v1": "1.0.0-research",
    "breakout_continuation_v1": "1.0.0-research",
    "volatility_expansion_v1": "1.0.0-generation-3",
    "donchian_breakout_retest_v1": "1.0.0-generation-4",
    "momentum_continuation_v1": "1.0.0-generation-5",
}
PROPOSAL_CONTEXT_WINDOW_LENGTHS: dict[str, int] = {
    "1m": 2,
    "5m": 2,
    "15m": 80,
    "1h": 80,
    "4h": 80,
}


def _evaluators() -> tuple[tuple[str, CandidateEvaluator], ...]:
    return (
        ("htf_trend_continuation_v1", evaluate_htf_trend_continuation),
        ("breakout_retest_v1", evaluate_breakout_retest),
        ("loss_aware_trend_pullback_v1", evaluate_loss_aware_trend_pullback),
        ("trend_pullback_v2", evaluate_trend_pullback_v2),
        ("range_sweep_reversion_v1", evaluate_range_sweep_reversion),
        ("failed_breakout_reversal_v1", evaluate_failed_breakout_reversal),
        ("breakout_continuation_v1", evaluate_breakout_continuation),
        ("volatility_expansion_v1", evaluate_volatility_expansion),
        ("donchian_breakout_retest_v1", evaluate_donchian_breakout_retest),
        ("momentum_continuation_v1", evaluate_momentum_continuation),
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
    evaluation_errors: dict[str, CandidateEvaluationError] = {}
    available_ids = frozenset(strategy_id for strategy_id, _ in _evaluators())
    if candidate_ids is not None and not candidate_ids <= available_ids:
        unknown = ",".join(sorted(candidate_ids - available_ids))
        raise ValueError(f"unknown proposal candidate ids: {unknown}")
    for strategy_id, evaluator in _evaluators():
        if candidate_ids is not None and strategy_id not in candidate_ids:
            continue
        try:
            proposal = evaluator(context, regime)
        except Exception as exc:  # noqa: BLE001
            safe_message = " ".join(str(exc).split())[:240] or "candidate evaluation failed"
            evaluation_errors[strategy_id] = CandidateEvaluationError(
                error_class=type(exc).__name__,
                safe_message=safe_message,
            )
            continue
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
        strategy_versions={
            strategy_id: RESEARCH_CANDIDATE_VERSIONS.get(strategy_id, "unknown")
            for strategy_id in available_ids
            if candidate_ids is None or strategy_id in candidate_ids
        },
        evaluation_errors=evaluation_errors,
    )


def proposal_pipeline_payload(result: ProposalPipelineResult) -> dict[str, Any]:
    """Convert a result into the JSON payload stored on a shadow decision."""

    return result.model_dump(mode="json")
