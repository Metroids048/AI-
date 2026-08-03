#!/usr/bin/env python3
"""检查19:03:23重启以来的完整运行情况"""

import json
import sqlite3

conn = sqlite3.connect(".local_paper_console.db")
cursor = conn.cursor()

since = "2026-07-25 11:03:49"

print(f"\n=== 调度周期执行情况（自 {since} UTC） ===\n")
cursor.execute(
    """
    SELECT started_at, completed_at, status, failure_reason
    FROM scheduler_cycles
    WHERE started_at > ?
    ORDER BY started_at ASC
""",
    (since,),
)
rows = cursor.fetchall()
print(f"共 {len(rows)} 个周期")
if rows:
    completed = [r for r in rows if r[2] == "completed"]
    print(f"成功: {len(completed)} / {len(rows)}")
    print("\n最近5个:")
    for row in rows[-5:]:
        print(f"  {row[0]} -> {row[1]} | {row[2]} | {row[3] or ''}")
else:
    print("❌ 没有任何调度周期记录 —— 调度器可能没在跑")

print("\n=== 订单记录 ===\n")
cursor.execute(
    """
    SELECT order_execution_id, created_at, symbol, direction, execution_status, rejection_reason, gateway_order_id, evaluated_risk_state
    FROM order_executions
    WHERE created_at > ?
    ORDER BY created_at ASC
""",
    (since,),
)
rows = cursor.fetchall()
if not rows:
    print("❌ 没有产生任何订单")
else:
    for row in rows:
        print(f"{row[1]} | {row[2]} {row[3]} | {row[4]} | reject={row[5]} | gw={row[6]}")
        if row[7]:
            try:
                rs = json.loads(row[7])
                eq = rs.get("account_equity")
                peak = rs.get("equity_peak")
                dd = None
                if eq is not None and peak and peak > 0:
                    dd = round(max(0.0, (peak - eq) / peak) * 100, 2)
                print(f"  account_equity={eq} equity_peak={peak} implied_drawdown={dd}%")
            except Exception as e:
                print(f"  (parse failed: {e})")

print("\n=== 决策快照action分布 ===\n")
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

print("\n=== 风险事件 ===\n")
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
    print("没有新的风险事件")
else:
    for row in rows:
        print(f"{row[0]} | {row[1]} | expires={row[3]} | {row[2][:70]}")

print("\n=== 当前活跃风险事件（截至现在） ===\n")
cursor.execute("""
    SELECT COUNT(*) FROM risk_events
    WHERE resolution_status IN ('detected', 'acknowledged')
      AND (expires_at IS NULL OR expires_at > datetime('now'))
""")
print(f"活跃数量: {cursor.fetchone()[0]}")

conn.close()
