"""Read-only acceptance check for the S1A proposal-baseline artifact set.

Verifies the seven S1A acceptance points against a finished artifact directory.
Reads only; never writes into the artifact directory and never imports an
execution service.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

EXPECTED_CANDIDATES = (
    "failed_breakout_reversal_v1",
    "trend_pullback_v2",
    "range_sweep_reversion_v1",
)
EXPECTED_SYMBOLS = ("BTC/USDT", "ETH/USDT")
EXPECTED_WINDOWS = tuple(f"oos_{index}" for index in range(1, 9))
EXPECTED_ARTIFACTS = (
    "PHASE1_MANIFEST.json",
    "source_tree_manifest.json",
    "config_manifest.json",
    "proposal-research-report.json",
    "trial-ledger.jsonl",
)
FINAL_HOLDOUT_START = "2026-01-29T00:00:00+00:00"


def _nonfinite_paths(node: Any, trail: str = "") -> list[str]:
    found: list[str] = []
    if isinstance(node, float):
        if math.isnan(node) or math.isinf(node):
            found.append(trail or "<root>")
    elif isinstance(node, dict):
        for key, value in node.items():
            found.extend(_nonfinite_paths(value, f"{trail}.{key}" if trail else str(key)))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            found.extend(_nonfinite_paths(value, f"{trail}[{index}]"))
    return found


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifacts", type=Path, required=True)
    args = parser.parse_args()
    root = args.artifacts
    checks: list[tuple[str, bool, str]] = []

    missing = [name for name in EXPECTED_ARTIFACTS if not (root / name).is_file()]
    checks.append(("A5_artifacts_present", not missing, f"missing={missing or 'none'}"))

    report = json.loads((root / "proposal-research-report.json").read_text(encoding="utf-8"))
    results = report["results"]

    # A1: three candidates x two symbols present.
    candidate_gaps: list[str] = []
    for candidate in EXPECTED_CANDIDATES:
        if candidate not in results:
            candidate_gaps.append(f"candidate_missing:{candidate}")
            continue
        for symbol in EXPECTED_SYMBOLS:
            if symbol not in results[candidate]["symbols"]:
                candidate_gaps.append(f"symbol_missing:{candidate}/{symbol}")
    extra = sorted(set(results) - set(EXPECTED_CANDIDATES))
    checks.append(
        (
            "A1_candidates_x_symbols",
            not candidate_gaps and not extra,
            f"candidates={len(results)} gaps={candidate_gaps or 'none'} unexpected={extra or 'none'}",
        )
    )

    # A2: eight complete walk-forward windows for every candidate/symbol.
    window_gaps: list[str] = []
    for candidate in EXPECTED_CANDIDATES:
        oos = results.get(candidate, {}).get("walk_forward_oos", {})
        if tuple(oos) != EXPECTED_WINDOWS:
            window_gaps.append(f"{candidate}:window_ids={tuple(oos)}")
            continue
        for window_id in EXPECTED_WINDOWS:
            entry = oos[window_id]
            if entry["window"]["window_id"] != window_id:
                window_gaps.append(f"{candidate}/{window_id}:id_mismatch")
            for symbol in EXPECTED_SYMBOLS:
                payload = entry["symbols"].get(symbol)
                if payload is None:
                    window_gaps.append(f"{candidate}/{window_id}/{symbol}:absent")
                elif "selected_proposals" not in payload or "total_trades" not in payload:
                    window_gaps.append(f"{candidate}/{window_id}/{symbol}:incomplete_payload")
    checks.append(
        (
            "A2_eight_windows_complete",
            not window_gaps,
            f"gaps={window_gaps[:6] or 'none'} (total={len(window_gaps)})",
        )
    )

    # A3: final holdout never read.
    manifest = json.loads((root / "PHASE1_MANIFEST.json").read_text(encoding="utf-8"))
    config = json.loads((root / "config_manifest.json").read_text(encoding="utf-8"))
    holdout_ok = (
        report["holdout_results_accessed"] is False
        and manifest["holdout_results_accessed"] is False
        and config["final_holdout_results_accessed"] is False
        and report["scope"]["final_holdout_start"] == FINAL_HOLDOUT_START
    )
    window_bounds_ok = all(
        results[candidate]["walk_forward_oos"][window_id]["window"]["oos_end"] <= FINAL_HOLDOUT_START
        for candidate in EXPECTED_CANDIDATES
        for window_id in EXPECTED_WINDOWS
    )
    checks.append(
        (
            "A3_final_holdout_not_accessed",
            holdout_ok and window_bounds_ok,
            f"flags_false={holdout_ok} all_oos_end<=holdout={window_bounds_ok}",
        )
    )

    # A4: no NaN/Inf, no silent skip, no crash marker.
    nonfinite = _nonfinite_paths(report)
    trade_totals = {candidate: results[candidate]["portfolio"]["total_trades"] for candidate in EXPECTED_CANDIDATES}
    selected_totals = {
        candidate: results[candidate]["portfolio"]["selected_proposals"] for candidate in EXPECTED_CANDIDATES
    }
    error_keys = [key for key in report if "error" in key.lower() or "crash" in key.lower()]
    funding_flags = {
        candidate: results[candidate]["portfolio"]["funding_rate_available"] for candidate in EXPECTED_CANDIDATES
    }
    checks.append(
        (
            "A4_no_nan_no_silent_skip",
            not nonfinite and not error_keys,
            f"nonfinite={nonfinite[:5] or 'none'} error_keys={error_keys or 'none'} funding_available={funding_flags}",
        )
    )

    # A5b: ledger completeness — 3 candidates x 8 windows x 2 symbols.
    ledger_lines = [line for line in (root / "trial-ledger.jsonl").read_text(encoding="utf-8").splitlines() if line]
    ledger = [json.loads(line) for line in ledger_lines]
    trial_ids = [item["trial_id"] for item in ledger]
    expected_ids = {
        f"{candidate}:{symbol}:{window_id}"
        for candidate in EXPECTED_CANDIDATES
        for window_id in EXPECTED_WINDOWS
        for symbol in EXPECTED_SYMBOLS
    }
    duplicates = len(trial_ids) - len(set(trial_ids))
    checks.append(
        (
            "A5b_ledger_complete_and_unique",
            set(trial_ids) == expected_ids and duplicates == 0,
            f"lines={len(ledger_lines)} unique={len(set(trial_ids))} expected={len(expected_ids)} "
            f"duplicates={duplicates} missing={sorted(expected_ids - set(trial_ids))[:4] or 'none'}",
        )
    )

    # A7: no production write markers in the artifact set.
    verdict_ok = report.get("verdict") == "NO_ACTIVE_STRATEGY" and manifest.get("status") == "NO_ACTIVE_STRATEGY"
    promotion_blocked = all(results[candidate]["promotion"]["eligible"] is False for candidate in EXPECTED_CANDIDATES)
    checks.append(
        (
            "A7_no_production_write",
            verdict_ok and promotion_blocked,
            f"verdict={report.get('verdict')} manifest_status={manifest.get('status')} "
            f"promotion_eligible_any={not promotion_blocked}",
        )
    )

    print("=== S1A ACCEPTANCE (read-only) ===")
    print(f"artifacts={root}")
    for name, passed, detail in checks:
        print(f"[{'PASS' if passed else 'FAIL'}] {name}: {detail}")
    print("--- portfolio totals ---")
    print(f"trade_count={trade_totals}")
    print(f"selected_proposals={selected_totals}")
    print(f"funding_rows_detected={report.get('funding_rows_detected')}")
    failures = [name for name, passed, _ in checks if not passed]
    print(f"RESULT={'ALL_PASS' if not failures else 'FAIL:' + ','.join(failures)}")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
