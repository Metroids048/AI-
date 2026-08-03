"""清空 processed_cycle_keys，强制系统重新评估所有K线

警告：这会让系统重新评估最近的K线，可能产生重复订单。
但当前系统卡在 skip_duplicate 循环里，必须清空才能继续。
"""

import json
import sqlite3
from pathlib import Path

db = Path(__file__).parents[1] / ".local_paper_console.db"
conn = sqlite3.connect(str(db))

# 读取当前状态
row = conn.execute("SELECT paper_metrics_summary FROM paper_runs WHERE paper_run_id LIKE '35298c65%'").fetchone()
m = json.loads(row[0])

print(f"当前 processed_cycle_keys 数量: {len(m.get('processed_cycle_keys', []))}")
print(f"unmanaged_external_symbols: {m.get('unmanaged_external_symbols')}")

# 清空 processed_cycle_keys
m["processed_cycle_keys"] = []

# 写回DB
conn.execute("UPDATE paper_runs SET paper_metrics_summary = ? WHERE paper_run_id LIKE '35298c65%'", (json.dumps(m),))
conn.commit()
conn.close()

print("\n✅ processed_cycle_keys 已清空")
print("重启系统后，下一个K线会重新评估信号")
