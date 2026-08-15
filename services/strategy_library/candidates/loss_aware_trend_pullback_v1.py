"""Loss-attribution-driven trend pullback candidate.

The live V2 audit found stop-dominated losses and a post-cost PF below one.
This candidate tests a stricter, trend-aligned structural pullback entry. It is
research-only; it never changes the active Testnet lane or execution geometry.
"""

from __future__ import annotations

from decimal import Decimal

from services.strategy_library.candidates.trend_pullback_v2 import (
    TrendPullbackConfig,
    evaluate_trend_pullback_v2,
)
from services.strategy_library.context import MarketContext
from services.strategy_library.proposals import StrategyProposal
from services.strategy_library.regime.scorer_v2 import RegimeScore

STRATEGY_ID = "loss_aware_trend_pullback_v1"
STRATEGY_VERSION = "1.0.0-live-loss-hypothesis"


def evaluate_loss_aware_trend_pullback(
    context: MarketContext,
    regime: RegimeScore,
    *,
    config: TrendPullbackConfig | None = None,
) -> StrategyProposal | None:
    """Use stricter trend evidence before accepting structural pullback risk."""
    base = config or TrendPullbackConfig(
        minimum_trend_score=0.65,
        maximum_entry_distance_atr=Decimal("1.5"),
    )
    proposal = evaluate_trend_pullback_v2(context, regime, config=base)
    if proposal is None:
        return None
    return proposal.model_copy(
        update={
            "proposal_id": proposal.proposal_id.replace("trend_pullback_v2", STRATEGY_ID),
            "strategy_id": STRATEGY_ID,
            "strategy_version": STRATEGY_VERSION,
            "reasons": (*proposal.reasons, "live_stop_loss_dominance_filter"),
        }
    )


__all__ = ["STRATEGY_ID", "STRATEGY_VERSION", "evaluate_loss_aware_trend_pullback"]
