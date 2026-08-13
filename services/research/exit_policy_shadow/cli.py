"""CLI entry point for P2-A exit-policy shadow evaluation.

Usage:
    python -m services.research.exit_policy_shadow.cli

Loads real testnet_sampling_v2 entries, replays under five exit policies,
computes aggregates and verdicts, writes a report to stdout and a JSON artifact.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

from services.research.exit_policy_shadow.contracts import (
    MINIMUM_TRADES_FOR_VERDICT,
    MINIMUM_TRADES_PER_REGIME_SLICE,
    Bar,
    ExcursionMetrics,
    ExitPolicyId,
    PolicyAggregate,
    Regime,
    RegimeSliceComparison,
    ShadowOutcome,
    Verdict,
)
from services.research.exit_policy_shadow.excursions import (
    compute_excursions,
    compute_post_exit_remaining_mfe_r,
)
from services.research.exit_policy_shadow.loader import (
    build_entry_context,
    classify_entry_regime,
    load_bars,
    load_real_entries,
)
from services.research.exit_policy_shadow.metrics import (
    aggregate_outcomes,
    answer_q1_entry_has_edge,
    answer_q2_exit_leakage,
    answer_q3_policy_by_regime,
)
from services.research.exit_policy_shadow.policies import build_initial_geometry
from services.research.exit_policy_shadow.regime import (
    CLASSIFIER_VERSION,
    RegimeLabelResult,
)
from services.research.exit_policy_shadow.replay import replay_entry_under_policy
from services.strategy_library.regime.scorer_v2 import SCORER_VERSION

# Fixed observation horizon for entry-level excursions (Q1). Covers the longest real
# holding time in the sample, so entry quality is measured over a window every entry
# actually had, independent of any policy's exit.
ENTRY_HORIZON_HOURS = 24

# Reported as a proxy, not as structure recognition: policy C's stop and target are
# plain ATR multiples. The enum value stays unchanged so existing artifacts remain
# comparable; only the display label carries the caveat.
POLICY_DISPLAY_NAMES = {
    ExitPolicyId.STRUCTURE_INVALIDATION: "C_STRUCTURE_PROXY",
}


def _display(policy: ExitPolicyId) -> str:
    return POLICY_DISPLAY_NAMES.get(policy, policy.value)


def _as_display_names(text: str) -> str:
    """Apply the proxy caveat to policy ids embedded in a prose evidence string."""
    for policy, display in POLICY_DISPLAY_NAMES.items():
        text = text.replace(policy.value, display)
    return text


def main() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    db_path = repo_root / ".local_paper_console.db"
    if not db_path.exists():
        print(f"❌ Database not found: {db_path}", file=sys.stderr)
        sys.exit(1)

    print("=" * 80)
    print("P2-A EXIT POLICY SHADOW EVALUATION")
    print("=" * 80)
    print()

    # Load real entries.
    entries = load_real_entries(db_path)
    if not entries:
        print("❌ No testnet_sampling_v2 CLOSED positions found.", file=sys.stderr)
        sys.exit(1)

    print(f"✅ Loaded {len(entries)} real CONTROL entries.")
    print()

    # Determine sample window.
    start = min(e.fill_timestamp for e in entries)
    end = max(e.fill_timestamp for e in entries)
    print(f"SAMPLE_START={start.isoformat()}")
    print(f"SAMPLE_END={end.isoformat()}")
    print(f"TOTAL_TRADES={len(entries)}")
    print()

    # Split by symbol and side.
    btc_entries = [e for e in entries if e.symbol == "BTC/USDT"]
    eth_entries = [e for e in entries if e.symbol == "ETH/USDT"]
    long_entries = [e for e in entries if e.side == "long"]
    short_entries = [e for e in entries if e.side == "short"]
    print(f"BTC_TRADES={len(btc_entries)}")
    print(f"ETH_TRADES={len(eth_entries)}")
    print(f"LONG_TRADES={len(long_entries)}")
    print(f"SHORT_TRADES={len(short_entries)}")
    print()

    if len(entries) < MINIMUM_TRADES_FOR_VERDICT:
        print(f"⚠️  Only {len(entries)} trades; need {MINIMUM_TRADES_FOR_VERDICT} for stable verdicts.")
        print()

    # Replay each entry under each policy.
    policies = [
        ExitPolicyId.CURRENT_CONTROL,
        ExitPolicyId.ATR_ADAPTIVE,
        ExitPolicyId.STRUCTURE_INVALIDATION,
        ExitPolicyId.SCALE_OUT_RUNNER,
        ExitPolicyId.REGIME_AWARE,
    ]

    all_outcomes: list[ShadowOutcome] = []
    entry_excursions: list[ExcursionMetrics] = []
    regime_labels: dict[str, RegimeLabelResult] = {}
    replayed_entries = 0
    skipped_no_bars = 0
    skipped_no_atr = 0

    for entry in entries:
        # 1m is the highest resolution available, which minimises how often a bar
        # brackets both stop and target and therefore how often the intrabar
        # ambiguity rule has to be invoked.
        bar_end = entry.fill_timestamp + timedelta(days=7)
        bars_1m = load_bars(
            db_path,
            symbol=entry.symbol,
            timeframe="1m",
            start=entry.fill_timestamp,
            end=bar_end,
        )

        if not bars_1m:
            print(f"WARN  no 1m bars for {entry.symbol} after {entry.fill_timestamp}; skipping")
            skipped_no_bars += 1
            continue

        # Entry-time ATR14 on the 15m decision timeframe, computed strictly from
        # bars that closed at or before the decision bar. This is the same input
        # the production sampling rule used, so policy A reproduces the real
        # geometry instead of a fallback.
        entry_context = build_entry_context(db_path, symbol=entry.symbol, decision_bar=entry.decision_bar_timestamp)
        if entry_context is None:
            print(
                f"WARN  no point-in-time ATR14 for {entry.symbol} at "
                f"{entry.decision_bar_timestamp}; skipping entry {entry.position_id}"
            )
            skipped_no_atr += 1
            continue

        # Entry-time regime, classified strictly from bars closed at or before the
        # decision bar. This is what makes the Q3 slice meaningful: before P2-A2 the
        # label was hardcoded UNKNOWN, so E always fell back to CONTROL and every
        # regime comparison was vacuous.
        label = classify_entry_regime(db_path, symbol=entry.symbol, decision_bar=entry.decision_bar_timestamp)
        regime_labels[entry.position_id] = label

        # Entry-level excursions over one fixed horizon, measured before any exit
        # policy is applied. Q1 ("does the entry have edge") must not be answered
        # from per-policy excursions, which are truncated at each policy's own exit.
        control_stop, _ = build_initial_geometry(
            policy=ExitPolicyId.CURRENT_CONTROL,
            side=entry.side,
            entry_price=entry.average_fill_price,
            entry_context=entry_context,
            regime=label.regime,
        )
        risk_per_unit = abs(entry.average_fill_price - control_stop)
        horizon_end = entry.fill_timestamp + timedelta(hours=ENTRY_HORIZON_HOURS)
        horizon_bars = [bar for bar in bars_1m if bar.time <= horizon_end]
        entry_excursions.append(
            compute_excursions(
                side=entry.side,
                entry_price=entry.average_fill_price,
                quantity=entry.filled_quantity,
                bars=horizon_bars,
                risk_per_unit=risk_per_unit,
            )
        )

        for policy in policies:
            outcome = replay_entry_under_policy(
                entry=entry,
                bars=bars_1m,
                policy=policy,
                regime=label.regime,
                entry_context=entry_context,
            )
            all_outcomes.append(_with_post_exit_continuation(outcome, bars=bars_1m, horizon_end=horizon_end))
        replayed_entries += 1

    print(f"REPLAYED_ENTRIES={replayed_entries}")
    print(f"SKIPPED_NO_BARS={skipped_no_bars}")
    print(f"SKIPPED_NO_POINT_IN_TIME_ATR={skipped_no_atr}")
    print(f"OUTCOME_ROWS={len(all_outcomes)}")
    print()

    if not all_outcomes:
        print("BLOCKED: no entry could be replayed with point-in-time inputs.")
        sys.exit(2)

    _print_regime_distribution(regime_labels)

    # Aggregate overall.
    overall = aggregate_outcomes(all_outcomes, slice_key="overall")
    print("=" * 80)
    print("OVERALL RESULTS")
    print("=" * 80)
    print()
    _print_comparison_table(overall)
    print()

    # Aggregate by symbol.
    btc_outcomes = [o for o in all_outcomes if o.symbol == "BTC/USDT"]
    eth_outcomes = [o for o in all_outcomes if o.symbol == "ETH/USDT"]

    if btc_outcomes:
        btc_agg = aggregate_outcomes(btc_outcomes, slice_key="BTC/USDT")
        print("=" * 80)
        print("BTC/USDT RESULTS")
        print("=" * 80)
        print()
        _print_comparison_table(btc_agg)
        print()

    if eth_outcomes:
        eth_agg = aggregate_outcomes(eth_outcomes, slice_key="ETH/USDT")
        print("=" * 80)
        print("ETH/USDT RESULTS")
        print("=" * 80)
        print()
        _print_comparison_table(eth_agg)
        print()

    # Answer the three business questions.
    _print_per_trade_detail(all_outcomes)

    print("=" * 80)
    print("BUSINESS VERDICTS")
    print("=" * 80)
    print()

    q1_verdict, q1_evidence = answer_q1_entry_has_edge(entry_excursions, horizon_hours=ENTRY_HORIZON_HOURS)
    print(f"Q1_ENTRY_HAS_EDGE={q1_verdict.value}")
    print(f"Q1_OBSERVED_EVIDENCE={q1_evidence.describe()}")
    if q1_verdict == Verdict.INSUFFICIENT_SAMPLE:
        print("Q1_NOTE=OBSERVED_ONLY — the numbers above are real but are NOT a conclusion.")
    print()

    control_outcomes = [o for o in all_outcomes if o.policy == ExitPolicyId.CURRENT_CONTROL]
    q2_verdict, q2_evidence = answer_q2_exit_leakage(
        control_outcomes,
        entry_excursions=entry_excursions,
        all_outcomes=all_outcomes,
    )
    print(f"Q2_EXIT_LEAKAGE={q2_verdict.value}")
    print(f"Q2_OBSERVED_EVIDENCE={_as_display_names(q2_evidence.describe())}")
    print(
        "Q2_NOTE=policy-horizon capture and fixed-24h opportunity are separate measurements; "
        "they are reported side by side and never divided into one another."
    )
    if q2_verdict == Verdict.INSUFFICIENT_SAMPLE:
        print("Q2_NOTE=OBSERVED_ONLY — the numbers above are real but are NOT a conclusion.")
    print()

    q3_verdict, q3_evidence, regime_comparisons = answer_q3_policy_by_regime(all_outcomes)
    print(f"Q3_POLICY_BY_REGIME={q3_verdict.value}")
    print(f"Q3_OBSERVED_EVIDENCE={q3_evidence}")
    print()
    _print_regime_slices(regime_comparisons)
    best_by_regime = _print_best_by_regime(regime_comparisons)
    print()

    # Rank policies.
    if overall:
        best_policy, best_exp = _find_best_policy(overall)
        control_exp = (
            overall[ExitPolicyId.CURRENT_CONTROL].net_expectancy_usdt
            if ExitPolicyId.CURRENT_CONTROL in overall
            else None
        )
        control_rank = _rank_policy(overall, ExitPolicyId.CURRENT_CONTROL)

        print(f"CURRENT_CONTROL_RANK={control_rank}/{len(overall)}")
        print(
            f"CURRENT_CONTROL_NET_EXPECTANCY={control_exp:.2f} USDT"
            if control_exp is not None
            else "CURRENT_CONTROL_NET_EXPECTANCY=N/A"
        )
        print(f"BEST_OBSERVED_POLICY={_display(best_policy)}")
        print(f"BEST_OBSERVED_NET_EXPECTANCY={best_exp:.2f} USDT")
        print()
        _print_policy_results(overall)
        print()

    next_step = _next_research_step(
        total_entries=replayed_entries,
        q1_verdict=q1_verdict,
        q2_verdict=q2_verdict,
        q3_verdict=q3_verdict,
    )
    final_status = (
        "P2A_RESEARCH_COMPLETE"
        if replayed_entries >= MINIMUM_TRADES_FOR_VERDICT and q3_verdict != Verdict.INSUFFICIENT_SLICE_SAMPLE
        else "RESEARCH_INFRA_COMPLETE_DATA_ACCUMULATING"
    )
    print(f"NEXT_RESEARCH_STEP={next_step}")
    print("PRODUCTION_CHANGED=NO")
    print(f"FINAL_STATUS={final_status}")
    print()

    print("SAMPLE_LIMITATIONS=")
    if len(entries) < MINIMUM_TRADES_FOR_VERDICT:
        print(f"  - Only {len(entries)} trades; statistical power is low. Verdicts may flip with more data.")
    thin_slices = [
        comparison
        for comparison in regime_comparisons.values()
        if comparison.verdict == Verdict.INSUFFICIENT_SLICE_SAMPLE
    ]
    for comparison in sorted(thin_slices, key=lambda c: c.regime.value):
        print(
            f"  - {comparison.regime.value} slice has {comparison.trade_count} entries "
            f"(<{MINIMUM_TRADES_PER_REGIME_SLICE}); no winner may be declared for it."
        )
    print()

    # Write the evidence artifact under docs/audits, not into the source package:
    # it is regenerated output, not code.
    audits_dir = repo_root / "docs" / "audits"
    audits_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = audits_dir / "2026-08-11-p2a-exit-policy-shadow-results.json"
    artifact = {
        "sample_start": start.isoformat(),
        "sample_end": end.isoformat(),
        "total_entries": len(entries),
        "replayed_entries": replayed_entries,
        "btc_entries": len(btc_entries),
        "eth_entries": len(eth_entries),
        "long_entries": len(long_entries),
        "short_entries": len(short_entries),
        "regime_scorer_version": SCORER_VERSION,
        "regime_classifier_version": CLASSIFIER_VERSION,
        "regime_distribution": _regime_distribution(regime_labels),
        "regime_labels": {
            position_id: label.model_dump(mode="json") for position_id, label in sorted(regime_labels.items())
        },
        "q1_verdict": q1_verdict.value,
        "q1_evidence": q1_evidence.model_dump(mode="json"),
        "q2_verdict": q2_verdict.value,
        "q2_evidence": q2_evidence.model_dump(mode="json"),
        "q3_verdict": q3_verdict.value,
        "q3_evidence": q3_evidence,
        "q3_by_regime": {
            regime.value: {
                "trade_count": comparison.trade_count,
                "verdict": comparison.verdict.value,
                "observed_best_policy": (
                    _display(comparison.observed_best_policy) if comparison.observed_best_policy else None
                ),
                "observed_best_net_expectancy": (
                    float(comparison.observed_best_net_expectancy)
                    if comparison.observed_best_net_expectancy is not None
                    else None
                ),
                "policies": [_agg_to_dict(aggregate) for aggregate in comparison.aggregates],
            }
            for regime, comparison in sorted(regime_comparisons.items(), key=lambda kv: kv[0].value)
        },
        "best_by_regime": best_by_regime,
        "overall": {pol.value: _agg_to_dict(agg) for pol, agg in overall.items() if agg is not None},
        "next_research_step": next_step,
        "production_changed": "NO",
        "final_status": final_status,
    }
    with open(artifact_path, "w", encoding="utf-8") as f:
        json.dump(artifact, f, indent=2, default=str)

    print(f"✅ Artifact written: {artifact_path}")
    print()
    print("=" * 80)
    print(final_status)
    print("=" * 80)


def _with_post_exit_continuation(
    outcome: ShadowOutcome,
    *,
    bars: list[Bar],
    horizon_end: datetime,
) -> ShadowOutcome:
    """Attach post-exit continuation to ``outcome``.

    Measured after the policy's own exit, so it can only be computed once the replay
    has produced that exit. It never feeds realised PnL.
    """
    if not outcome.legs:
        return outcome

    exit_time = max(leg.filled_at for leg in outcome.legs)
    risk_per_unit = abs(outcome.entry_price - outcome.initial_stop_price)
    remaining = compute_post_exit_remaining_mfe_r(
        side=outcome.side,
        entry_price=outcome.entry_price,
        exit_time=exit_time,
        horizon_end=horizon_end,
        bars=bars,
        in_policy_mfe_pct=outcome.excursions.mfe_pct,
        risk_per_unit=risk_per_unit if risk_per_unit > 0 else None,
    )
    return outcome.model_copy(update={"post_exit_remaining_mfe_r": remaining})


def _regime_distribution(labels: dict[str, RegimeLabelResult]) -> dict[str, int]:
    """Count entries per regime, including regimes with zero observations."""
    counts = {regime.value: 0 for regime in Regime}
    for label in labels.values():
        counts[label.regime.value] += 1
    return counts


def _print_regime_distribution(labels: dict[str, RegimeLabelResult]) -> None:
    """Print the entry-time regime distribution and the classifier provenance."""
    print("=" * 80)
    print("ENTRY-TIME REGIME DISTRIBUTION (point-in-time)")
    print("=" * 80)
    print()
    print(f"REGIME_SCORER_VERSION={SCORER_VERSION}")
    print(f"REGIME_CLASSIFIER_VERSION={CLASSIFIER_VERSION}")
    print()

    counts = _regime_distribution(labels)
    total = len(labels)
    for regime in ("TREND", "RANGE", "EXPANSION", "UNKNOWN"):
        print(f"{regime}_COUNT={counts[regime]}")
    unknown_rate = Decimal(counts["UNKNOWN"]) / Decimal(total) if total else Decimal("0")
    print(f"UNKNOWN_RATE={unknown_rate:.1%}")
    print()

    # Distinguish low-confidence abstention from unusable data: they call for different
    # follow-up, and collapsing them would hide a data-collection problem.
    forced = [label for label in labels.values() if label.data_quality_reason]
    print(f"UNKNOWN_FROM_DATA_QUALITY={len(forced)}")
    print(f"UNKNOWN_FROM_LOW_CONFIDENCE={counts['UNKNOWN'] - len(forced)}")
    if forced:
        for reason in sorted({label.data_quality_reason for label in forced}):
            print(f"  data_quality_reason={reason}")
    print()

    headers = ["POSITION", "REGIME", "DIR", "UP", "DOWN", "RANGE", "EXPANS", "UNSTAB"]
    print(" | ".join(f"{header:>10}" for header in headers))
    print("-" * (11 * len(headers) + (len(headers) - 1) * 3))
    for position_id, label in sorted(labels.items(), key=lambda kv: kv[0]):
        row = [
            position_id[:10],
            label.regime.value[:10],
            label.trend_direction.value[:10],
            f"{label.trend_up:.3f}",
            f"{label.trend_down:.3f}",
            f"{label.range:.3f}",
            f"{label.expansion:.3f}",
            f"{label.unstable:.3f}",
        ]
        print(" | ".join(f"{cell:>10}" for cell in row))
    print()


def _print_regime_slices(comparisons: dict[Regime, RegimeSliceComparison]) -> None:
    """Print the per-regime policy comparison, one table per regime."""
    for regime, comparison in sorted(comparisons.items(), key=lambda kv: kv[0].value):
        print("=" * 80)
        print(f"REGIME {regime.value}: {comparison.trade_count} entries — {comparison.verdict.value}")
        print("=" * 80)
        print()
        _print_comparison_table({aggregate.policy: aggregate for aggregate in comparison.aggregates})
        print()


def _print_best_by_regime(comparisons: dict[Regime, RegimeSliceComparison]) -> dict[str, str]:
    """Print BEST_*_POLICY lines and return them for the artifact.

    A thin slice still shows its observed best, tagged INSUFFICIENT_SLICE_SAMPLE, so the
    number is visible but cannot be read as a winner.
    """
    result: dict[str, str] = {}
    for regime in (Regime.TREND, Regime.RANGE, Regime.EXPANSION):
        comparison = comparisons.get(regime)
        key = f"BEST_{regime.value}_POLICY"
        if comparison is None or comparison.observed_best_policy is None:
            value = "N/A_NO_TRADES"
        elif comparison.verdict == Verdict.INSUFFICIENT_SLICE_SAMPLE:
            value = (
                f"{_display(comparison.observed_best_policy)} "
                f"(observed only, n={comparison.trade_count}, INSUFFICIENT_SLICE_SAMPLE)"
            )
        else:
            value = _display(comparison.observed_best_policy)
        result[key] = value
        print(f"{key}={value}")
    return result


def _next_research_step(
    *,
    total_entries: int,
    q1_verdict: Verdict,
    q2_verdict: Verdict,
    q3_verdict: Verdict,
) -> str:
    """Decide the next research step from the verdicts, per the frozen case table."""
    if total_entries < MINIMUM_TRADES_FOR_VERDICT:
        return "DATA_ACCUMULATION"
    if q3_verdict == Verdict.INSUFFICIENT_SLICE_SAMPLE:
        return "DATA_ACCUMULATION"
    if q1_verdict == Verdict.NOT_SUPPORTED:
        return "P2B_ENTRY_STRATEGY_COMPARISON"
    if q1_verdict == Verdict.SUPPORTED and q2_verdict == Verdict.SUPPORTED:
        return "EXIT_POLICY_REFINEMENT"
    if q1_verdict == Verdict.SUPPORTED and q2_verdict == Verdict.NOT_SUPPORTED:
        return "KEEP_CONTROL_AND_COLLECT_MORE"
    return "DATA_ACCUMULATION"


def _print_policy_results(aggregates: dict[ExitPolicyId, PolicyAggregate]) -> None:
    """Print the five *_RESULT lines the review contract asks for."""
    labels = {
        ExitPolicyId.CURRENT_CONTROL: "CONTROL_RESULT",
        ExitPolicyId.ATR_ADAPTIVE: "ATR_RESULT",
        ExitPolicyId.STRUCTURE_INVALIDATION: "STRUCTURE_PROXY_RESULT",
        ExitPolicyId.SCALE_OUT_RUNNER: "SCALE_OUT_RESULT",
        ExitPolicyId.REGIME_AWARE: "REGIME_AWARE_RESULT",
    }
    for policy, key in labels.items():
        aggregate = aggregates.get(policy)
        if aggregate is None or aggregate.net_expectancy_usdt is None:
            print(f"{key}=N/A")
            continue
        capture = (
            f"{aggregate.median_profit_capture_ratio:.1%}"
            if aggregate.median_profit_capture_ratio is not None
            else "N/A"
        )
        profit_factor = f"{aggregate.profit_factor:.2f}" if aggregate.profit_factor is not None else "N/A"
        avg_r = f"{aggregate.avg_r:.2f}" if aggregate.avg_r is not None else "N/A"
        median_r = f"{aggregate.median_r:.2f}" if aggregate.median_r is not None else "N/A"
        print(
            f"{key}=n={aggregate.trade_count}, net_expectancy={aggregate.net_expectancy_usdt:+.2f} USDT, "
            f"PF={profit_factor}, avg_R={avg_r}, median_R={median_r}, "
            f"max_DD={aggregate.max_drawdown_usdt:.2f}, capture={capture}, "
            f"fee_drag={aggregate.fee_drag_usdt:.2f}"
        )


def _print_per_trade_detail(outcomes: list[ShadowOutcome]) -> None:
    """Print per-trade rows for the baseline policy so individual trades are auditable."""
    control = [o for o in outcomes if o.policy == ExitPolicyId.CURRENT_CONTROL]
    if not control:
        return

    print("=" * 80)
    print("PER-TRADE DETAIL (A_CURRENT_CONTROL)")
    print("=" * 80)
    print()
    headers = ["SYMBOL", "SIDE", "ENTRY", "REASON", "NET", "MFE_R", "MAE_R", "CAPTURE", "HOLD_MIN"]
    print(" | ".join(f"{h:>10}" for h in headers))
    print("-" * (11 * len(headers) + (len(headers) - 1) * 3))
    for o in sorted(control, key=lambda x: x.position_id):
        ratio = o.profit_capture_ratio
        capture = f"{ratio:.1%}" if ratio is not None else (o.capture_ratio_undefined_reason or "N/A")[:10]
        row = [
            o.symbol.replace("/USDT", ""),
            o.side,
            f"{o.entry_price:.2f}",
            o.final_reason.value[:10],
            f"{o.net_pnl_usdt:+.2f}",
            f"{o.excursions.mfe_r:.2f}" if o.excursions.mfe_r is not None else "N/A",
            f"{o.excursions.mae_r:.2f}" if o.excursions.mae_r is not None else "N/A",
            capture,
            f"{o.holding_time_minutes:.0f}",
        ]
        print(" | ".join(f"{cell:>10}" for cell in row))
    print()


def _print_comparison_table(aggregates: dict[ExitPolicyId, PolicyAggregate]) -> None:
    """Print a comparison table for the given aggregates."""
    if not aggregates:
        print("(no data)")
        return

    headers = [
        "POLICY",
        "TRADES",
        "NET_EXP",
        "PF",
        "AVG_R",
        "MAX_DD",
        "CAPTURE",
        "FEE_DRAG",
        "HOLD_MIN",
        "AMBIG",
    ]
    print(" | ".join(f"{h:>12}" for h in headers))
    print("-" * (13 * len(headers) + (len(headers) - 1) * 3))

    for policy, agg in sorted(aggregates.items(), key=lambda kv: kv[0].value):
        row = [
            policy.value[:12],
            str(agg.trade_count),
            f"{agg.net_expectancy_usdt:.2f}" if agg.net_expectancy_usdt is not None else "N/A",
            f"{agg.profit_factor:.2f}" if agg.profit_factor is not None else "N/A",
            f"{agg.avg_r:.2f}" if agg.avg_r is not None else "N/A",
            f"{agg.max_drawdown_usdt:.2f}",
            (f"{agg.median_profit_capture_ratio:.2%}" if agg.median_profit_capture_ratio is not None else "N/A"),
            f"{agg.fee_drag_usdt:.2f}",
            f"{agg.median_holding_time_minutes:.0f}" if agg.median_holding_time_minutes else "N/A",
            str(agg.ambiguous_intrabar_count),
        ]
        print(" | ".join(f"{cell:>12}" for cell in row))


def _find_best_policy(aggregates: dict[ExitPolicyId, PolicyAggregate]) -> tuple[ExitPolicyId, Decimal]:
    """Return the policy with the highest net expectancy."""
    best_policy = ExitPolicyId.CURRENT_CONTROL
    best_exp = Decimal("-999999")
    for pol, agg in aggregates.items():
        if agg.net_expectancy_usdt is not None and agg.net_expectancy_usdt > best_exp:
            best_exp = agg.net_expectancy_usdt
            best_policy = pol
    return best_policy, best_exp


def _rank_policy(aggregates: dict[ExitPolicyId, PolicyAggregate], target: ExitPolicyId) -> int:
    """Return the rank of ``target`` (1 = best) by net expectancy."""
    sorted_policies = sorted(
        aggregates.items(),
        key=lambda kv: kv[1].net_expectancy_usdt if kv[1].net_expectancy_usdt else Decimal("-999999"),
        reverse=True,
    )
    for rank, (pol, _) in enumerate(sorted_policies, start=1):
        if pol == target:
            return rank
    return len(sorted_policies)


def _agg_to_dict(agg: PolicyAggregate) -> dict[str, object]:
    """Serialize PolicyAggregate to dict."""
    return {
        "policy": agg.policy.value,
        "slice_key": agg.slice_key,
        "trade_count": agg.trade_count,
        "win_rate": float(agg.win_rate) if agg.win_rate else None,
        "net_expectancy_usdt": float(agg.net_expectancy_usdt) if agg.net_expectancy_usdt else None,
        "profit_factor": float(agg.profit_factor) if agg.profit_factor else None,
        "avg_r": float(agg.avg_r) if agg.avg_r else None,
        "median_r": float(agg.median_r) if agg.median_r else None,
        "max_drawdown_usdt": float(agg.max_drawdown_usdt),
        "avg_mfe_r": float(agg.avg_mfe_r) if agg.avg_mfe_r else None,
        "avg_mae_r": float(agg.avg_mae_r) if agg.avg_mae_r else None,
        "median_profit_capture_ratio": (
            float(agg.median_profit_capture_ratio) if agg.median_profit_capture_ratio else None
        ),
        "capture_ratio_sample_count": agg.capture_ratio_sample_count,
        "fee_drag_usdt": float(agg.fee_drag_usdt),
        "avg_holding_time_minutes": (float(agg.avg_holding_time_minutes) if agg.avg_holding_time_minutes else None),
        "median_holding_time_minutes": (
            float(agg.median_holding_time_minutes) if agg.median_holding_time_minutes else None
        ),
        "ambiguous_intrabar_count": agg.ambiguous_intrabar_count,
    }


if __name__ == "__main__":
    main()
