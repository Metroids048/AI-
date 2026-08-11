"""Direction-aware MFE/MAE computation.

Read-only. Excursions are measured from the real entry price over the bars the
policy actually held through, and are truncated at the exit bar: price action after
the exit was never available to that policy and must not inflate its MFE.
"""

from __future__ import annotations

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
