"""Read-only R-003 anti-overfit checks for a five-symbol replay artifact."""

from __future__ import annotations

import json
import sys
from pathlib import Path

SYMBOLS = ("BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "BNB/USDT")
EXPECTED_WINDOWS = tuple(f"oos_{index}" for index in range(1, 9))


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: audit_r003_five_symbol.py REPORT.json")
    report = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    failures: list[str] = []
    if report.get("holdout_results_accessed") is not False:
        failures.append("holdout_accessed")
    for candidate_id, candidate in report.get("results", {}).items():
        windows = candidate.get("walk_forward_oos", {})
        if tuple(windows) != EXPECTED_WINDOWS:
            failures.append(f"{candidate_id}:window_set")
        if candidate.get("promotion", {}).get("eligible"):
            failures.append(f"{candidate_id}:promotion_flag_true")
        if not candidate.get("funding_treatment"):
            failures.append(f"{candidate_id}:funding_treatment_missing")
        for window_id, window in windows.items():
            if window["window"]["oos_end"] > report["scope"]["final_holdout_start"]:
                failures.append(f"{candidate_id}/{window_id}:holdout_contamination")
            if set(window.get("symbols", {})) != set(SYMBOLS):
                failures.append(f"{candidate_id}/{window_id}:symbol_set")
    print(json.dumps({"status": "R003_PASS" if not failures else "R003_FAIL", "failures": failures}, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
