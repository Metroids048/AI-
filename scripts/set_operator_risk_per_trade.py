"""Update the operator risk_per_trade on the armed directional PaperRun.

``risk_per_trade`` is in ``OPERATOR_AUTO_SETTING_KEYS``, so bootstrap deliberately
preserves whatever the database holds across restarts. Changing the static default
in ``PAPER_RUNTIME_LIMITS`` is therefore not enough — the persisted operator value
must be updated explicitly, and the active ConfigSnapshot must be re-staged so the
next cycle reads it.

Usage:
    python scripts/set_operator_risk_per_trade.py --value 0.10 [--execute]
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys

DB = ".local_paper_console.db"
DIRECTIONAL_RUN = "35298c65-cdbe-4bee-bee3-b7ded07c3204"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--value", type=float, required=True)
    parser.add_argument("--run-id", default=DIRECTIONAL_RUN)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()

    if not 0 < args.value <= 0.5:
        print(f"refusing implausible risk_per_trade={args.value}; expected 0 < value <= 0.5")
        return 2

    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT execution_profile, active_config_snapshot_id, paper_metrics_summary FROM paper_runs WHERE paper_run_id = ?",
        (args.run_id,),
    ).fetchone()
    if row is None:
        print(f"paper_run not found: {args.run_id}")
        return 1

    profile = json.loads(row["execution_profile"] or "{}")
    metrics = json.loads(row["paper_metrics_summary"] or "{}")
    old = profile.get("risk_per_trade")
    equity = float(metrics.get("account_equity") or profile.get("account_equity") or 0.0)
    exposure = float(profile.get("max_symbol_exposure") or 0.0)
    leverage = float(profile.get("max_leverage") or 0.0)

    print(f"run              = {args.run_id}")
    print(f"risk_per_trade   = {old} -> {args.value}")
    print(f"account_equity   = {equity}")
    print(f"exposure cap     = {exposure} -> {equity * exposure:.2f} USDT notional ceiling")
    print(f"margin capacity  = {leverage}x -> {equity * leverage:.2f} USDT notional ceiling")
    print(f"risk budget      = {equity * args.value:.2f} USDT per trade if stop is hit")

    if not args.execute:
        print("\n[DRY RUN] pass --execute to apply")
        return 0

    profile["risk_per_trade"] = args.value
    conn.execute(
        "UPDATE paper_runs SET execution_profile = ?, pending_config_snapshot_id = NULL, pending_config_hash = NULL WHERE paper_run_id = ?",
        (json.dumps(profile), args.run_id),
    )
    conn.commit()
    print(f"\nupdated execution_profile.risk_per_trade = {args.value}")

    # Re-stage: clearing the active snapshot forces bootstrap to publish a fresh
    # one from the updated profile on next start instead of reusing a stale hash.
    conn.execute(
        "UPDATE paper_runs SET active_config_snapshot_id = NULL, active_config_hash = NULL WHERE paper_run_id = ?",
        (args.run_id,),
    )
    conn.commit()
    print("cleared active ConfigSnapshot; bootstrap will publish a fresh one on restart")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
