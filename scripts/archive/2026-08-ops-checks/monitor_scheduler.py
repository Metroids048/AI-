#!/usr/bin/env python3
"""监控调度器运行和订单生成情况"""

import sqlite3
from datetime import UTC, datetime


def main():
    conn = sqlite3.connect(".local_paper_console.db")
    cursor = conn.cursor()

    # 1. 最近的调度周期
    print("\n=== 1. 最近5次调度周期 ===")
    cursor.execute("""
        SELECT job_name, started_at, completed_at, status, failure_reason
        FROM scheduler_cycles
        ORDER BY started_at DESC
        LIMIT 5
    """)
    rows = cursor.fetchall()
    if rows:
        for row in rows:
            status_icon = "✅" if row[3] == "completed" else "❌"
            print(f"{status_icon} {row[0]}")
            print(f"   开始: {row[1]} | 结束: {row[2]} | 状态: {row[3]}")
            if row[4]:
                print(f"   失败原因: {row[4]}")
    else:
        print("❌ 没有找到调度周期记录")

    # 2. 最近30分钟的订单
    print("\n=== 2. 最近30分钟的订单尝试 ===")
    cursor.execute("""
        SELECT created_at, symbol, direction, execution_status, order_origin, gateway_order_id, rejection_reason
        FROM order_executions
        WHERE created_at > datetime('now', '-30 minutes')
        ORDER BY created_at DESC
    """)
    rows = cursor.fetchall()
    if rows:
        print(f"共 {len(rows)} 条:")
        for row in rows:
            gateway = row[5] if row[5] else "NULL"
            status_icon = "✅" if row[3] == "filled" else ("⏳" if row[3] == "submitted" else "❌")
            print(f"{status_icon} {row[0]} | {row[1]} | {row[2]} | {row[3]} | 来源:{row[4]}")
            print(f"   网关ID: {gateway}")
            if row[6]:
                print(f"   拒绝原因: {row[6]}")
    else:
        print("❌ 没有新的订单尝试")

    # 3. 最近1小时的订单统计
    print("\n=== 3. 最近1小时的订单统计 ===")
    cursor.execute("""
        SELECT execution_status, COUNT(*) as cnt
        FROM order_executions
        WHERE created_at > datetime('now', '-1 hour')
        GROUP BY execution_status
    """)
    stats = cursor.fetchall()
    if stats:
        for row in stats:
            print(f"  {row[0]}: {row[1]} 条")
    else:
        print("  无订单")

    # 4. 检查是否有真实的Binance订单
    print("\n=== 4. 最近1小时真实提交到Binance的订单 ===")
    cursor.execute("""
        SELECT created_at, symbol, direction, execution_status, gateway_order_id, gateway_status
        FROM order_executions
        WHERE created_at > datetime('now', '-1 hour')
          AND gateway_order_id IS NOT NULL
        ORDER BY created_at DESC
    """)
    binance_orders = cursor.fetchall()
    if binance_orders:
        print(f"✅ 找到 {len(binance_orders)} 条Binance订单:")
        for row in binance_orders:
            print(f"  {row[0]} | {row[1]} | {row[2]} | {row[3]} | ID:{row[4]} | {row[5]}")
    else:
        print("❌ 没有真实提交到Binance的订单")

    # 5. 当前时间
    now_utc = datetime.now(UTC)
    now_local = datetime.now()
    print(f"\n当前UTC时间: {now_utc.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"当前本地时间: {now_local.strftime('%Y-%m-%d %H:%M:%S')}")

    conn.close()

    print("\n" + "=" * 80)
    print("💡 验收目标：")
    print("   1. 调度周期正常运行（每15分钟一次）")
    print("   2. 有新的订单尝试（不管是否被拒绝）")
    print("   3. 至少有1条订单真实提交到Binance（有gateway_order_id）")
    print("=" * 80)


if __name__ == "__main__":
    main()
