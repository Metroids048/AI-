import json
import sqlite3

conn = sqlite3.connect(".local_paper_console.db")
conn.row_factory = sqlite3.Row
RUN = "78ba69a7-2bfb-457e-9a97-934aaf418e00"
rows = conn.execute(
    """
  SELECT symbol, bar_time, created_at, reason_code, details
  FROM decision_funnel_terminals
  WHERE paper_run_id=?
  ORDER BY created_at DESC LIMIT 8
  """,
    (RUN,),
).fetchall()
for row in rows:
    details = json.loads(row["details"] or "{}")
    eval_at = (details.get("decision_trace") or {}).get("sampling_metrics", {}).get("evaluated_at")
    # also top-level
    print(
        row["symbol"],
        "bar",
        row["bar_time"],
        "funnel_created",
        row["created_at"],
        "eval",
        eval_at,
        "reason",
        row["reason_code"],
    )
# management_status positions
print("=== positions by management_status ===")
for row in conn.execute(
    """
  SELECT symbol, position_side, quantity, management_status, order_origin, opened_at, run_id
  FROM position_records
  WHERE ABS(CAST(quantity AS REAL)) > 0 OR management_status NOT IN ('CLOSED','closed')
  ORDER BY opened_at DESC LIMIT 20
  """
):
    print(dict(row))
conn.close()
