"""Investigate historical TESTNET_SAMPLING_SIGNAL outcomes and current blockers."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / ".local_paper_console.db"


def main() -> None:
    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row
    print("=== funnel TESTNET_SAMPLING / SAMPLING ===")
    for row in conn.execute(
        """
        SELECT symbol, bar_time, reason_code, status, created_at, paper_run_id
        FROM decision_funnel_terminals
        WHERE reason_code LIKE '%SAMPLING%' OR reason_code LIKE '%TESTNET_SAMPLING%'
        ORDER BY created_at DESC LIMIT 20
        """
    ):
        print(dict(row))

    print("\n=== order_executions around sampling windows ===")
    for row in conn.execute(
        """
        SELECT symbol, execution_status, gateway_order_id, rejection_reason, created_at,
               paper_run_id, substr(cast(entry_context as text),1,240) AS ctx
        FROM order_executions
        WHERE created_at >= '2026-07-27'
          AND (cast(entry_context as text) LIKE '%sampling%'
               OR cast(entry_context as text) LIKE '%TESTNET%'
               OR rejection_reason IS NOT NULL)
        ORDER BY created_at DESC LIMIT 30
        """
    ):
        print(dict(row))

    print("\n=== decision_events sampling-ish ===")
    cols = [r[1] for r in conn.execute("PRAGMA table_info(decision_events)")]
    print("cols", cols[:25])
    if "payload" in cols or "event_payload" in cols or "details" in cols:
        pass
    # try common shapes
    for candidate in ("payload", "details", "event_json", "trace", "reason"):
        if candidate in cols:
            print("has", candidate)

    print("\n=== paper_metrics sampling counters ===")
    for row in conn.execute(
        "SELECT paper_run_id, paper_metrics_summary FROM paper_runs WHERE paper_run_id LIKE '78ba%'"
    ):
        metrics = json.loads(row["paper_metrics_summary"] or "{}")
        keys = [k for k in metrics if "sampl" in k.lower() or "cool" in k.lower() or "rate" in k.lower()]
        print("run", row["paper_run_id"], {k: metrics.get(k) for k in keys})
        print("processed_keys_tail", list(metrics.get("processed_cycle_keys") or [])[-6:])


if __name__ == "__main__":
    main()
