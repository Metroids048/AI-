"""CLI entry point for P2-A exit-policy shadow evaluation.

Usage:
    python -m services.research.exit_policy_shadow.cli

Loads real testnet_sampling_v2 entries, replays under five exit policies,
computes aggregates and verdicts, writes a report to stdout and a JSON artifact.
"""

from __future__ import annotations

import json
import sys
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from statistics import median

from services.research.exit_policy_shadow.contracts import (
    MINIMUM_TRADES_FOR_VERDICT,
    ExcursionMetrics,
    ExitPolicyId,
    PolicyAggregate,
    Regime,
    ShadowOutcome,
)
from services.research.exit_policy_shadow.excursions import compute_excursions
from services.research.exit_policy_shadow.loader import (
    build_entry_context,
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
from services.research.exit_policy_shadow.replay import replay_entry_under_policy

# Fixed observation horizon for entry-level excursions (Q1). Covers the longest real
# holding time in the sample, so entry quality is measured over a window every entry
# actually had, independent of any policy's exit.
ENTRY_HORIZON_HOURS = 24


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
    print()

    # Split by symbol.
    btc_entries = [e for e in entries if e.symbol == "BTC/USDT"]
    eth_entries = [e for e in entries if e.symbol == "ETH/USDT"]
    print(f"BTC_TRADES={len(btc_entries)}")
    print(f"ETH_TRADES={len(eth_entries)}")
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

        # Entry-level excursions over one fixed horizon, measured before any exit
        # policy is applied. Q1 ("does the entry have edge") must not be answered
        # from per-policy excursions, which are truncated at each policy's own exit.
        control_stop, _ = build_initial_geometry(
            policy=ExitPolicyId.CURRENT_CONTROL,
            side=entry.side,
            entry_price=entry.average_fill_price,
            entry_context=entry_context,
            regime=Regime.UNKNOWN,
        )
        horizon_bars = [
            bar for bar in bars_1m if bar.time <= entry.fill_timestamp + timedelta(hours=ENTRY_HORIZON_HOURS)
        ]
        entry_excursions.append(
            compute_excursions(
                side=entry.side,
                entry_price=entry.average_fill_price,
                quantity=entry.filled_quantity,
                bars=horizon_bars,
                risk_per_unit=abs(entry.average_fill_price - control_stop),
            )
        )

        for policy in policies:
            outcome = replay_entry_under_policy(
                entry=entry,
                bars=bars_1m,
                policy=policy,
                regime=Regime.UNKNOWN,
                entry_context=entry_context,
            )
            all_outcomes.append(outcome)
        replayed_entries += 1

    print(f"REPLAYED_ENTRIES={replayed_entries}")
    print(f"SKIPPED_NO_BARS={skipped_no_bars}")
    print(f"SKIPPED_NO_POINT_IN_TIME_ATR={skipped_no_atr}")
    print(f"OUTCOME_ROWS={len(all_outcomes)}")
    print()

    if not all_outcomes:
        print("BLOCKED: no entry could be replayed with point-in-time inputs.")
        sys.exit(2)

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

    q1_verdict, q1_evidence = answer_q1_entry_has_edge(entry_excursions)
    print(f"Q1_ENTRY_HAS_EDGE={q1_verdict.value}")
    print(f"Q1_EVIDENCE={q1_evidence}")
    _print_observed_entry_excursions(entry_excursions)
    print()

    control_outcomes = [o for o in all_outcomes if o.policy == ExitPolicyId.CURRENT_CONTROL]
    q2_verdict, q2_evidence = answer_q2_exit_leakage(control_outcomes)
    print(f"Q2_EXIT_LEAKAGE={q2_verdict.value}")
    print(f"Q2_EVIDENCE={q2_evidence}")
    print()

    q3_verdict, q3_evidence = answer_q3_policy_by_regime(all_outcomes)
    print(f"Q3_POLICY_BY_REGIME={q3_verdict.value}")
    print(f"Q3_EVIDENCE={q3_evidence}")
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
        print(f"BEST_OBSERVED_POLICY={best_policy.value}")
        print(f"BEST_OBSERVED_NET_EXPECTANCY={best_exp:.2f} USDT")
        print()

    print("SAMPLE_LIMITATIONS=")
    if len(entries) < MINIMUM_TRADES_FOR_VERDICT:
        print(f"  - Only {len(entries)} trades; statistical power is low. Verdicts may flip with more data.")
    if len(entries) < 20:
        print("  - Small sample prevents confident regime segmentation. Q3 likely INSUFFICIENT_SAMPLE.")
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
        "btc_entries": len(btc_entries),
        "eth_entries": len(eth_entries),
        "q1_verdict": q1_verdict.value,
        "q1_evidence": q1_evidence,
        "q2_verdict": q2_verdict.value,
        "q2_evidence": q2_evidence,
        "q3_verdict": q3_verdict.value,
        "q3_evidence": q3_evidence,
        "overall": {pol.value: _agg_to_dict(agg) for pol, agg in overall.items() if agg is not None},
    }
    with open(artifact_path, "w", encoding="utf-8") as f:
        json.dump(artifact, f, indent=2, default=str)

    print(f"✅ Artifact written: {artifact_path}")
    print()
    print("=" * 80)
    print("P2A_IMPLEMENTATION_COMPLETE_PENDING_REVIEW")
    print("=" * 80)


def _print_observed_entry_excursions(excursions: list[ExcursionMetrics]) -> None:
    """Print observed entry-level excursions.

    Printed even when the verdict is INSUFFICIENT_SAMPLE: the numbers are what the
    data actually shows and are useful, but they are explicitly labelled as not
    constituting a conclusion so they cannot be quoted as one.
    """
    if not excursions:
        return
    mfe_r = [e.mfe_r for e in excursions if e.mfe_r is not None]
    mae_r = [e.mae_r for e in excursions if e.mae_r is not None]
    if not mfe_r or not mae_r:
        return

    avg_mfe = sum(mfe_r, Decimal("0")) / Decimal(len(mfe_r))
    avg_mae = sum(mae_r, Decimal("0")) / Decimal(len(mae_r))
    positive_mfe = sum(1 for value in mfe_r if value > 0)

    print(
        f"Q1_OBSERVED_ONLY (n={len(excursions)}, NOT a conclusion): "
        f"avg MFE={avg_mfe:.2f}R, avg MAE={avg_mae:.2f}R, "
        f"median MFE={median(mfe_r):.2f}R, median MAE={median(mae_r):.2f}R, "
        f"entries with positive MFE={positive_mfe}/{len(mfe_r)}, "
        f"horizon={ENTRY_HORIZON_HOURS}h"
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
