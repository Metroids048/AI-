"""Dump recent sampling metrics from decision traces / state."""

from __future__ import annotations

import contextlib
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "logs" / "scheduler-state.json"
DB = ROOT / ".local_paper_console.db"
RUN = "78ba69a7-2bfb-457e-9a97-934aaf418e00"


def main() -> None:
    if STATE.exists():
        state = json.loads(STATE.read_text(encoding="utf-8-sig"))
        paper = (state.get("task_last_results") or {}).get("paper_runtime_cycle") or {}
        print("=== scheduler paper_runtime_cycle actions ===")
        for action in paper.get("actions") or []:
            trace = action.get("decision_trace") or {}
            print(
                {
                    "symbol": action.get("symbol"),
                    "action": action.get("action"),
                    "reason": action.get("reason"),
                    "sampling_reason": action.get("sampling_reason") or trace.get("sampling_fallback_rejection_reason"),
                    "metrics": trace.get("sampling_metrics"),
                }
            )
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    print("tables_with_decision", [t for t in tables if "decision" in t.lower() or "funnel" in t.lower()])
    if "decision_funnel_terminals" in tables:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(decision_funnel_terminals)")]
        print("funnel_cols", cols)
        select_cols = ", ".join(cols)
        rows = conn.execute(
            f"""
            SELECT {select_cols}
            FROM decision_funnel_terminals
            WHERE paper_run_id=?
            ORDER BY created_at DESC LIMIT 6
            """,
            (RUN,),
        ).fetchall()
        print("=== funnel terminals ===")
        for row in rows:
            print(dict(row))
    # decision_events may carry sampling_metrics in payload
    if "decision_events" in tables:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(decision_events)")]
        print("event_cols", cols)
        for row in conn.execute(
            """
            SELECT * FROM decision_events
            WHERE paper_run_id=?
            ORDER BY created_at DESC LIMIT 4
            """,
            (RUN,),
        ):
            d = dict(row)
            for key in ("payload", "payload_json", "details", "trace_json", "event_payload"):
                if key in d and d[key]:
                    with contextlib.suppress(json.JSONDecodeError):
                        d[key] = json.loads(d[key]) if isinstance(d[key], str) else d[key]
            print("event", {k: d.get(k) for k in list(d)[:12]})
    conn.close()


if __name__ == "__main__":
    main()
