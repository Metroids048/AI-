"""Aggregation and comparison metrics for P2-A.

Computes PolicyAggregate for one slice of outcomes and produces the three business
verdict questions.
"""

from __future__ import annotations

from decimal import Decimal
from statistics import median

from services.research.exit_policy_shadow.contracts import (
    MINIMUM_TRADES_FOR_VERDICT,
    ExcursionMetrics,
    ExitPolicyId,
    PolicyAggregate,
    ShadowOutcome,
    Verdict,
)


def aggregate_outcomes(outcomes: list[ShadowOutcome], *, slice_key: str) -> dict[ExitPolicyId, PolicyAggregate]:
    """Aggregate outcomes by policy.

    Returns a dict mapping each policy present in ``outcomes`` to its aggregate.
    """
    by_policy: dict[ExitPolicyId, list[ShadowOutcome]] = {}
    for out in outcomes:
        by_policy.setdefault(out.policy, []).append(out)

    aggregates: dict[ExitPolicyId, PolicyAggregate] = {}
    for policy, outs in by_policy.items():
        aggregates[policy] = _compute_aggregate(outs, policy=policy, slice_key=slice_key)
    return aggregates


def _compute_aggregate(outcomes: list[ShadowOutcome], *, policy: ExitPolicyId, slice_key: str) -> PolicyAggregate:
    """Compute aggregate metrics for one policy over one slice."""
    if not outcomes:
        return PolicyAggregate(
            policy=policy,
            slice_key=slice_key,
            trade_count=0,
            win_rate=None,
            gross_expectancy_usdt=None,
            net_expectancy_usdt=None,
            profit_factor=None,
            avg_r=None,
            median_r=None,
            max_drawdown_usdt=Decimal("0"),
            avg_mfe_r=None,
            avg_mae_r=None,
            median_profit_capture_ratio=None,
            capture_ratio_sample_count=0,
            fee_drag_usdt=Decimal("0"),
            avg_holding_time_minutes=None,
            median_holding_time_minutes=None,
            ambiguous_intrabar_count=0,
        )

    count = len(outcomes)
    winners = [o for o in outcomes if o.net_pnl_usdt > 0]
    losers = [o for o in outcomes if o.net_pnl_usdt <= 0]
    win_rate = Decimal(len(winners)) / Decimal(count) if count else None

    gross_sum = sum((o.gross_pnl_usdt for o in outcomes), Decimal("0"))
    net_sum = sum((o.net_pnl_usdt for o in outcomes), Decimal("0"))
    gross_exp = gross_sum / Decimal(count)
    net_exp = net_sum / Decimal(count)

    gross_wins = sum((o.gross_pnl_usdt for o in winners), Decimal("0"))
    gross_losses = abs(sum((o.gross_pnl_usdt for o in losers), Decimal("0")))
    pf = gross_wins / gross_losses if gross_losses > 0 else None

    r_vals = [o.r_multiple for o in outcomes if o.r_multiple is not None]
    avg_r = sum(r_vals, Decimal("0")) / Decimal(len(r_vals)) if r_vals else None
    median_r = Decimal(str(median(r_vals))) if r_vals else None

    running_equity = Decimal("0")
    peak = Decimal("0")
    max_dd = Decimal("0")
    for o in outcomes:
        running_equity += o.net_pnl_usdt
        if running_equity > peak:
            peak = running_equity
        dd = peak - running_equity
        if dd > max_dd:
            max_dd = dd

    mfe_r_vals = [o.excursions.mfe_r for o in outcomes if o.excursions.mfe_r is not None]
    avg_mfe_r = sum(mfe_r_vals, Decimal("0")) / Decimal(len(mfe_r_vals)) if mfe_r_vals else None

    mae_r_vals = [o.excursions.mae_r for o in outcomes if o.excursions.mae_r is not None]
    avg_mae_r = sum(mae_r_vals, Decimal("0")) / Decimal(len(mae_r_vals)) if mae_r_vals else None

    capture_ratios = [o.profit_capture_ratio for o in outcomes if o.profit_capture_ratio is not None]
    median_capture = Decimal(str(median(capture_ratios))) if capture_ratios else None

    total_cost = sum((o.total_cost_usdt for o in outcomes), Decimal("0"))

    holding_times = [o.holding_time_minutes for o in outcomes]
    avg_hold = sum(holding_times, Decimal("0")) / Decimal(len(holding_times))
    median_hold = Decimal(str(median(holding_times)))

    ambig_count = sum(1 for o in outcomes if o.ambiguous_intrabar)

    return PolicyAggregate(
        policy=policy,
        slice_key=slice_key,
        trade_count=count,
        win_rate=win_rate,
        gross_expectancy_usdt=gross_exp,
        net_expectancy_usdt=net_exp,
        profit_factor=pf,
        avg_r=avg_r,
        median_r=median_r,
        max_drawdown_usdt=max_dd,
        avg_mfe_r=avg_mfe_r,
        avg_mae_r=avg_mae_r,
        median_profit_capture_ratio=median_capture,
        capture_ratio_sample_count=len(capture_ratios),
        fee_drag_usdt=total_cost,
        avg_holding_time_minutes=avg_hold,
        median_holding_time_minutes=median_hold,
        ambiguous_intrabar_count=ambig_count,
    )


