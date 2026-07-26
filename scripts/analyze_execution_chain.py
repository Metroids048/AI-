#!/usr/bin/env python3
"""分析执行链路断点"""

import sqlite3


def main():
    conn = sqlite3.connect(".local_paper_console.db")
    cursor = conn.cursor()

    # 1. 统计过去24小时的订单状态分布
    print("\n=== 1. 过去24小时订单状态分布 ===")
    cursor.execute("""
        SELECT execution_status, COUNT(*) as cnt
        FROM order_executions
        WHERE created_at >= datetime('now','-1 day')
        GROUP BY execution_status
        ORDER BY cnt DESC;
    """)
    for row in cursor.fetchall():
        print(f"{row[0]}: {row[1]} 条")

    # 2. 统计拒绝原因
    print("\n=== 2. 拒绝原因分布 ===")
    cursor.execute("""
        SELECT rejection_reason, COUNT(*) as cnt
        FROM order_executions
        WHERE created_at >= datetime('now','-1 day')
          AND execution_status = 'rejected'
        GROUP BY rejection_reason
        ORDER BY cnt DESC;
    """)
    for row in cursor.fetchall():
        print(f"{row[0]}: {row[1]} 条")

    # 3. 找出有gateway_order_id的订单（真正到交易所的）
    print("\n=== 3. 真正提交到Binance的订单 ===")
    cursor.execute("""
        SELECT created_at, symbol, direction, execution_status, gateway_order_id, gateway_status
        FROM order_executions
        WHERE created_at >= datetime('now','-1 day')
          AND gateway_order_id IS NOT NULL
        ORDER BY created_at DESC;
    """)
    rows = cursor.fetchall()
    print(f"共 {len(rows)} 条有网关订单ID的记录：")
    for row in rows:
        print(f"  {row[0]} | {row[1]} | {row[2]} | {row[3]} | ID:{row[4]} | {row[5]}")

    # 4. 找出filled状态但没有gateway_order_id的订单（Paper-only）
    print("\n=== 4. Paper-only成交订单（无网关ID） ===")
    cursor.execute("""
        SELECT created_at, symbol, direction, order_origin
        FROM order_executions
        WHERE created_at >= datetime('now','-1 day')
          AND execution_status = 'filled'
          AND gateway_order_id IS NULL
        ORDER BY created_at DESC;
    """)
    rows = cursor.fetchall()
    print(f"共 {len(rows)} 条Paper-only成交：")
    for row in rows:
        print(f"  {row[0]} | {row[1]} | {row[2]} | 来源:{row[3]}")

    # 5. 查找blocking_risk_event的时间分布
    print("\n=== 5. blocking_risk_event 拒绝时间分布 ===")
    cursor.execute("""
        SELECT created_at, symbol, direction
        FROM order_executions
        WHERE created_at >= datetime('now','-1 day')
          AND rejection_reason LIKE '%blocking_risk_event%'
        ORDER BY created_at DESC
        LIMIT 10;
    """)
    rows = cursor.fetchall()
    print("最近10条blocking_risk_event拒绝：")
    for row in rows:
        print(f"  {row[0]} | {row[1]} | {row[2]}")

    # 6. 检查是否有active的风险事件
    print("\n=== 6. 当前活跃的风险事件 ===")
    cursor.execute("""
        SELECT event_type, severity, started_at, ended_at, description
        FROM risk_events
        WHERE ended_at IS NULL OR ended_at > datetime('now','-1 day')
        ORDER BY started_at DESC;
    """)
    rows = cursor.fetchall()
    if rows:
        print(f"发现 {len(rows)} 个活跃或近期风险事件：")
        for row in rows:
            status = "🔴 活跃中" if row[3] is None else f"✅ 已结束于 {row[3]}"
            print(f"  {row[0]} | 严重度:{row[1]} | 开始:{row[2]} | {status}")
            print(f"    描述: {row[4]}")
    else:
        print("✅ 没有活跃的风险事件")

    conn.close()

    # 7. 结论
    print("\n" + "=" * 80)
    print("=== 诊断结论 ===")
    print("=" * 80)


if __name__ == "__main__":
    main()
