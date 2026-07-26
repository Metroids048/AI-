#!/usr/bin/env python3
"""实时监控自动开平仓执行情况"""

import json
import sqlite3
import time
from datetime import datetime


def monitor():
    conn = sqlite3.connect(".local_paper_console.db")
    cursor = conn.cursor()

    print("=== 实时监控自动开平仓执行 ===\n")
    print("策略run: 35298c65-cdbe-4bee-bee3-b7ded07c3204 (auto_paper_mature_templates)")
    print("监控间隔: 30秒\n")

    last_order_time = None

    while True:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"\n[{now}] 检查中...")

        # 1. 最新订单（最近5分钟）
        cursor.execute("""
            SELECT created_at, symbol, direction, execution_status, rejection_reason,
                   evaluated_risk_state, gateway_order_id
            FROM order_executions
            WHERE paper_run_id = '35298c65-cdbe-4bee-bee3-b7ded07c3204'
              AND created_at > datetime('now', '-5 minutes')
            ORDER BY created_at DESC
            LIMIT 5
        """)

        recent_orders = cursor.fetchall()
        if recent_orders:
            newest_time = recent_orders[0][0]
            if newest_time != last_order_time:
                print("\n🔔 发现新订单（最近5分钟）:")
                for row in recent_orders:
                    status_icon = "✅" if row[3] == "accepted" else "❌"
                    print(f"  {status_icon} {row[0]} | {row[1]} {row[2]} | {row[3]}")
                    if row[4]:
                        print(f"     拒绝原因: {row[4][:80]}")
                    if row[5]:
                        try:
                            risk = json.loads(row[5])
                            dd_pct = risk.get("drawdown_pct", 0) * 100
                            print(
                                f"     drawdown={dd_pct:.2f}% peak={risk.get('equity_peak'):.2f} equity={risk.get('account_equity'):.2f}"
                            )
                        except:
                            pass
                    if row[6]:
                        print(f"     gateway_order_id={row[6]}")
                last_order_time = newest_time
        else:
            print("  无新订单")

        # 2. 当前策略持仓
        cursor.execute("""
            SELECT symbol, position_side, quantity, management_status
            FROM position_records
            WHERE run_id = '35298c65-cdbe-4bee-bee3-b7ded07c3204'
              AND management_status = 'MANAGED_STRATEGY'
            ORDER BY opened_at DESC
        """)
        positions = cursor.fetchall()
        if positions:
            print(f"\n📊 当前策略持仓({len(positions)}):")
            for row in positions:
                print(f"  {row[0]} {row[1]} qty={row[2]:.4f}")
        else:
            print("\n📊 当前无策略持仓")

        # 3. paper_status检查（防止被hard_drawdown锁定）
        cursor.execute("SELECT paper_status FROM paper_runs WHERE paper_run_id='35298c65-cdbe-4bee-bee3-b7ded07c3204'")
        status_row = cursor.fetchone()
        if status_row:
            if status_row[0] == "locked":
                print(f"\n⚠️  策略状态: {status_row[0]} (已被hard_drawdown锁定！)")
            elif status_row[0] != "running":
                print(f"\n⚠️  策略状态: {status_row[0]}")

        time.sleep(30)


if __name__ == "__main__":
    try:
        monitor()
    except KeyboardInterrupt:
        print("\n\n监控已停止")
