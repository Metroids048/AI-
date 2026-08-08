"""Update testnet strategy to trend_momentum_v2_enriched."""

from __future__ import annotations

import json
import sqlite3
import sys

from services.execution.signal_edge_stats import strategy_rules_hash
from services.strategy_library.candidates.registry import get_candidate
from shared.models import StrategyRules

DB = ".local_paper_console.db"
TESTNET_STRATEGY_ID = "7da8d5e9-86fa-46ee-9668-a648c731819b"


def main() -> int:
    candidate = get_candidate("trend_momentum_v2_enriched")
    new_config = candidate.get_config()
    new_rules = StrategyRules(**new_config)
    new_hash = strategy_rules_hash(new_rules)

    print(f"=== Updating testnet strategy to {candidate.candidate_id} ===")
    print(f"  entry_signals: {new_config['entry_rules']['entry_signals']}")
    print(f"  rules_hash: {new_hash}")

    if "--dry-run" in sys.argv:
        print("\n[DRY RUN] Would update strategy record")
        return 0

    conn = sqlite3.connect(DB)
    cursor = conn.cursor()

    # Read current
    row = cursor.execute(
        "SELECT strategy_key, entry_rules FROM strategies WHERE id=?",
        (TESTNET_STRATEGY_ID,),
    ).fetchone()
    if not row:
        print(f"ERROR: strategy {TESTNET_STRATEGY_ID} not found")
        return 1

    print(f"\nCurrent strategy_key: {row[0]}")
    if row[1]:
        old = json.loads(row[1])
        print(f"  old candidate_id: {old.get('candidate_id')}")
        print(f"  old entry_signals: {old.get('entry_signals')}")

    # Update all rule fields
    cursor.execute(
        """UPDATE strategies SET
            entry_rules = ?,
            exit_rules = ?,
            stoploss_rules = ?,
            takeprofit_rules = ?,
            position_rules = ?
        WHERE id = ?""",
        (
            json.dumps(new_config["entry_rules"]),
            json.dumps(new_config["exit_rules"]),
            json.dumps(new_config["stoploss_rules"]),
            json.dumps(new_config["takeprofit_rules"]),
            json.dumps(new_config["position_rules"]),
            TESTNET_STRATEGY_ID,
        ),
    )
    conn.commit()
    print(f"\n✅ Updated strategy record (affected {cursor.rowcount} row)")
    print("   Restart the scheduler for changes to take effect")

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
