"""Initial exit geometry for each of the five policies. Point-in-time only.

No execution authority. See ADR-004.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from services.research.exit_policy_shadow.contracts import ExitPolicyId, Regime, Side


@dataclass(frozen=True)
class RegimeSelection:
    """Regime-aware policy selection outcome."""

    policy: ExitPolicyId
    reason: str
    fallback: bool = False
    """True when this selection is the UNKNOWN fail-closed path.

    Reported explicitly so an unclassifiable entry is never silently indistinguishable
    from a deliberate RANGE routing, which also resolves to CONTROL.
    """


def build_initial_geometry(
    *,
    policy: ExitPolicyId,
    side: Side,
    entry_price: Decimal,
    entry_context: dict[str, Decimal],
    regime: Regime,
) -> tuple[Decimal, Decimal | None]:
    """Return (stop_price, target_price) for this policy.

    Only depends on entry-time context. Two calls with identical arguments must
    produce identical geometry, regardless of what happens afterward.

    Returns
    -------
    stop_price
        The initial invalidation / stop-loss price.
    target_price
        The initial take-profit price. May be None for pure-runner policies.
    """
    if policy == ExitPolicyId.CURRENT_CONTROL:
        return _control_geometry(side, entry_price, entry_context)
    if policy == ExitPolicyId.ATR_ADAPTIVE:
        return _atr_adaptive_geometry(side, entry_price, entry_context)
    if policy == ExitPolicyId.STRUCTURE_INVALIDATION:
        return _structure_geometry(side, entry_price, entry_context)
    if policy == ExitPolicyId.SCALE_OUT_RUNNER:
        return _scale_out_geometry(side, entry_price, entry_context)
    if policy == ExitPolicyId.REGIME_AWARE:
        # Delegate to regime-specific sub-policy.
        selection = resolve_regime_policy(regime)
        return build_initial_geometry(
            policy=selection.policy,
            side=side,
            entry_price=entry_price,
            entry_context=entry_context,
            regime=regime,
        )
    raise ValueError(f"unknown policy {policy}")


def resolve_regime_policy(regime: Regime) -> RegimeSelection:
    """Select the concrete policy for this entry-time regime.

    This is the E (regime-aware) policy's dispatch logic. It must depend only on
    ``regime``, which was determined at entry time, never from post-exit bars.

    The mapping is **frozen in advance**. Picking each regime's policy after seeing
    which one earned most on the current sample would be selecting on the very outcome
    being measured, and at this sample size that is indistinguishable from fitting
    noise.

    C_STRUCTURE_INVALIDATION is deliberately excluded. It is not structure recognition:
    its stop and target are plain ATR multiples (see `_structure_geometry`), so routing
    entries into it would endorse an ATR proxy under a name implying structural
    analysis. It stays in the report as an independent benchmark, labelled as a proxy.
    """
    if regime == Regime.TREND:
        return RegimeSelection(
            policy=ExitPolicyId.SCALE_OUT_RUNNER,
            reason="TREND regime routes to D_SCALE_OUT_RUNNER (frozen mapping)",
        )
    if regime == Regime.RANGE:
        return RegimeSelection(
            policy=ExitPolicyId.CURRENT_CONTROL,
            reason="RANGE regime routes to A_CURRENT_CONTROL (frozen mapping)",
        )
    if regime == Regime.EXPANSION:
        return RegimeSelection(
            policy=ExitPolicyId.ATR_ADAPTIVE,
            reason="EXPANSION regime routes to B_ATR_ADAPTIVE (frozen mapping)",
        )
    # Unknown / unclassifiable regime fails closed to the baseline, visibly.
    return RegimeSelection(
        policy=ExitPolicyId.CURRENT_CONTROL,
        reason="UNKNOWN regime falls back to A_CURRENT_CONTROL (visible fail-closed)",
        fallback=True,
    )


# -------------------------------------------------------------------- policy A


def _control_geometry(side: Side, entry_price: Decimal, ctx: dict[str, Decimal]) -> tuple[Decimal, Decimal]:
    """Policy A: max(1.2*ATR14, price*0.0035) and 1.5R.

    Must reproduce the production formula exactly. This is the baseline for all
    comparisons.
    """
    atr = ctx.get("atr14", Decimal("0"))
    atr_term = Decimal("1.2") * atr
    pct_term = entry_price * Decimal("0.0035")
    stop_distance = max(atr_term, pct_term)

    take_profit_distance = Decimal("1.5") * stop_distance

    if side == "long":
        stop_price = entry_price - stop_distance
        target_price = entry_price + take_profit_distance
    else:
        stop_price = entry_price + stop_distance
        target_price = entry_price - take_profit_distance

    return stop_price, target_price


# -------------------------------------------------------------------- policy B


def _atr_adaptive_geometry(side: Side, entry_price: Decimal, ctx: dict[str, Decimal]) -> tuple[Decimal, Decimal]:
    """Policy B: Pure ATR-based geometry without a hard floor.

    Uses 1.5*ATR for stop, 2.5*ATR for target. Freezes these coefficients; P2-A is
    not a parameter-search exercise.
    """
    atr = ctx.get("atr14", Decimal("1"))
    if atr <= 0:
        atr = entry_price * Decimal("0.01")  # sanity fallback

    stop_distance = Decimal("1.5") * atr
    target_distance = Decimal("2.5") * atr

    if side == "long":
        return entry_price - stop_distance, entry_price + target_distance
    else:
        return entry_price + stop_distance, entry_price - target_distance


# -------------------------------------------------------------------- policy C


def _structure_geometry(side: Side, entry_price: Decimal, ctx: dict[str, Decimal]) -> tuple[Decimal, Decimal]:
    """Policy C: Structure invalidation + structural target.

    P2-A does not have live structure recognition; we approximate with an ATR buffer
    beyond a notional swing pivot. This is a placeholder for real structure logic that
    would belong in a production structure-analyzer.
    """
    atr = ctx.get("atr14", Decimal("1"))
    if atr <= 0:
        atr = entry_price * Decimal("0.01")

    # Structure invalidation is notionally "last swing low/high + buffer".
    # Here we proxy it as 2*ATR beyond entry.
    stop_distance = Decimal("2") * atr
    # Target is the next structural level (here 3*ATR as a stand-in).
    target_distance = Decimal("3") * atr

    if side == "long":
        return entry_price - stop_distance, entry_price + target_distance
    else:
        return entry_price + stop_distance, entry_price - target_distance


# -------------------------------------------------------------------- policy D


def _scale_out_geometry(side: Side, entry_price: Decimal, ctx: dict[str, Decimal]) -> tuple[Decimal, Decimal]:
    """Policy D: Initial geometry for a laddered runner exit.

    The stop is placed using the same structure or ATR logic; the first partial
    target is at 1R. Further legs are resolved by the replay engine.
    """
    atr = ctx.get("atr14", Decimal("1"))
    if atr <= 0:
        atr = entry_price * Decimal("0.01")

    stop_distance = Decimal("1.5") * atr
    first_target_distance = stop_distance  # 1R

    if side == "long":
        return entry_price - stop_distance, entry_price + first_target_distance
    else:
        return entry_price + stop_distance, entry_price - first_target_distance
