import sqlite3

conn = sqlite3.connect(".local_paper_console.db")
conn.row_factory = sqlite3.Row
print("position_records cols", [r[1] for r in conn.execute("PRAGMA table_info(position_records)")])
RUN = "78ba69a7-2bfb-457e-9a97-934aaf418e00"
q = "SELECT * FROM position_records WHERE paper_run_id=? ORDER BY opened_at DESC LIMIT 12"
for row in conn.execute(q, (RUN,)):
    d = dict(row)
    keep = {
        k: d.get(k)
        for k in d
        if k
        in (
            "position_record_id",
            "symbol",
            "position_side",
            "quantity",
            "management_status",
            "lifecycle_status",
            "position_status",
            "provenance",
            "opened_at",
            "closed_at",
            "paper_run_id",
        )
        or "status" in k
        or "management" in k
        or "qty" in k.lower()
    }
    print(keep or {k: d[k] for k in list(d)[:12]})
# order_executions sampling rejections
print("=== recent sampling-ish order_executions ===")
try:
    for row in conn.execute(
        """
        SELECT symbol, execution_status, rejection_reason, created_at, gateway_order_id
        FROM order_executions
        WHERE paper_run_id=?
        ORDER BY created_at DESC LIMIT 15
        """,
        (RUN,),
    ):
        print(dict(row))
except Exception as e:
    print("oe err", e)
conn.close()
