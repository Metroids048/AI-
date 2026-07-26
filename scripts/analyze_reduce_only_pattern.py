#!/usr/bin/env python3
"""分析71条ReduceOnly拒绝的共同模式，找出根因"""

import json
import sqlite3

conn = sqlite3.connect(".local_paper_console.db")
cursor = conn.cursor()

cursor.execute("""
    SELECT created_at, symbol, direction, order_origin, paper_run_id, entry_context
    FROM order_executions
    WHERE rejection_reason LIKE '%ReduceOnly%'
    ORDER BY created_at DESC
    LIMIT 15
""")

rows = cursor.fetchall()
print("最近15条ReduceOnly拒绝：\n")
for row in rows:
    print(f"{row[0]} | {row[1]} {row[2]} | origin={row[3]} | run={row[4][:12] if row[4] else None}")
    if row[5]:
        try:
            ctx = json.loads(row[5])
            print(
                f"  close_only_mode={ctx.get('close_only_mode')} reduce_only={ctx.get('reduce_only')} "
                f"quantity={ctx.get('quantity')} remaining_quantity={ctx.get('remaining_quantity')}"
            )
        except Exception as e:
            print(f"  (parse failed: {e})")
    print()

# 统计症状分布：symbol+direction组合
print("\n=== symbol+direction组合统计 ===\n")
cursor.execute("""
    SELECT symbol, direction, COUNT(*) as cnt
    FROM order_executions
    WHERE rejection_reason LIKE '%ReduceOnly%'
    GROUP BY symbol, direction
    ORDER BY cnt DESC
""")
for row in cursor.fetchall():
    print(row)

conn.close()
