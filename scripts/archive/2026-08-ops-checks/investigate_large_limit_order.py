#!/usr/bin/env python3
"""调查那个8.363 ETH限价止盈单的来源，以及当前ETH真实价格"""

import sqlite3

conn = sqlite3.connect(".local_paper_console.db")
cursor = conn.cursor()

print("=== 1. 检查ETH持仓记录，看8.363这个数量对应哪个持仓 ===\n")
cursor.execute("""
    SELECT position_record_id, symbol, position_side, quantity, management_status,
           order_origin, opened_at, run_id
    FROM position_records
    WHERE symbol = 'ETH/USDT'
    ORDER BY opened_at DESC
""")
for row in cursor.fetchall():
    print(
        f"id={row[0][:12]}... side={row[2]} qty={row[3]:.4f} status={row[4]} origin={row[5]} opened={row[6]} run={row[7][:12] if row[7] else None}..."
    )

print("\n=== 2. 检查最近的保护性挂单记录(protection_records) ===\n")
cursor.execute("""
    SELECT protection_record_id, position_record_id, status, stop_price, take_profit_price,
           quantity, created_at, updated_at
    FROM protection_records
    ORDER BY updated_at DESC
    LIMIT 15
""")
for row in cursor.fetchall():
    print(
        f"pos={row[1][:12] if row[1] else None}... status={row[2]} stop={row[3]} tp={row[4]} qty={row[5]} updated={row[7]}"
    )

print("\n=== 3. 检查order_executions里是否有对应8.363 ETH的记录 ===\n")
cursor.execute("""
    SELECT created_at, symbol, direction, execution_status, order_origin, gateway_order_id
    FROM order_executions
    WHERE symbol = 'ETH/USDT'
    ORDER BY created_at DESC
    LIMIT 20
""")
for row in cursor.fetchall():
    print(f"{row[0]} | {row[2]} | {row[3]} | origin={row[4]} | gw={row[5]}")

conn.close()
