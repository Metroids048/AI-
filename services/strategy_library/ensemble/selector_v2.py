"""Conflict-safe selector for point-in-time research proposals."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from pydantic import Field

from services.strategy_library.context import FrozenContract
from services.strategy_library.proposals import StrategyProposal


class SelectionResult(FrozenContract):
    status: str
    selected: StrategyProposal | None
    supporting_proposal_ids: tuple[str, ...]
    rejected_reasons: dict[str, str]
    selected_score: float | None = Field(default=None, ge=0, le=1)


class CandidateSelectorV2:
    """Select one unchanged proposal, rejecting unresolved directional conflict."""

    def __init__(self, *, conflict_margin: float = 0.08, minimum_selected_score: float = 0.58) -> None:
        self.conflict_margin = conflict_margin
        self.minimum_selected_score = minimum_selected_score

    @staticmethod
    def _score(proposal: StrategyProposal) -> float:
        confidence = proposal.confidence_components.values()
        confidence_score = (
            sum(confidence) / len(proposal.confidence_components) if proposal.confidence_components else 0.0
        )
        return min(
            1.0,
            (
                proposal.regime_fit
                + proposal.setup_quality
                + min(1.0, float(proposal.cost_adjusted_rr) / 2)
                + confidence_score
            )
            / 4,
        )

    def select(self, proposals: Sequence[StrategyProposal], *, now: datetime) -> SelectionResult:
        rejected = {proposal.proposal_id: "expired" for proposal in proposals if proposal.expires_at <= now}
        active = [proposal for proposal in proposals if proposal.expires_at > now]
        if not active:
            return SelectionResult(status="EMPTY", selected=None, supporting_proposal_ids=(), rejected_reasons=rejected)
        ranked = sorted(
            ((self._score(proposal), proposal) for proposal in active), key=lambda item: item[0], reverse=True
        )
        by_side = {
            side: [(score, proposal) for score, proposal in ranked if proposal.side == side]
            for side in ("long", "short")
        }
        if by_side["long"] and by_side["short"]:
            long_score, _ = by_side["long"][0]
            short_score, _ = by_side["short"][0]
            if abs(long_score - short_score) < self.conflict_margin:
                rejected.update({proposal.proposal_id: "opposing_direction_conflict" for proposal in active})
                return SelectionResult(
                    status="CONFLICT", selected=None, supporting_proposal_ids=(), rejected_reasons=rejected
                )
        winner_score, winner = ranked[0]
        if winner_score < self.minimum_selected_score:
            rejected.update({proposal.proposal_id: "selected_score_below_threshold" for proposal in active})
            return SelectionResult(
                status="NO_TRADE",
                selected=None,
                supporting_proposal_ids=(),
                rejected_reasons=rejected,
                selected_score=winner_score,
            )
        supporting = tuple(proposal.proposal_id for _, proposal in by_side[winner.side])
        for _, proposal in ranked:
            if proposal.side != winner.side:
                rejected[proposal.proposal_id] = "opposing_direction_lost"
        return SelectionResult(
            status="SELECTED",
            selected=winner,
            supporting_proposal_ids=supporting,
            rejected_reasons=rejected,
            selected_score=winner_score,
        )
