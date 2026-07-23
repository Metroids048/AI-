"""检查SQLite数据库中的订单数据"""

import sqlite3
from pathlib import Path

db_path = Path(".local_paper_console.db")
if not db_path.exists():
    print(f"⚠️  数据库文件不存在: {db_path}")
    exit(1)

conn = sqlite3.connect(str(db_path))
cursor = conn.cursor()

print("=" * 80)
print("SQLite数据库诊断")
print("=" * 80)

# 1. 检查表
print("\n【1. 数据库表列表】")
cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
tables = [t[0] for t in cursor.fetchall()]
for table in tables:
    cursor.execute(f"SELECT COUNT(*) FROM {table}")
    count = cursor.fetchone()[0]
    print(f"  {table:35} {count:6} 行")

# 2. 检查order_executions表结构
if "order_executions" in tables:
    print("\n【2. order_executions 表结构】")
    cursor.execute("PRAGMA table_info(order_executions)")
    columns = cursor.fetchall()
    col_names = [col[1] for col in columns]
    for col in columns[:20]:  # 只显示前20列
        print(f"  {col[1]:30} {col[2]:15}")
    if len(columns) > 20:
        print(f"  ... 还有 {len(columns) - 20} 列")

    # 3. 统计订单
    print("\n【3. 订单统计】")
    cursor.execute("SELECT COUNT(*) FROM order_executions")
    total = cursor.fetchone()[0]
    print(f"  总订单数: {total}")

    if total > 0:
        # 最近的订单
        print("\n【4. 最近20条订单】")
        # 使用实际存在的列名
        select_cols = []
        for col in ["created_at", "symbol", "order_side", "status", "rejection_reason", "strategy_key"]:
            if col in col_names:
                select_cols.append(col)

        query = f"SELECT {', '.join(select_cols)} FROM order_executions ORDER BY created_at DESC LIMIT 20"
        cursor.execute(query)
        orders = cursor.fetchall()

        for o in orders:
            print(f"  {o}")

        # 状态统计
        print("\n【5. 订单状态统计】")
        cursor.execute("""
            SELECT status, COUNT(*)
            FROM order_executions
            GROUP BY status
            ORDER BY COUNT(*) DESC
        """)
        for status, count in cursor.fetchall():
            print(f"  {status:15}: {count}")

        # 拒单原因
        cursor.execute("""
            SELECT COUNT(*)
            FROM order_executions
            WHERE status = 'rejected'
        """)
        rejected_count = cursor.fetchone()[0]

        if rejected_count > 0:
            print("\n【6. 拒单原因（Top 10）】")
            cursor.execute("""
                SELECT rejection_reason, COUNT(*) as count
                FROM order_executions
                WHERE status = 'rejected' AND rejection_reason IS NOT NULL
                GROUP BY rejection_reason
                ORDER BY count DESC
                LIMIT 10
            """)
            for reason, count in cursor.fetchall():
                print(f"  {count:4}x {reason[:70]}")
else:
    print("\n⚠️  order_executions 表不存在！")

conn.close()
print("\n" + "=" * 80)
