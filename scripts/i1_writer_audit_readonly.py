"""I-1 writer audit. READ-ONLY (mode=ro). Resolves the 'how many live writers' gate.

Lists any lease/instance/heartbeat/writer/scheduler-ish table and dumps recent rows,
so stale heartbeats from dead PIDs are not mistaken for live writers.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

DB = ".local_paper_console.db"
PAT = ("lease", "instance", "heartbeat", "writer", "scheduler", "runtime_control", "singleton", "lock")


def main() -> int:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        hits = [t for t in tables if any(p in t.lower() for p in PAT)]
        print(f"total tables: {len(tables)}")
        print(f"candidate writer/lease tables ({len(hits)}): {hits}\n")

        for t in hits:
            cols = [c[1] for c in con.execute(f"PRAGMA table_info({t})")]
            n = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            print(f"--- {t}  rows={n}  cols={cols}")
            if n == 0:
                continue
            order = next(
                (c for c in ("updated_at", "heartbeat_at", "last_seen_at", "created_at", "id") if c in cols), None
            )
            sql = f"SELECT * FROM {t}"
            if order:
                sql += f" ORDER BY {order} DESC"
            sql += " LIMIT 10"
            for row in con.execute(sql):
                print("    " + json.dumps(dict(zip(cols, row, strict=False)), ensure_ascii=False, default=str))
            print()

        print("--- legacy scheduler_cycles / v2_execution_cycles latest")
        for t, tcol in (("scheduler_cycles", "started_at"), ("v2_execution_cycles", "started_at")):
            if t in tables:
                n = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                mx = con.execute(f"SELECT MAX({tcol}) FROM {t}").fetchone()[0]
                print(f"    {t}: count={n} max({tcol})={mx}")
    finally:
        con.close()

    sp = Path("logs/scheduler-state.json")
    if sp.exists():
        data = json.loads(sp.read_text(encoding="utf-8"))
        keys = {k: v for k, v in data.items() if not isinstance(v, (dict, list))}
        print(
            f"\n--- logs/scheduler-state.json scalar keys\n    {json.dumps(keys, ensure_ascii=False, default=str)[:1200]}"
        )
        print(f"    top-level keys: {list(data.keys())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
