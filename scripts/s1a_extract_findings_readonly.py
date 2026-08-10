"""Read-only extraction of S1A per-candidate / per-window / per-symbol metrics.

Produces the numeric tables needed to answer the three S1 stub questions.
Reads the finished artifact set only; performs no replay and no writes into it.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

CANDIDATES = (
    "trend_pullback_v2",
    "range_sweep_reversion_v1",
    "failed_breakout_reversal_v1",
)
SYMBOLS = ("BTC/USDT", "ETH/USDT")
WINDOWS = tuple(f"oos_{index}" for index in range(1, 9))


def _fmt(value: Any, digits: int = 4) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifacts", type=Path, required=True)
    args = parser.parse_args()
    report = json.loads((args.artifacts / "proposal-research-report.json").read_text(encoding="utf-8"))
    results = report["results"]

    print("=== S1A FINDINGS EXTRACT (read-only) ===")
    print(f"artifacts={args.artifacts}")
    print(f"cost_model={json.dumps(report['cost_model'], default=str)}")
    print(f"funding_rows_detected={report['funding_rows_detected']}")
    print()

    print("--- PORTFOLIO (all windows, both symbols) ---")
    header = f"{'candidate':<30}{'trades':>8}{'win_rate':>10}{'PF':>9}{'expectancy':>13}{'net_ret':>11}{'maxDD':>10}"
    print(header)
    for candidate in CANDIDATES:
        p = results[candidate]["portfolio"]
        print(
            f"{candidate:<30}{p['total_trades']:>8}{_fmt(p['win_rate']):>10}{_fmt(p['profit_factor']):>9}"
            f"{_fmt(p['net_expectancy'], 6):>13}{_fmt(p['net_return']):>11}{_fmt(p['max_drawdown']):>10}"
        )
    print()

    print("--- PER SYMBOL ---")
    print(f"{'candidate':<30}{'symbol':<11}{'trades':>8}{'win_rate':>10}{'PF':>9}{'expectancy':>13}{'net_ret':>11}")
    for candidate in CANDIDATES:
        for symbol in SYMBOLS:
            s = results[candidate]["symbols"][symbol]
            print(
                f"{candidate:<30}{symbol:<11}{s['total_trades']:>8}{_fmt(s['win_rate']):>10}"
                f"{_fmt(s['profit_factor']):>9}{_fmt(s['net_expectancy'], 6):>13}{_fmt(s['net_return']):>11}"
            )
    print()

    print("--- PER WALK-FORWARD WINDOW (symbol-summed) ---")
    for candidate in CANDIDATES:
        print(f"\n[{candidate}]")
        print(
            f"{'window':<9}{'oos_start':<12}{'oos_end':<12}{'trades':>8}{'win_rate':>10}"
            f"{'PF':>9}{'expectancy':>13}{'net_ret':>11}{'maxDD':>9}"
        )
        oos = results[candidate]["walk_forward_oos"]
        positive_windows = 0
        counted_windows = 0
        for window_id in WINDOWS:
            entry = oos[window_id]
            window = entry["window"]
            trades = sum(entry["symbols"][symbol]["total_trades"] for symbol in SYMBOLS)
            net = sum(entry["symbols"][symbol]["net_return"] for symbol in SYMBOLS)
            wins = sum(
                entry["symbols"][symbol]["win_rate"] * entry["symbols"][symbol]["total_trades"] for symbol in SYMBOLS
            )
            win_rate = wins / trades if trades else None
            pf_values = [
                entry["symbols"][symbol]["profit_factor"]
                for symbol in SYMBOLS
                if entry["symbols"][symbol]["total_trades"]
            ]
            pf = sum(pf_values) / len(pf_values) if pf_values else None
            expectancy = net / trades if trades else None
            dd = max(entry["symbols"][symbol]["max_drawdown"] for symbol in SYMBOLS)
            if trades:
                counted_windows += 1
                if net > 0:
                    positive_windows += 1
            print(
                f"{window_id:<9}{window['oos_start'][:10]:<12}{window['oos_end'][:10]:<12}{trades:>8}"
                f"{_fmt(win_rate):>10}{_fmt(pf):>9}{_fmt(expectancy, 6):>13}{_fmt(net):>11}{_fmt(dd):>9}"
            )
        print(
            f"  windows_with_trades={counted_windows}/8  windows_net_positive={positive_windows}"
            f"  positive_share={_fmt(positive_windows / counted_windows if counted_windows else None)}"
        )

    print()
    print("--- SIGNAL AVAILABILITY / COST PROVENANCE ---")
    for candidate in CANDIDATES:
        p = results[candidate]["portfolio"]
        print(
            f"{candidate:<30} selected={p['selected_proposals']:<6} expired={p['expired_proposals']:<5} "
            f"drift_rejected={p['rejected_price_drift']:<5} funding_available={p['funding_rate_available']} "
            f"promotion_observations_complete={p['promotion_observations_complete']}"
        )
        print(f"{'':<30} cost_provenance={json.dumps(p['cost_provenance'])}")
        print(f"{'':<30} promotion={json.dumps(results[candidate]['promotion'])}")

    print()
    print("--- VALIDATION-LAYER GATES (AGENTS.md Layer 4 defaults) ---")
    print("gate: profit_factor>1.3, max_drawdown<25%, expectancy>0 (Sharpe not computed by this pipeline)")
    for candidate in CANDIDATES:
        p = results[candidate]["portfolio"]
        pf_ok = p["profit_factor"] > 1.3
        exp_ok = p["net_expectancy"] > 0
        print(
            f"{candidate:<30} PF={_fmt(p['profit_factor'])} ({'PASS' if pf_ok else 'FAIL'})  "
            f"expectancy={_fmt(p['net_expectancy'], 6)} ({'PASS' if exp_ok else 'FAIL'})  "
            f"-> {'PASS' if pf_ok and exp_ok else 'FAIL'}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
