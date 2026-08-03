#!/usr/bin/env python3
"""重新检查持仓归属，区分策略持仓和手动持仓"""

import sqlite3


def main():
    conn = sqlite3.connect(".local_paper_console.db")
    cursor = conn.cursor()

    run_id = "35298c65-cdbe-4bee-bee3-b7ded07c3204"

    print("\n" + "=" * 80)
    print(f"持仓归属检查 - Run: {run_id[:12]}...")
    print("=" * 80)

    # 查看该run的所有持仓记录
    cursor.execute(
        """
        SELECT position_record_id, symbol, position_side, quantity,
               management_status, order_origin, opened_at, strategy_id
        FROM position_records
        WHERE run_id = ?
        ORDER BY opened_at DESC
    """,
        (run_id,),
    )

    rows = cursor.fetchall()

    print(f"\n=== 该run的所有持仓记录（共{len(rows)}条） ===\n")

    managed_count = 0
    unmanaged_count = 0

    for row in rows:
        status = row[4]
        icon = "✅" if status == "MANAGED_STRATEGY" else "⚠️"

        print(f"{icon} {row[1]} | {row[2]} | 数量:{row[3]:.4f}")
        print(f"   状态: {status}")
        print(f"   来源: {row[5]}")
        print(f"   策略ID: {row[7]}")
        print(f"   开仓时间: {row[6]}\n")

        if status == "MANAGED_STRATEGY":
            managed_count += 1
        elif status == "UNMANAGED_EXTERNAL_POSITION":
            unmanaged_count += 1

    print("=" * 80)
    print(f"统计: 策略管理={managed_count}, 未托管外部={unmanaged_count}")
    print("=" * 80)

    if unmanaged_count == 0:
        print("\n✅ 所有持仓都是策略管理的，没有真正的手动持仓")
        print("❌ 我的分析出错了！drawdown问题不是手动持仓导致的")
    else:
        print(f"\n⚠️  有{unmanaged_count}个未托管外部持仓")

    # 重新检查drawdown的真实原因
    print("\n" + "=" * 80)
    print("重新分析drawdown原因")
    print("=" * 80)

    cursor.execute(
        """
        SELECT paper_metrics_summary, execution_profile
        FROM paper_runs
        WHERE paper_run_id = ?
    """,
        (run_id,),
    )

    row = cursor.fetchone()
    if row:
        import json

        try:
            metrics = json.loads(row[0]) if row[0] else {}
            profile = json.loads(row[1]) if row[1] else {}

            print(f"\n账户权益: {metrics.get('account_equity')}")
            print(f"权益峰值: {metrics.get('equity_peak')}")
            print(f"已实现盈亏: {metrics.get('total_realized_pnl')}")
            print(f"日内已实现: {metrics.get('daily_realized_pnl')}")
            print(f"周已实现: {metrics.get('weekly_realized_pnl')}")
            print(f"连续亏损: {metrics.get('consecutive_losses')}")

            equity = float(metrics.get("account_equity", 10000))
            peak = float(metrics.get("equity_peak", equity))
            drawdown = (peak - equity) / peak if peak > 0 else 0

            print(f"\nDrawdown计算: ({peak} - {equity}) / {peak} = {drawdown:.4f} ({drawdown * 100:.2f}%)")

            if unmanaged_count == 0:
                print("\n真实原因分析:")
                print("  策略确实产生了浮亏或已实现亏损")
                print("  账户从峰值5714降到4345，亏损约1369")
                print("  这是策略自身的回撤，不是手动持仓的问题")

        except Exception as e:
            print(f"解析失败: {e}")

    conn.close()


if __name__ == "__main__":
    main()
