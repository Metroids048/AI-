import json
import sqlite3
from pathlib import Path

conn = sqlite3.connect(".local_paper_console.db")
conn.row_factory = sqlite3.Row
row = conn.execute(
    "SELECT paper_run_id, execution_profile FROM paper_runs WHERE paper_run_id=?",
    ("78ba69a7-2bfb-457e-9a97-934aaf418e00",),
).fetchone()
prof = json.loads(row["execution_profile"]) if isinstance(row["execution_profile"], str) else row["execution_profile"]
print("acceptance_symbols", prof.get("acceptance_symbols"))
print("max_symbols", prof.get("max_symbols"))
print("strategy_lane", prof.get("strategy_lane"))
# candidate_symbols column?
cols = [r[1] for r in conn.execute("PRAGMA table_info(paper_runs)")]
print([c for c in cols if "cand" in c or "symbol" in c])
if "candidate_symbols" in cols:
    print(
        "candidate",
        conn.execute(
            "SELECT candidate_symbols FROM paper_runs WHERE paper_run_id=?", ("78ba69a7-2bfb-457e-9a97-934aaf418e00",)
        ).fetchone()[0],
    )
state = json.loads(Path("logs/scheduler-state.json").read_text(encoding="utf-8-sig"))
paper = (state.get("task_last_results") or {}).get("paper_runtime_cycle") or {}
acts = paper.get("actions") or []
print("n_actions", len(acts), "symbols", [a.get("symbol") for a in acts])
print("cycle_time", paper.get("cycle_time"), "paper_run", paper.get("paper_run_id"))
conn.close()
