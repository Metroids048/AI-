#!/usr/bin/env python3
"""检查监控启动(10:26:05)以来的所有订单和风险事件"""

import json
import sqlite3

conn = sqlite3.connect(".local_paper_console.db")
cursor = conn.cursor()

since = "2026-07-25 10:26:05"

print(f"\n=== 从 {since} 开始的订单记录 ===\n")
cursor.execute(
    """
    SELECT created_at, symbol, direction, execution_status, rejection_reason, gateway_order_id, evaluated_risk_state
    FROM order_executions
    WHERE created_at > ?
    ORDER BY created_at ASC
""",
    (since,),
)

rows = cursor.fetchall()
if not rows:
    print("❌ 没有任何新订单产生（这段时间调度器可能没有生成入场信号）")
else:
    for row in rows:
        print(f"{row[0]} | {row[1]} {row[2]} | {row[3]} | reject={row[4]} | gw={row[5]}")
        if row[6]:
            try:
                rs = json.loads(row[6])
                print(f"  account_equity={rs.get('account_equity')} equity_peak={rs.get('equity_peak')}")
            except Exception as e:
                print(f"  (parse failed: {e})")

print(f"\n=== 从 {since} 开始的风险事件 ===\n")
cursor.execute(
    """
    SELECT created_at, event_type, description, expires_at
    FROM risk_events
    WHERE created_at > ?
    ORDER BY created_at ASC
""",
    (since,),
)

rows = cursor.fetchall()
if not rows:
    print("✅ 没有产生新的风险事件")
else:
    for row in rows:
        print(f"{row[0]} | {row[1]} | expires={row[3]} | {row[2][:70]}")

print("\n=== 调度周期执行情况 ===\n")
cursor.execute(
    """
    SELECT started_at, completed_at, status
    FROM scheduler_cycles
    WHERE started_at > ?
    ORDER BY started_at ASC
""",
    (since,),
)

rows = cursor.fetchall()
print(f"共 {len(rows)} 个周期")
for row in rows[-10:]:
    print(f"{row[0]} -> {row[1]} | {row[2]}")

print("\n=== 决策快照统计（action分布） ===\n")
cursor.execute(
    """
    SELECT action, COUNT(*) as cnt
    FROM decision_snapshots
    WHERE created_at > ?
    GROUP BY action
    ORDER BY cnt DESC
""",
    (since,),
)

for row in cursor.fetchall():
    print(f"{row[0]}: {row[1]}")

conn.close()
