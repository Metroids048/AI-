"""Aggregation and comparison metrics for P2-A.

Computes PolicyAggregate for one slice of outcomes and produces the three business
verdict questions.
"""

from __future__ import annotations

from decimal import Decimal
from statistics import median

from services.research.exit_policy_shadow.contracts import (
    MINIMUM_TRADES_FOR_VERDICT,
    MINIMUM_TRADES_PER_REGIME_SLICE,
    ExcursionMetrics,
    ExitPolicyId,
    PolicyAggregate,
    Q1Evidence,
    Q2Evidence,
    Regime,
    RegimeSliceComparison,
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


def compute_q1_evidence(
    entry_excursions: list[ExcursionMetrics],
    *,
    horizon_hours: int,
) -> Q1Evidence:
    """Summarise entry-level excursions. Always computed, verdict-independent.

    Reported even when the sample is too small for a verdict: the numbers are what the
    data shows, and suppressing them would hide the only available evidence. They are
    labelled OBSERVED_ONLY at the call site so they cannot be quoted as a conclusion.
    """
    mfe_r = [e.mfe_r for e in entry_excursions if e.mfe_r is not None]
    mae_r = [e.mae_r for e in entry_excursions if e.mae_r is not None]

    mean_mfe = sum(mfe_r, Decimal("0")) / Decimal(len(mfe_r)) if mfe_r else None
    mean_mae = sum(mae_r, Decimal("0")) / Decimal(len(mae_r)) if mae_r else None
    ratio: Decimal | None = None
    if mean_mfe is not None and mean_mae is not None and mean_mae != 0:
        ratio = mean_mfe / abs(mean_mae)

    return Q1Evidence(
        sample_count=len(entry_excursions),
        positive_mfe_count=sum(1 for value in mfe_r if value > 0),
        mean_mfe_r=mean_mfe,
        median_mfe_r=Decimal(str(median(mfe_r))) if mfe_r else None,
        mean_mae_r=mean_mae,
        median_mae_r=Decimal(str(median(mae_r))) if mae_r else None,
        mfe_mae_ratio=ratio,
        horizon_hours=horizon_hours,
    )


def answer_q1_entry_has_edge(
    entry_excursions: list[ExcursionMetrics],
    *,
    horizon_hours: int = 24,
) -> tuple[Verdict, Q1Evidence]:
    """Q1: Do the real entries themselves have edge?

    Takes **entry-level** excursions measured over one fixed horizon per entry,
    deliberately *not* the per-policy excursions used for exit comparison. Feeding
    all (entry, policy) rows here would count each entry once per policy and would
    truncate each measurement at that policy's exit, so the answer would conflate
    entry quality with the exit choice being evaluated. Entry quality has to be
    measured before any exit decision is applied.

    One entry contributes exactly one observation.
    """
    evidence = compute_q1_evidence(entry_excursions, horizon_hours=horizon_hours)

    if evidence.sample_count < MINIMUM_TRADES_FOR_VERDICT:
        return Verdict.INSUFFICIENT_SAMPLE, evidence
    if evidence.mean_mfe_r is None or evidence.mean_mae_r is None:
        return Verdict.NOT_SUPPORTED, evidence

    if evidence.mfe_mae_ratio is not None and evidence.mfe_mae_ratio > Decimal("1.5"):
        return Verdict.SUPPORTED, evidence
    return Verdict.NOT_SUPPORTED, evidence


MATERIAL_POST_EXIT_MFE_R = Decimal("0.5")
"""Post-exit continuation counts as material at half the initial risk or more.

Expressed in the trade's own R so it introduces no price- or symbol-specific constant.
A frozen reporting threshold: it must not be tuned to make leakage appear or disappear.
"""

CONCENTRATION_SHARE_THRESHOLD = Decimal("0.5")
"""An improvement is "concentrated" when >50% of it comes from the single best trade.

Guards the specific failure the Q2 contract calls out: an alternative policy whose
after-cost advantage rests on one or two outliers is not evidence of a better exit rule.
"""


def compute_q2_evidence(
    control_outcomes: list[ShadowOutcome],
    *,
    entry_excursions: list[ExcursionMetrics],
    all_outcomes: list[ShadowOutcome],
) -> Q2Evidence:
    """Assemble Q2 evidence across both horizons, keeping them separate."""
    capture_ratios = [o.profit_capture_ratio for o in control_outcomes if o.profit_capture_ratio is not None]

    fixed_mfe = [e.mfe_r for e in entry_excursions if e.mfe_r is not None]
    mean_fixed_mfe = sum(fixed_mfe, Decimal("0")) / Decimal(len(fixed_mfe)) if fixed_mfe else None

    post_exit = [o.post_exit_remaining_mfe_r for o in control_outcomes if o.post_exit_remaining_mfe_r is not None]
    material = sum(1 for value in post_exit if value >= MATERIAL_POST_EXIT_MFE_R)

    control_expectancy = _net_expectancy(control_outcomes)
    best_policy, best_expectancy, concentrated = _best_alternative(
        all_outcomes,
        control_expectancy=control_expectancy,
    )

    return Q2Evidence(
        sample_count=len(control_outcomes),
        median_policy_horizon_capture=(Decimal(str(median(capture_ratios))) if capture_ratios else None),
        capture_sample_count=len(capture_ratios),
        mean_fixed_horizon_mfe_r=mean_fixed_mfe,
        median_post_exit_remaining_mfe_r=(Decimal(str(median(post_exit))) if post_exit else None),
        post_exit_sample_count=len(post_exit),
        trades_with_material_post_exit_mfe=material,
        best_alternative_policy=best_policy,
        best_alternative_net_expectancy=best_expectancy,
        control_net_expectancy=control_expectancy,
        alternative_improvement_is_concentrated=concentrated,
    )


def answer_q2_exit_leakage(
    control_outcomes: list[ShadowOutcome],
    *,
    entry_excursions: list[ExcursionMetrics],
    all_outcomes: list[ShadowOutcome],
) -> tuple[Verdict, Q2Evidence]:
    """Q2: Does the CONTROL exit truncate profit the entry actually offered?

    SUPPORTED requires all three of:

    1. at least ``MINIMUM_TRADES_FOR_VERDICT`` CONTROL trades;
    2. CONTROL frequently leaving material favourable movement on the table *after* it
       exited (measured over the fixed post-entry horizon, never divided into the
       policy-horizon capture);
    3. some non-CONTROL policy improving after-cost expectancy without that
       improvement being concentrated in a couple of outlier trades.

    Requirement 3 is what separates "the exit rule is wrong" from "one trade would have
    run further". A low capture ratio alone is not sufficient: a tight target
    mechanically produces low capture even when no better exit existed.
    """
    evidence = compute_q2_evidence(
        control_outcomes,
        entry_excursions=entry_excursions,
        all_outcomes=all_outcomes,
    )

    if evidence.sample_count < MINIMUM_TRADES_FOR_VERDICT:
        return Verdict.INSUFFICIENT_SAMPLE, evidence

    if evidence.post_exit_sample_count == 0:
        return Verdict.NOT_SUPPORTED, evidence

    frequent_continuation = (
        Decimal(evidence.trades_with_material_post_exit_mfe) / Decimal(evidence.post_exit_sample_count)
    ) > Decimal("0.5")

    alternative_helps = (
        evidence.best_alternative_policy is not None
        and evidence.best_alternative_net_expectancy is not None
        and evidence.control_net_expectancy is not None
        and evidence.best_alternative_net_expectancy > evidence.control_net_expectancy
        and evidence.alternative_improvement_is_concentrated is False
    )

    if frequent_continuation and alternative_helps:
        return Verdict.SUPPORTED, evidence
    return Verdict.NOT_SUPPORTED, evidence


def _net_expectancy(outcomes: list[ShadowOutcome]) -> Decimal | None:
    if not outcomes:
        return None
    return sum((o.net_pnl_usdt for o in outcomes), Decimal("0")) / Decimal(len(outcomes))


def _best_alternative(
    all_outcomes: list[ShadowOutcome],
    *,
    control_expectancy: Decimal | None,
) -> tuple[ExitPolicyId | None, Decimal | None, bool | None]:
    """Find the best non-CONTROL policy and whether its edge is outlier-driven.

    Concentration is assessed per position against CONTROL, so it answers "would this
    alternative still win without its single best trade?".
    """
    by_policy: dict[ExitPolicyId, list[ShadowOutcome]] = {}
    for outcome in all_outcomes:
        if outcome.policy == ExitPolicyId.CURRENT_CONTROL:
            continue
        by_policy.setdefault(outcome.policy, []).append(outcome)

    best_policy: ExitPolicyId | None = None
    best_expectancy: Decimal | None = None
    for policy, outcomes in by_policy.items():
        expectancy = _net_expectancy(outcomes)
        if expectancy is None:
            continue
        if best_expectancy is None or expectancy > best_expectancy:
            best_policy, best_expectancy = policy, expectancy

    if best_policy is None or control_expectancy is None or best_expectancy is None:
        return best_policy, best_expectancy, None
    if best_expectancy <= control_expectancy:
        return best_policy, best_expectancy, None

    control_by_position = {
        o.position_id: o.net_pnl_usdt for o in all_outcomes if o.policy == ExitPolicyId.CURRENT_CONTROL
    }
    deltas = [
        outcome.net_pnl_usdt - control_by_position[outcome.position_id]
        for outcome in by_policy[best_policy]
        if outcome.position_id in control_by_position
    ]
    total_delta = sum(deltas, Decimal("0"))
    if not deltas or total_delta <= 0:
        return best_policy, best_expectancy, None

    concentrated = (max(deltas) / total_delta) > CONCENTRATION_SHARE_THRESHOLD
    return best_policy, best_expectancy, concentrated


def compare_policies_by_regime(
    outcomes: list[ShadowOutcome],
) -> dict[Regime, RegimeSliceComparison]:
    """Per-regime policy comparison, one entry per regime present in the sample.

    Each slice carries its own verdict. A slice below
    ``MINIMUM_TRADES_PER_REGIME_SLICE`` distinct entries still reports its observed
    best policy — the number is real — but its verdict is INSUFFICIENT_SLICE_SAMPLE so
    it cannot be quoted as a winner.

    The slice size counts **distinct entries**, not outcome rows: every entry appears
    once per policy, so counting rows would multiply an apparent 2-trade slice into 10.
    """
    by_regime: dict[Regime, list[ShadowOutcome]] = {}
    for outcome in outcomes:
        by_regime.setdefault(outcome.regime, []).append(outcome)

    comparisons: dict[Regime, RegimeSliceComparison] = {}
    for regime, slice_outcomes in by_regime.items():
        entry_count = len({o.position_id for o in slice_outcomes})
        aggregates = aggregate_outcomes(slice_outcomes, slice_key=regime.value)

        best_policy: ExitPolicyId | None = None
        best_expectancy: Decimal | None = None
        for policy, aggregate in aggregates.items():
            if aggregate.net_expectancy_usdt is None:
                continue
            if best_expectancy is None or aggregate.net_expectancy_usdt > best_expectancy:
                best_policy, best_expectancy = policy, aggregate.net_expectancy_usdt

        verdict = (
            Verdict.SUPPORTED if entry_count >= MINIMUM_TRADES_PER_REGIME_SLICE else Verdict.INSUFFICIENT_SLICE_SAMPLE
        )
        comparisons[regime] = RegimeSliceComparison(
            regime=regime,
            trade_count=entry_count,
            verdict=verdict,
            observed_best_policy=best_policy,
            observed_best_net_expectancy=best_expectancy,
            aggregates=tuple(aggregates[policy] for policy in sorted(aggregates, key=lambda p: p.value)),
        )
    return comparisons


def answer_q3_policy_by_regime(
    outcomes: list[ShadowOutcome],
) -> tuple[Verdict, str, dict[Regime, RegimeSliceComparison]]:
    """Q3: Should different regimes use different exit policies?

    UNKNOWN is excluded from the hypothesis test entirely: it is the abstention bucket,
    so "UNKNOWN prefers a different policy" is not evidence about any market regime.
    """
    comparisons = compare_policies_by_regime(outcomes)
    total_entries = len({o.position_id for o in outcomes})

    if total_entries < MINIMUM_TRADES_FOR_VERDICT:
        return (
            Verdict.INSUFFICIENT_SAMPLE,
            f"only {total_entries} entries, need {MINIMUM_TRADES_FOR_VERDICT}",
            comparisons,
        )

    qualified = {
        regime: comparison
        for regime, comparison in comparisons.items()
        if regime != Regime.UNKNOWN and comparison.verdict != Verdict.INSUFFICIENT_SLICE_SAMPLE
    }
    if len(qualified) < 2:
        return (
            Verdict.INSUFFICIENT_SLICE_SAMPLE,
            (
                f"only {len(qualified)} classified regime(s) reached "
                f"{MINIMUM_TRADES_PER_REGIME_SLICE} entries; need 2 to compare"
            ),
            comparisons,
        )

    winners = {comparison.observed_best_policy for comparison in qualified.values()}
    detail = "; ".join(
        f"{regime.value}→"
        f"{comparison.observed_best_policy.value if comparison.observed_best_policy else 'N/A'}"
        f"({comparison.observed_best_net_expectancy:+.2f})"
        if comparison.observed_best_net_expectancy is not None
        else f"{regime.value}→N/A"
        for regime, comparison in sorted(qualified.items(), key=lambda kv: kv[0].value)
    )
    if len(winners) > 1:
        return Verdict.SUPPORTED, detail, comparisons
    return Verdict.NOT_SUPPORTED, f"all qualified regimes prefer the same policy; {detail}", comparisons
