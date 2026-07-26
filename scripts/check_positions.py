#!/usr/bin/env python3
"""检查Binance持仓和本地持仓记录"""

import json
import sqlite3


def main():
    conn = sqlite3.connect(".local_paper_console.db")
    cursor = conn.cursor()

    # 1. 查看position_records中的持仓
    print("\n=== 1. position_records中的持仓 ===")
    cursor.execute("""
        SELECT position_record_id, symbol, position_side, quantity, management_status,
               opened_at, entry_order_id, order_origin
        FROM position_records
        ORDER BY opened_at DESC
        LIMIT 10
    """)
    rows = cursor.fetchall()
    if rows:
        print(f"共 {len(rows)} 条:")
        for row in rows:
            print(f"\nID: {row[0][:12]}...")
            print(f"  {row[1]} | {row[2]} | 数量:{row[3]} | 状态:{row[4]}")
            print(f"  开仓:{row[5]} | 订单ID:{row[6]} | 来源:{row[7]}")
    else:
        print("没有持仓记录")

    # 2. 查看exchange_account_snapshots中最新的持仓快照
    print("\n=== 2. 最新的交易所账户快照 ===")
    cursor.execute("""
        SELECT snapshot_id, snapshot_time, total_wallet_balance, total_unrealized_pnl,
               total_margin_balance, available_balance, positions
        FROM exchange_account_snapshots
        ORDER BY snapshot_time DESC
        LIMIT 1
    """)
    row = cursor.fetchone()
    if row:
        print(f"快照时间: {row[1]}")
        print(f"钱包余额: {row[2]}")
        print(f"未实现盈亏: {row[3]}")
        print(f"保证金余额: {row[4]}")
        print(f"可用余额: {row[5]}")
        if row[6]:
            try:
                positions = json.loads(row[6]) if isinstance(row[6], str) else row[6]
                print("\n持仓详情:")
                if positions:
                    for pos in positions:
                        print(
                            f"  {pos.get('symbol')} | {pos.get('positionSide')} | 数量:{pos.get('positionAmt')} | 未实现:{pos.get('unrealizedProfit')}"
                        )
                else:
                    print("  无持仓")
            except Exception as e:
                print(f"  解析失败: {e}")
    else:
        print("没有找到账户快照")

    # 3. 查看最近的对账记录
    print("\n=== 3. 最近的对账记录 ===")
    cursor.execute("""
        SELECT reconciliation_id, reconciliation_time, symbol, discrepancy_type,
               resolution_action, notes
        FROM reconciliation_records
        ORDER BY reconciliation_time DESC
        LIMIT 5
    """)
    rows = cursor.fetchall()
    if rows:
        print(f"共 {len(rows)} 条:")
        for row in rows:
            print(f"\n时间: {row[1]}")
            print(f"  {row[2]} | 差异类型:{row[3]}")
            print(f"  解决方案:{row[4]}")
            if row[5]:
                print(f"  备注:{row[5][:100]}")
    else:
        print("没有对账记录")

    conn.close()


if __name__ == "__main__":
    main()
