#!/usr/bin/env python3
"""检查UNMANAGED持仓的真实来源"""

import sqlite3


def main():
    conn = sqlite3.connect(".local_paper_console.db")
    cursor = conn.cursor()

    print("\n" + "=" * 80)
    print("检查UNMANAGED持仓的真实来源")
    print("=" * 80)

    # 检查2026-07-24 03:58前后的订单记录
    print("\n=== 1. 2026-07-24 03:50-04:10的所有订单 ===")
    cursor.execute("""
        SELECT created_at, symbol, direction, execution_status, order_origin, gateway_order_id
        FROM order_executions
        WHERE created_at BETWEEN '2026-07-24 03:50:00' AND '2026-07-24 04:10:00'
        ORDER BY created_at
    """)

    orders = cursor.fetchall()
    if orders:
        for row in orders:
            print(f"{row[0]} | {row[1]} | {row[2]} | {row[3]} | {row[4]} | ID:{row[5]}")
    else:
        print("  没有找到订单记录")

    # 检查reconciliation_records
    print("\n=== 2. 对账记录 ===")
    cursor.execute("""
        SELECT reconciliation_time, symbol, discrepancy_type, resolution_action
        FROM reconciliation_records
        WHERE reconciliation_time >= '2026-07-24 03:50:00'
        ORDER BY reconciliation_time DESC
        LIMIT 10
    """)

    reconcile = cursor.fetchall()
    if reconcile:
        print("最近的对账记录:")
        for row in reconcile:
            print(f"  {row[0]} | {row[1]} | {row[2]} | {row[3]}")
    else:
        print("  没有对账记录")

    # 关键问题：这些持仓是否真的在Binance上存在？
    print("\n=== 3. 关键判断 ===")
    print("\n这两个UNMANAGED持仓的标记来源是 'external_reconciliation'")
    print("标记时间: 2026-07-24 03:58:45")
    print("\n可能的情况:")
    print("  A. 您确实在Binance testnet手动开了这两个仓位")
    print("  B. 系统自动开的但被错误标记为UNMANAGED")
    print("  C. 对账逻辑错误，把策略仓位误认为外部仓位")

    # 查看该run在那个时间点之前的策略订单
    print("\n=== 4. 该run在03:58之前的策略订单 ===")
    cursor.execute("""
        SELECT created_at, symbol, direction, execution_status, gateway_order_id
        FROM order_executions
        WHERE order_origin = 'live_scheduler'
          AND created_at < '2026-07-24 03:58:45'
        ORDER BY created_at DESC
        LIMIT 20
    """)

    strategy_orders = cursor.fetchall()
    if strategy_orders:
        print(f"找到{len(strategy_orders)}条策略订单:")
        btc_long_found = False
        eth_long_found = False

        for row in strategy_orders:
            print(f"  {row[0]} | {row[1]} | {row[2]} | {row[3]} | ID:{row[4]}")
            if row[1] == "BTC/USDT" and row[2] == "long":
                btc_long_found = True
            if row[1] == "ETH/USDT" and row[2] == "long":
                eth_long_found = True

        print(f"\n策略是否开过BTC多单: {'是' if btc_long_found else '否'}")
        print(f"策略是否开过ETH多单: {'是' if eth_long_found else '否'}")
    else:
        print("  没有找到策略订单")

    conn.close()

    print("\n" + "=" * 80)
    print("请您确认:")
    print("=" * 80)
    print("1. 您是否在Binance testnet手动开过BTC/USDT多单(约0.4044个)?")
    print("2. 您是否在Binance testnet手动开过ETH/USDT多单(约21.117个)?")
    print("\n如果都不是您手动开的，那么这些可能是:")
    print("  - 系统自动开的但被错误标记")
    print("  - 对账逻辑的bug")
    print("=" * 80)


if __name__ == "__main__":
    main()
