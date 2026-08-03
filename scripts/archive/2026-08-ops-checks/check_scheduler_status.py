#!/usr/bin/env python3
"""检查调度器状态和最近的执行情况"""

import sqlite3
from datetime import UTC, datetime


def main():
    conn = sqlite3.connect(".local_paper_console.db")
    cursor = conn.cursor()

    # 1. 调度器状态
    print("\n=== 1. 调度器状态 ===")
    cursor.execute("""
        SELECT last_run, next_run, state, exception_detail
        FROM scheduler_state
        ORDER BY last_run DESC
        LIMIT 1
    """)
    row = cursor.fetchone()
    if row:
        print(f"最后运行: {row[0]}")
        print(f"下次运行: {row[1]}")
        print(f"状态: {row[2]}")
        print(f"异常: {row[3] if row[3] else '无'}")

    # 2. 最近10分钟的订单尝试
    print("\n=== 2. 最近10分钟的订单尝试 ===")
    cursor.execute("""
        SELECT created_at, symbol, direction, execution_status, rejection_reason
        FROM order_executions
        WHERE created_at > datetime('now', '-10 minutes')
        ORDER BY created_at DESC
    """)
    rows = cursor.fetchall()
    if rows:
        print(f"共 {len(rows)} 条:")
        for row in rows:
            print(f"  {row[0]} | {row[1]} | {row[2]} | {row[3]} | {row[4]}")
    else:
        print("没有新的订单尝试")

    # 3. 最近30分钟的订单尝试
    print("\n=== 3. 最近30分钟的订单尝试 ===")
    cursor.execute("""
        SELECT created_at, symbol, direction, execution_status, rejection_reason
        FROM order_executions
        WHERE created_at > datetime('now', '-30 minutes')
        ORDER BY created_at DESC
    """)
    rows = cursor.fetchall()
    print(f"共 {len(rows)} 条")

    # 4. 统计当前时间
    now = datetime.now(UTC)
    print(f"\n当前UTC时间: {now.strftime('%Y-%m-%d %H:%M:%S')}")

    conn.close()


if __name__ == "__main__":
    main()
