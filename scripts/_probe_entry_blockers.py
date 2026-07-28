import json
import sqlite3
import urllib.request
from pathlib import Path

DB = Path(".local_paper_console.db")
RUN = "78ba69a7-2bfb-457e-9a97-934aaf418e00"

req = urllib.request.Request(
    "http://127.0.0.1:8016/api/v1/runtime/reconciliation",
    headers={"Authorization": "Bearer dev-admin-token"},
)
with urllib.request.urlopen(req, timeout=30) as r:
    recon = json.loads(r.read().decode())
print("=== reconciliation ===")
print(json.dumps(recon, ensure_ascii=False, indent=2)[:2500])

req2 = urllib.request.Request(
    "http://127.0.0.1:8016/api/v1/runtime/snapshot",
    headers={"Authorization": "Bearer dev-admin-token"},
)
with urllib.request.urlopen(req2, timeout=30) as r:
    snap = json.loads(r.read().decode())
ex = (snap.get("exchange") or {}).get("value") or {}
print("=== exchange positions ===", ex.get("positions"))
print("=== open_orders count ===", len(ex.get("open_orders") or []))
for o in (ex.get("open_orders") or [])[:8]:
    print({k: o.get(k) for k in ("symbol", "side", "orderType", "algoType", "clientAlgoId", "algoId", "reduceOnly")})
mm = snap.get("mismatch") or {}
print("=== mismatch ===", json.dumps(mm, ensure_ascii=False)[:1200])

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
print("=== local open/ghost positions ===")
for row in conn.execute(
    """
    SELECT position_record_id, symbol, position_side, quantity, status, provenance, paper_run_id, opened_at
    FROM position_records
    WHERE paper_run_id=? AND (ABS(quantity)>0 OR status LIKE '%OPEN%' OR status LIKE '%GHOST%' OR status LIKE '%RECON%')
    ORDER BY opened_at DESC LIMIT 20
    """,
    (RUN,),
):
    print(dict(row))
# also any open-ish across runs
print("=== any non-closed positions recent ===")
cols = [r[1] for r in conn.execute("PRAGMA table_info(position_records)")]
print("cols", cols)
for row in conn.execute(
    """
    SELECT position_record_id, symbol, position_side, quantity, status, paper_run_id, opened_at
    FROM position_records
    WHERE ABS(CAST(quantity AS REAL)) > 0
    ORDER BY opened_at DESC LIMIT 15
    """
):
    print(dict(row))
conn.close()
