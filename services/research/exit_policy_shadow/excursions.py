"""Direction-aware MFE/MAE computation.

Read-only. Excursions are measured from the real entry price over the bars the
policy actually held through, and are truncated at the exit bar: price action after
the exit was never available to that policy and must not inflate its MFE.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from services.research.exit_policy_shadow.contracts import Bar, ExcursionMetrics, Side


def compute_excursions(
    *,
    side: Side,
    entry_price: Decimal,
    quantity: Decimal,
    bars: list[Bar],
    risk_per_unit: Decimal | None,
) -> ExcursionMetrics:
    """Return direction-aware excursions over ``bars``.

    ``mfe_pct`` is the best favourable move as a positive percentage; ``mae_pct`` is
    the worst adverse move as a negative percentage. For a long, favourable is the
    bar high and adverse is the bar low; for a short the roles invert, so both sides
    are sign-symmetric by construction.

    ``risk_per_unit`` is ``abs(entry_price - initial_stop_price)``. When it is None or
    non-positive the R-denominated fields are None rather than a fabricated 0.
    """
    if entry_price <= 0:
        raise ValueError(f"entry_price must be > 0, got {entry_price}")

    best_favourable = Decimal("0")
    worst_adverse = Decimal("0")

    for bar in bars:
        if side == "long":
            favourable = bar.high - entry_price
            adverse = bar.low - entry_price
        else:
            favourable = entry_price - bar.low
            adverse = entry_price - bar.high

        if favourable > best_favourable:
            best_favourable = favourable
        if adverse < worst_adverse:
            worst_adverse = adverse

    mfe_pct = best_favourable / entry_price * Decimal("100")
    mae_pct = worst_adverse / entry_price * Decimal("100")

    mfe_r: Decimal | None = None
    mae_r: Decimal | None = None
    if risk_per_unit is not None and risk_per_unit > 0:
        mfe_r = best_favourable / risk_per_unit
        mae_r = worst_adverse / risk_per_unit

    return ExcursionMetrics(
        mfe_pct=mfe_pct,
        mae_pct=mae_pct,
        mfe_r=mfe_r,
        mae_r=mae_r,
        mfe_pnl_usdt=best_favourable * quantity,
        mae_pnl_usdt=worst_adverse * quantity,
    )


def compute_post_exit_remaining_mfe_r(
    *,
    side: Side,
    entry_price: Decimal,
    exit_time: datetime,
    horizon_end: datetime,
    bars: list[Bar],
    in_policy_mfe_pct: Decimal,
    risk_per_unit: Decimal | None,
) -> Decimal | None:
    """Additional favourable excursion, in R, after the policy exited.

    Measures how much *further* price moved in the entry's direction between the
    policy's exit and ``horizon_end``, beyond the best excursion the policy had already
    seen. Only bars strictly after ``exit_time`` and at or before ``horizon_end`` count.

    Research-only. This is deliberately *not* part of realised PnL: the policy had
    already exited and could not have captured it. It answers one narrow question —
    whether price kept going the entry's way — and is the correct place to look for exit
    truncation, because the policy-horizon capture ratio cannot see past its own exit.

    Returns None when risk is undefined or no post-exit bars exist, rather than 0, so
    "no continuation" stays distinguishable from "not measurable".
    """
    if risk_per_unit is None or risk_per_unit <= 0:
        return None

    post_exit = [bar for bar in bars if bar.time > exit_time and bar.time <= horizon_end]
    if not post_exit:
        return None

    best_favourable = Decimal("0")
    for bar in post_exit:
        favourable = bar.high - entry_price if side == "long" else entry_price - bar.low
        if favourable > best_favourable:
            best_favourable = favourable

    already_captured = in_policy_mfe_pct / Decimal("100") * entry_price
    additional = best_favourable - already_captured
    if additional <= 0:
        return Decimal("0")
    return additional / risk_per_unit
