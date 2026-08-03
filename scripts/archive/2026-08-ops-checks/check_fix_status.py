"""一行确认fix是否生效：检查unmanaged_external_symbols和最近cycle actions"""

import json
import sqlite3
import sys
from pathlib import Path

db = Path(__file__).parents[1] / ".local_paper_console.db"
conn = sqlite3.connect(str(db))
conn.row_factory = sqlite3.Row

row = conn.execute(
    "SELECT paper_metrics_summary, execution_profile FROM paper_runs WHERE paper_run_id LIKE '35298c65%' LIMIT 1"
).fetchone()

if not row:
    print("run not found")
    sys.exit(1)

m = json.loads(row["paper_metrics_summary"] or "{}")
p = json.loads(row["execution_profile"] or "{}")

print("=== FIX STATUS ===")
print(f"allow_entry_with_unmanaged_positions : {p.get('allow_entry_with_unmanaged_positions')}")
print(f"unmanaged_external_symbols           : {m.get('unmanaged_external_symbols', 'not set')}")
print("\n=== LAST CYCLE ACTIONS ===")
for a in m.get("last_cycle_actions", []):
    print(f"  {a.get('symbol', '?'):12} {a.get('action', '?')}")
conn.close()
