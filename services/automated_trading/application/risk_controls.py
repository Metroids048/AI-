"""Bounded R1/R2/R3 controls for the Testnet sampling lane.

The functions in this module are pure so the decision snapshot can preserve
the exact inputs and replay the admission/protection calculations offline.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

R2_MIN_THEORETICAL_NET_PAYOFF = Decimal("1.15")
DEFAULT_TAKER_FEE_BPS = Decimal("5")
DEFAULT_ROUND_TRIP_SLIPPAGE_BPS = Decimal("6")


@dataclass(frozen=True)
class CostGateResult:
    cost_r: Decimal
    theoretical_net_payoff: Decimal
    planned_target_r: Decimal
    commission_r: Decimal
    funding_r: Decimal
    slippage_r: Decimal
    passed: bool
    reason: str


def calculate_cost_gate(
    *,
    entry_price: Decimal,
    stop_distance: Decimal,
    take_profit_distance: Decimal | None,
    commission_bps: Decimal = DEFAULT_TAKER_FEE_BPS * Decimal("2"),
    funding_bps: Decimal = Decimal("0"),
    slippage_bps: Decimal = DEFAULT_ROUND_TRIP_SLIPPAGE_BPS,
    minimum_net_payoff: Decimal = R2_MIN_THEORETICAL_NET_PAYOFF,
) -> CostGateResult:
    """Compute the target-relative post-cost payoff gate.

    Cost is expressed in R using the stop distance.  No fixed ``cost_R``
    threshold is used; the admission test is the requested mechanical net
    payoff formula.
    """
    if entry_price <= 0 or stop_distance <= 0:
        raise ValueError("entry_price and stop_distance must be positive")
    planned_target_r = (take_profit_distance / stop_distance) if take_profit_distance is not None else Decimal("0")
    commission_r = (commission_bps / Decimal("10000")) * entry_price / stop_distance
    funding_r = (funding_bps / Decimal("10000")) * entry_price / stop_distance
    slippage_r = (slippage_bps / Decimal("10000")) * entry_price / stop_distance
    cost_r = commission_r + funding_r + slippage_r
    theoretical_net_payoff = (planned_target_r - cost_r) / (Decimal("1") + cost_r)
    passed = theoretical_net_payoff >= minimum_net_payoff
    return CostGateResult(
        cost_r=cost_r,
        theoretical_net_payoff=theoretical_net_payoff,
        planned_target_r=planned_target_r,
        commission_r=commission_r,
        funding_r=funding_r,
        slippage_r=slippage_r,
        passed=passed,
        reason="OK" if passed else "NO_TRADE_COST_INEFFICIENT",
    )


@dataclass(frozen=True)
class ProfitProtectionDecision:
    policy: str
    trigger_r: Decimal | None
    lock_r: Decimal | None
    stop_price: Decimal | None


def p1_profit_protection(
    *,
    direction: str,
    entry_price: Decimal,
    original_stop_price: Decimal,
    mark_price: Decimal,
) -> ProfitProtectionDecision:
    """Resolve the P1 stop only when it is a one-way tightening."""
    if direction not in {"long", "short"}:
        raise ValueError("direction must be long or short")
    risk = abs(entry_price - original_stop_price)
    if risk <= 0:
        return ProfitProtectionDecision("P1", None, None, None)
    mfe_r = (mark_price - entry_price) / risk if direction == "long" else (entry_price - mark_price) / risk
    lock_r = Decimal("0")
    trigger_r: Decimal | None = None
    if mfe_r >= Decimal("1.00"):
        trigger_r, lock_r = Decimal("1.00"), Decimal("0.40")
    elif mfe_r >= Decimal("0.60"):
        trigger_r, lock_r = Decimal("0.60"), Decimal("0.05")
    if trigger_r is None:
        return ProfitProtectionDecision("P1", None, None, None)
    stop_price = entry_price + lock_r * risk if direction == "long" else entry_price - lock_r * risk
    return ProfitProtectionDecision("P1", trigger_r, lock_r, stop_price)


def shadow_profit_protection(
    *,
    policy: str,
    direction: str,
    entry_price: Decimal,
    original_stop_price: Decimal,
    mark_price: Decimal,
) -> ProfitProtectionDecision:
    """Evaluate P2/P3 without any exchange side effect."""
    risk = abs(entry_price - original_stop_price)
    if risk <= 0:
        return ProfitProtectionDecision(policy, None, None, None)
    mfe_r = (mark_price - entry_price) / risk if direction == "long" else (entry_price - mark_price) / risk
    if policy == "P2":
        if mfe_r >= Decimal("1.25"):
            trigger, lock = Decimal("1.25"), Decimal("0.50")
        elif mfe_r >= Decimal("0.75"):
            trigger, lock = Decimal("0.75"), Decimal("0.05")
        else:
            return ProfitProtectionDecision(policy, None, None, None)
    elif policy == "P3":
        if mfe_r < Decimal("1.00"):
            return ProfitProtectionDecision(policy, None, None, None)
        trigger, lock = Decimal("1.00"), Decimal("0.10")
    else:
        raise ValueError(f"unsupported shadow policy: {policy}")
    stop_price = entry_price + lock * risk if direction == "long" else entry_price - lock * risk
    return ProfitProtectionDecision(policy, trigger, lock, stop_price)
