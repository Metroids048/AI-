"""Pure StrategyProposal -> V2 single-target projection helpers."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from services.strategy_library.proposals import StrategyProposal, TargetRule


@dataclass(frozen=True)
class V2SingleTargetProjection:
    entry_reference_price: Decimal
    stop_price: Decimal
    primary_target_price: Decimal
    stop_distance: Decimal
    take_profit_distance: Decimal
    target_label: str


def _target_key(target: TargetRule, *, entry: Decimal) -> tuple[Decimal, Decimal]:
    return target.quantity_fraction, abs(target.price - entry)


def project_single_target(proposal: StrategyProposal) -> V2SingleTargetProjection:
    """Choose the largest allocation, breaking ties by farthest target."""

    entry = proposal.entry_trigger.reference_price
    target = max(proposal.targets, key=lambda item: _target_key(item, entry=entry))
    stop_distance = abs(entry - proposal.invalidation.stop_price)
    take_distance = abs(target.price - entry)
    if stop_distance <= 0 or take_distance <= 0:
        raise ValueError("proposal geometry must have positive stop and target distances")
    if proposal.side == "long" and (proposal.invalidation.stop_price >= entry or target.price <= entry):
        raise ValueError("long proposal geometry is not directional")
    if proposal.side == "short" and (proposal.invalidation.stop_price <= entry or target.price >= entry):
        raise ValueError("short proposal geometry is not directional")
    return V2SingleTargetProjection(
        entry_reference_price=entry,
        stop_price=proposal.invalidation.stop_price,
        primary_target_price=target.price,
        stop_distance=stop_distance,
        take_profit_distance=take_distance,
        target_label=target.label,
    )


def risk_per_trade_for_score(score: Decimal) -> Decimal:
    """Map selector confidence to the frozen aggressive risk tiers."""

    if score < Decimal("0.58"):
        raise ValueError("score below production selection floor")
    if score < Decimal("0.66"):
        return Decimal("0.02")
    if score < Decimal("0.74"):
        return Decimal("0.04")
    if score < Decimal("0.82"):
        return Decimal("0.07")
    return Decimal("0.10")
