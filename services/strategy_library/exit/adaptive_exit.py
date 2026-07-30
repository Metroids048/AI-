"""Research-only adaptive exit plans resolved from confirmed fill prices."""

from __future__ import annotations

from decimal import Decimal

from pydantic import Field

from services.strategy_library.context import FrozenContract
from services.strategy_library.proposals import StrategyProposal


class AdaptiveExitTarget(FrozenContract):
    label: str
    price: Decimal = Field(gt=0)
    quantity_fraction: Decimal = Field(gt=0, le=1)


class AdaptiveExitPlan(FrozenContract):
    proposal_id: str
    side: str
    initial_stop: Decimal = Field(gt=0)
    targets: tuple[AdaptiveExitTarget, ...]
    time_exit_bars: int = Field(gt=0)
    time_exit_min_r: Decimal = Field(ge=0)
    trailing_activation_r: Decimal = Field(gt=0)
    trailing_atr_multiple: Decimal = Field(gt=0)


def build_adaptive_exit(
    proposal: StrategyProposal,
    *,
    filled_price: Decimal,
    atr: Decimal,
) -> AdaptiveExitPlan:
    """Resolve a proposal's partial exits from an actual exchange fill price."""

    if atr <= 0:
        raise ValueError("atr must be positive")
    stop = proposal.invalidation.stop_price
    if proposal.side == "long" and filled_price <= stop:
        raise ValueError("actual fill must stay above the long stop")
    if proposal.side == "short" and filled_price >= stop:
        raise ValueError("actual fill must stay below the short stop")
    proposed_entry = proposal.entry_trigger.reference_price
    proposed_risk = abs(proposed_entry - stop)
    actual_risk = abs(filled_price - stop)
    if proposed_risk <= 0:
        raise ValueError("proposal entry must stay on the valid side of its stop")
    direction = Decimal("1") if proposal.side == "long" else Decimal("-1")
    targets = tuple(
        AdaptiveExitTarget(
            label=target.label,
            price=filled_price + direction * actual_risk * (abs(target.price - proposed_entry) / proposed_risk),
            quantity_fraction=target.quantity_fraction,
        )
        for target in proposal.targets
    )
    return AdaptiveExitPlan(
        proposal_id=proposal.proposal_id,
        side=proposal.side,
        initial_stop=stop,
        targets=targets,
        time_exit_bars=8,
        time_exit_min_r=Decimal("0.5"),
        trailing_activation_r=Decimal("1.8"),
        trailing_atr_multiple=Decimal("2"),
    )
