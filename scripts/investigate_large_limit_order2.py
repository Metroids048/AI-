#!/usr/bin/env python3
"""调查8.363 ETH限价单来源：检查是否是系统下的还是用户手动在Binance上下的"""

import sqlite3

conn = sqlite3.connect(".local_paper_console.db")
cursor = conn.cursor()

print("=== 保护性挂单记录(protection_records)，检查止盈价1847附近 ===\n")
cursor.execute("""
    SELECT protection_record_id, position_record_id, stop_price, take_profit_price,
           protection_source, status, created_at, updated_at
    FROM protection_records
    ORDER BY updated_at DESC
    LIMIT 20
""")
for row in cursor.fetchall():
    print(
        f"pos={row[1][:12] if row[1] else None}... stop={row[2]} tp={row[3]} source={row[4]} status={row[5]} updated={row[7]}"
    )

print("\n=== 检查是否有take_profit_price接近1847的记录 ===\n")
cursor.execute("""
    SELECT protection_record_id, position_record_id, stop_price, take_profit_price, status, created_at
    FROM protection_records
    WHERE take_profit_price BETWEEN 1800 AND 1900
""")
rows = cursor.fetchall()
if rows:
    for row in rows:
        print(row)
else:
    print("没有找到 —— 说明这不是我们系统写入的保护记录")

print("\n=== 当前ETH总持仓数量核对（本地策略仓位总和） ===\n")
cursor.execute("""
    SELECT SUM(quantity) FROM position_records
    WHERE symbol='ETH/USDT' AND position_side='long' AND management_status='MANAGED_STRATEGY'
""")
print(f"策略多头ETH总量: {cursor.fetchone()[0]}")

cursor.execute("""
    SELECT SUM(quantity) FROM position_records
    WHERE symbol='ETH/USDT' AND management_status='UNMANAGED_EXTERNAL_POSITION'
""")
print(f"手动/未托管ETH总量: {cursor.fetchone()[0]}")

conn.close()
