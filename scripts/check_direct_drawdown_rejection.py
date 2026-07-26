#!/usr/bin/env python3
"""检查drawdown_limit_breached(不带blocking_risk_event)的拒绝，找出走的是哪条代码路径"""

import sqlite3

conn = sqlite3.connect(".local_paper_console.db")
cursor = conn.cursor()

cursor.execute("""
    SELECT created_at, symbol, direction, rejection_reason
    FROM order_executions
    WHERE rejection_reason LIKE '%drawdown_limit_breached%'
    ORDER BY created_at DESC
    LIMIT 20
""")

print("\n=== 所有drawdown_limit_breached相关的拒绝记录 ===\n")
for row in cursor.fetchall():
    print(f"{row[0]} | {row[1]} {row[2]} | {row[3]}")

conn.close()
