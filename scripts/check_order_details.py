"""检查订单详情和拒单原因"""

import sqlite3

conn = sqlite3.connect(".local_paper_console.db")
cursor = conn.cursor()

print("=" * 100)
print("【最近10条订单详情】")
print("=" * 100)
cursor.execute("""
    SELECT
        created_at,
        symbol,
        direction,
        execution_status,
        rejection_reason
    FROM order_executions
    ORDER BY created_at DESC
    LIMIT 10
""")

for row in cursor.fetchall():
    reason = (row[4] or "N/A")[:80]
    direction = row[2] or "None"
    status = row[3] or "None"
    print(f"{row[0]} | {row[1]:10} | {direction:5} | {status:15} | {reason}")

print("\n" + "=" * 100)
print("【订单状态统计】")
print("=" * 100)
cursor.execute("""
    SELECT execution_status, COUNT(*)
    FROM order_executions
    GROUP BY execution_status
    ORDER BY COUNT(*) DESC
""")
for status, count in cursor.fetchall():
    status_str = status or "NULL"
    print(f"  {status_str:20}: {count}")

print("\n" + "=" * 100)
print("【拒单原因统计（Top 20）】")
print("=" * 100)
cursor.execute("""
    SELECT rejection_reason, COUNT(*) as count
    FROM order_executions
    WHERE execution_status = 'rejected' AND rejection_reason IS NOT NULL
    GROUP BY rejection_reason
    ORDER BY count DESC
    LIMIT 20
""")
for reason, count in cursor.fetchall():
    print(f"  {count:4}x {reason[:100]}")

print("\n" + "=" * 100)
print("【最近3小时订单数】")
print("=" * 100)
cursor.execute("""
    SELECT COUNT(*)
    FROM order_executions
    WHERE created_at >= datetime('now', '-3 hours')
""")
count_3h = cursor.fetchone()[0]
print(f"  最近3小时: {count_3h} 条订单")

cursor.execute("""
    SELECT COUNT(*)
    FROM order_executions
    WHERE created_at >= datetime('now', '-1 hour')
""")
count_1h = cursor.fetchone()[0]
print(f"  最近1小时: {count_1h} 条订单")

print("\n" + "=" * 100)
print("【今天的订单】")
print("=" * 100)
cursor.execute("""
    SELECT
        created_at,
        symbol,
        direction,
        execution_status,
        rejection_reason
    FROM order_executions
    WHERE DATE(created_at) = DATE('now')
    ORDER BY created_at DESC
""")
today_orders = cursor.fetchall()
print(f"  今天共 {len(today_orders)} 条订单")
for row in today_orders[:5]:
    reason = (row[4] or "N/A")[:60]
    direction = row[2] or "None"
    status = row[3] or "None"
    print(f"  {row[0]} | {row[1]:10} | {direction:5} | {status:15} | {reason}")

conn.close()
print("\n" + "=" * 100)
