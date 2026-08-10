"""Read-only gross-vs-net decomposition for the S1A baseline.

Answers the decisive S1 question: is a candidate's negative expectancy caused by
execution cost, or is the raw signal itself unprofitable before cost?  Reads the
finished artifact set only; no replay, no writes into the artifact directory.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from statistics import fmean, pstdev

CANDIDATES = (
    "trend_pullback_v2",
    "range_sweep_reversion_v1",
    "failed_breakout_reversal_v1",
)
WINDOWS = tuple(f"oos_{index}" for index in range(1, 9))


def _fmt(value: float | None, digits: int = 6) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def _stats(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"n": 0, "mean": None, "sd": None, "t": None, "ci_lo": None, "ci_hi": None}
    mean = fmean(values)
    sd = pstdev(values) if len(values) > 1 else 0.0
    se = sd / (len(values) ** 0.5) if len(values) > 1 and sd > 0 else None
    return {
        "n": len(values),
        "mean": mean,
        "sd": sd,
        "t": mean / se if se else None,
        "ci_lo": mean - 1.96 * se if se else None,
        "ci_hi": mean + 1.96 * se if se else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifacts", type=Path, required=True)
    args = parser.parse_args()
    report = json.loads((args.artifacts / "proposal-research-report.json").read_text(encoding="utf-8"))
    results = report["results"]
    window_bounds = {
        window_id: (
            datetime.fromisoformat(results[CANDIDATES[0]]["walk_forward_oos"][window_id]["window"]["oos_start"]),
            datetime.fromisoformat(results[CANDIDATES[0]]["walk_forward_oos"][window_id]["window"]["oos_end"]),
        )
        for window_id in WINDOWS
    }

    print("=== S1A GROSS vs NET DECOMPOSITION (read-only) ===")
    print(f"artifacts={args.artifacts}")
    print()
    print("--- PORTFOLIO: is the raw signal profitable before cost? ---")
    print(
        f"{'candidate':<30}{'n':>6}{'gross_exp':>12}{'net_exp':>12}{'cost/trade':>12}"
        f"{'gross_t':>9}{'gross_ci_lo':>13}{'gross_ci_hi':>13}"
    )
    per_candidate: dict[str, list[dict]] = {}
    for candidate in CANDIDATES:
        trades = results[candidate]["trades"]
        per_candidate[candidate] = trades
        gross = [float(trade["gross_return"]) for trade in trades]
        net = [float(trade["net_return"]) for trade in trades]
        cost = [g - n for g, n in zip(gross, net, strict=True)]
        gs = _stats(gross)
        print(
            f"{candidate:<30}{len(trades):>6}{_fmt(gs['mean']):>12}"
            f"{_fmt(fmean(net) if net else None):>12}{_fmt(fmean(cost) if cost else None):>12}"
            f"{_fmt(gs['t'], 2):>9}{_fmt(gs['ci_lo']):>13}{_fmt(gs['ci_hi']):>13}"
        )
    print()
    print("interpretation: gross_ci spanning 0 means the raw signal is not")
    print("distinguishable from no-edge even before execution cost is charged.")
    print()

    print("--- PER WINDOW: gross expectancy sign stability ---")
    for candidate in CANDIDATES:
        trades = per_candidate[candidate]
        if not trades:
            print(f"\n[{candidate}] no trades")
            continue
        buckets: dict[str, list[float]] = defaultdict(list)
        for trade in trades:
            signal_time = datetime.fromisoformat(trade["signal_time"])
            for window_id, (start, end) in window_bounds.items():
                if start <= signal_time < end:
                    buckets[window_id].append(float(trade["gross_return"]))
                    break
        print(f"\n[{candidate}]")
        print(f"{'window':<9}{'n':>6}{'gross_exp':>12}{'gross_t':>9}{'sign':>7}")
        positive = 0
        counted = 0
        for window_id in WINDOWS:
            values = buckets.get(window_id, [])
            stats = _stats(values)
            sign = "n/a" if stats["mean"] is None else ("+" if stats["mean"] > 0 else "-")
            if values:
                counted += 1
                if stats["mean"] and stats["mean"] > 0:
                    positive += 1
            print(f"{window_id:<9}{stats['n']:>6}{_fmt(stats['mean']):>12}{_fmt(stats['t'], 2):>9}{sign:>7}")
        print(f"  gross_positive_windows={positive}/{counted}")

    print()
    print("--- EXIT REASON MIX (why trades end) ---")
    for candidate in CANDIDATES:
        trades = per_candidate[candidate]
        if not trades:
            print(f"{candidate:<30} no trades")
            continue
        counts = Counter(trade["exit_reason"] for trade in trades)
        total = sum(counts.values())
        mix = "  ".join(f"{reason}={count} ({count / total:.1%})" for reason, count in counts.most_common())
        print(f"{candidate:<30} {mix}")

    print()
    print("--- COST STRUCTURE ---")
    for candidate in CANDIDATES:
        trades = per_candidate[candidate]
        if not trades:
            continue
        bps = Counter(float(trade["fees_and_impact_bps"]) for trade in trades)
        funding = [float(trade["funding_cost"]) for trade in trades]
        nonzero_funding = [value for value in funding if value != 0]
        print(
            f"{candidate:<30} fees_and_impact_bps={dict(bps)} "
            f"funding_nonzero_trades={len(nonzero_funding)}/{len(trades)} "
            f"funding_sum={sum(funding):.6f}"
        )

    print()
    print("--- HOLDING PERIOD ---")
    for candidate in CANDIDATES:
        trades = per_candidate[candidate]
        if not trades:
            continue
        minutes = [
            (datetime.fromisoformat(trade["closed_at"]) - datetime.fromisoformat(trade["opened_at"])).total_seconds()
            / 60
            for trade in trades
        ]
        print(
            f"{candidate:<30} median_minutes={sorted(minutes)[len(minutes) // 2]:.0f} "
            f"mean_minutes={fmean(minutes):.1f} max_minutes={max(minutes):.0f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
