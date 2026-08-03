"""提取 execution_contract_rejected 事件的 payload，看具体 ValueError 内容"""

import json
import sqlite3
from pathlib import Path

db = Path(__file__).parents[1] / ".local_paper_console.db"
conn = sqlite3.connect(str(db))
conn.row_factory = sqlite3.Row

rows = conn.execute(
    """
    SELECT symbol, candle_close_time, payload, created_at
    FROM decision_events
    WHERE event_type = 'execution_contract_rejected'
    ORDER BY created_at DESC
    LIMIT 10
    """
).fetchall()

print("=== execution_contract_rejected 详细错误 ===\n")
for r in rows:
    p = json.loads(r["payload"] or "{}")
    error = p.get("error", "(无error字段)")
    print(f"{r['created_at']} | {r['symbol']}")
    print(f"  ⚠️  {error}\n")

conn.close()
