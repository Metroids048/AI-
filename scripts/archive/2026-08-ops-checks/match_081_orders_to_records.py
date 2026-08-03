#!/usr/bin/env python3
"""核实那批0.081/0.086 ETH的市价止盈止损单是否是系统下的"""

import sqlite3

conn = sqlite3.connect(".local_paper_console.db")
cursor = conn.cursor()

print("=== 本地protection_records里数量在0.08-0.09区间的ETH记录 ===\n")
cursor.execute("""
    SELECT pr.position_record_id, p.quantity, pr.stop_price, pr.take_profit_price, pr.updated_at
    FROM protection_records pr
    JOIN position_records p ON pr.position_record_id = p.position_record_id
    WHERE p.symbol = 'ETH/USDT' AND p.quantity BETWEEN 0.07 AND 0.10
    ORDER BY pr.updated_at DESC
""")
rows = cursor.fetchall()
if rows:
    for row in rows:
        print(row)
else:
    print("没有匹配的本地记录 — 说明这批也不是当前策略仓位的量级")

print("\n=== 对比: 系统里ETH策略仓位实际数量都是多少 ===\n")
cursor.execute("""
    SELECT quantity, opened_at FROM position_records
    WHERE symbol='ETH/USDT' AND management_status='MANAGED_STRATEGY'
    ORDER BY opened_at DESC
""")
for row in cursor.fetchall():
    print(row)

conn.close()