def answer_q1_entry_has_edge(
    entry_excursions: list[ExcursionMetrics],
) -> tuple[Verdict, str]:
    """Q1: Do the real entries themselves have edge?

    Takes **entry-level** excursions measured over one fixed horizon per entry,
    deliberately *not* the per-policy excursions used for exit comparison. Feeding
    all (entry, policy) rows here would count each entry once per policy and would
    truncate each measurement at that policy's exit, so the answer would conflate
    entry quality with the exit choice being evaluated. Entry quality has to be
    measured before any exit decision is applied.

    One entry contributes exactly one observation.
    """
    sample = len(entry_excursions)
    if sample < MINIMUM_TRADES_FOR_VERDICT:
        return (
            Verdict.INSUFFICIENT_SAMPLE,
            f"only {sample} entries, need {MINIMUM_TRADES_FOR_VERDICT}",
        )

    mfe_r = [e.mfe_r for e in entry_excursions if e.mfe_r is not None]
    mae_r = [e.mae_r for e in entry_excursions if e.mae_r is not None]

    if not mfe_r or not mae_r:
        return Verdict.NOT_SUPPORTED, "no R-denominated excursions available"

    avg_mfe = sum(mfe_r, Decimal("0")) / Decimal(len(mfe_r))
    avg_mae = sum(mae_r, Decimal("0")) / Decimal(len(mae_r))

    if avg_mfe > abs(avg_mae) * Decimal("1.5"):
        return (
            Verdict.SUPPORTED,
            f"n={sample}, avg MFE={avg_mfe:.2f}R vs avg MAE={avg_mae:.2f}R (MFE > 1.5x|MAE|)",
        )
    return (
        Verdict.NOT_SUPPORTED,
        f"n={sample}, avg MFE={avg_mfe:.2f}R vs avg MAE={avg_mae:.2f}R (no clear MFE advantage)",
    )


def answer_q2_exit_leakage(
    control_outcomes: list[ShadowOutcome],
) -> tuple[Verdict, str]:
    """Q2: Does CONTROL exit leak profit (high MFE, low capture)?"""
    if len(control_outcomes) < MINIMUM_TRADES_FOR_VERDICT:
        return (
            Verdict.INSUFFICIENT_SAMPLE,
            f"Only {len(control_outcomes)} CONTROL trades, need {MINIMUM_TRADES_FOR_VERDICT}",
        )

    ratios = [o.profit_capture_ratio for o in control_outcomes if o.profit_capture_ratio is not None]
    if not ratios:
        return Verdict.NOT_SUPPORTED, "No positive-MFE trades to compute capture ratio"

    med_capture = median(ratios)
    # Leakage exists if median capture < 50%.
    if med_capture < Decimal("0.5"):
        evidence = f"median profit capture ratio={med_capture:.2%}; majority of potential profit lost"
        return Verdict.SUPPORTED, evidence
    else:
        evidence = f"median profit capture ratio={med_capture:.2%}; exit captures >50% of MFE"
        return Verdict.NOT_SUPPORTED, evidence


def answer_q3_policy_by_regime(
    outcomes: list[ShadowOutcome],
) -> tuple[Verdict, str]:
    """Q3: Should different regimes use different exit policies?"""
    from collections import defaultdict

    if len(outcomes) < MINIMUM_TRADES_FOR_VERDICT:
        return Verdict.INSUFFICIENT_SAMPLE, f"Only {len(outcomes)} total trades"

    # Group by regime.
    by_regime: dict[str, list[ShadowOutcome]] = defaultdict(list)
    for o in outcomes:
        by_regime[o.regime.value].append(o)

    # For each regime with sufficient sample, find the best policy.
    regime_winners: dict[str, tuple[ExitPolicyId, Decimal]] = {}
    for regime_label, reg_outs in by_regime.items():
        if len(reg_outs) < 5:
            continue
        by_pol = aggregate_outcomes(reg_outs, slice_key=regime_label)
        best_policy: ExitPolicyId | None = None
        best_exp = Decimal("-999999")
        for pol, agg in by_pol.items():
            if agg.net_expectancy_usdt is not None and agg.net_expectancy_usdt > best_exp:
                best_exp = agg.net_expectancy_usdt
                best_policy = pol
        if best_policy:
            regime_winners[regime_label] = (best_policy, best_exp)

    if len(regime_winners) < 2:
        return Verdict.INSUFFICIENT_SAMPLE, "Need at least 2 regimes with 5+ trades each"

    # If different regimes prefer different policies, regime-awareness has merit.
    unique_winners = {pol for pol, _ in regime_winners.values()}
    if len(unique_winners) > 1:
        evidence = "; ".join(f"{reg}→{pol.value}({exp:+.2f})" for reg, (pol, exp) in regime_winners.items())
        return Verdict.SUPPORTED, evidence
    else:
        evidence = "All regimes prefer the same policy"
        return Verdict.NOT_SUPPORTED, evidence
