#!/usr/bin/env python3
"""查找今天的BTC多单记录"""

import sqlite3


def main():
    conn = sqlite3.connect(".local_paper_console.db")
    cursor = conn.cursor()

    print("\n" + "=" * 80)
    print("查找今天（2026-07-25）的BTC多单记录")
    print("=" * 80)

    # 查找今天的BTC多单
    cursor.execute("""
        SELECT created_at, symbol, direction, execution_status, order_origin, gateway_order_id, gateway_status
        FROM order_executions
        WHERE symbol = 'BTC/USDT'
          AND direction = 'long'
          AND created_at >= '2026-07-25 00:00:00'
        ORDER BY created_at DESC
    """)

    rows = cursor.fetchall()

    if rows:
        print(f"\n找到 {len(rows)} 条BTC多单记录:\n")
        for row in rows:
            print(f"时间: {row[0]}")
            print(f"  方向: {row[2]}")
            print(f"  状态: {row[3]}")
            print(f"  来源: {row[4]}")
            print(f"  网关ID: {row[5]}")
            print(f"  网关状态: {row[6]}\n")
    else:
        print("\n❌ 今天没有找到BTC多单的订单记录")
        print("\n但position_records里有一个BTC多单(0.4044)，标记为UNMANAGED")
        print("开仓时间: 2026-07-24 03:58:45")
        print("来源: external_reconciliation")

    # 查找昨天到今天凌晨的BTC多单
    print("\n" + "=" * 80)
    print("查找昨天晚上到今天凌晨(2026-07-24 20:00 - 2026-07-25 06:00)的BTC多单")
    print("=" * 80)

    cursor.execute("""
        SELECT created_at, symbol, direction, execution_status, order_origin, gateway_order_id
        FROM order_executions
        WHERE symbol = 'BTC/USDT'
          AND direction = 'long'
          AND created_at BETWEEN '2026-07-24 20:00:00' AND '2026-07-25 06:00:00'
        ORDER BY created_at DESC
    """)

    rows = cursor.fetchall()

    if rows:
        print(f"\n找到 {len(rows)} 条:\n")
        for row in rows:
            print(f"{row[0]} | {row[2]} | {row[3]} | 来源:{row[4]} | ID:{row[5]}")
    else:
        print("\n❌ 这个时间段也没有BTC多单记录")

    # 检查position_records中BTC多单的entry_order_id
    print("\n" + "=" * 80)
    print("检查BTC多单持仓记录的详细信息")
    print("=" * 80)

    cursor.execute("""
        SELECT position_record_id, symbol, position_side, quantity,
               management_status, order_origin, entry_order_id, entry_fill_id,
               opened_at, strategy_id, run_id
        FROM position_records
        WHERE symbol = 'BTC/USDT'
          AND position_side = 'long'
          AND opened_at >= '2026-07-24 00:00:00'
        ORDER BY opened_at DESC
    """)

    rows = cursor.fetchall()

    if rows:
        print(f"\n找到 {len(rows)} 条BTC多单持仓记录:\n")
        for row in rows:
            print(f"持仓ID: {row[0][:12]}...")
            print(f"  数量: {row[3]}")
            print(f"  管理状态: {row[4]}")
            print(f"  来源: {row[5]}")
            print(f"  入场订单ID: {row[6]}")
            print(f"  入场成交ID: {row[7]}")
            print(f"  开仓时间: {row[8]}")
            print(f"  策略ID: {row[9]}")
            print(f"  Run ID: {row[10][:12]}...\n")

            # 如果有entry_order_id，查找对应的订单
            if row[6]:
                cursor.execute(
                    """
                    SELECT created_at, symbol, direction, execution_status, order_origin
                    FROM order_executions
                    WHERE order_execution_id = ?
                """,
                    (row[6],),
                )
                order = cursor.fetchone()
                if order:
                    print(f"  对应订单: {order[0]} | {order[2]} | {order[3]} | {order[4]}")
                else:
                    print("  ⚠️  找不到对应的订单记录")

    conn.close()

    print("\n" + "=" * 80)
    print("结论")
    print("=" * 80)
    print("\n如果找不到BTC多单的order_executions记录，说明:")
    print("  1. 这个持仓可能是系统启动前就存在的")
    print("  2. 或者是对账时从Binance发现的外部持仓")
    print("  3. 或者订单记录因某种原因丢失了")
    print("\n需要确认: 这个BTC多单是否真的在Binance testnet上存在？")


if __name__ == "__main__":
    main()
