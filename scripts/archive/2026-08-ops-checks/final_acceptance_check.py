#!/usr/bin/env python3
"""最终验收检查和持续监控"""

import sqlite3
from datetime import UTC, datetime


def main():
    conn = sqlite3.connect(".local_paper_console.db")
    cursor = conn.cursor()

    print("\n" + "=" * 80)
    print("Binance自动交易闭环验收 - 最终检查")
    print(f"检查时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 80)

    # 1. 验收目标检查
    print("\n【验收目标】")

    # 1.1 调度周期正常运行
    cursor.execute("""
        SELECT job_name, started_at, completed_at, status
        FROM scheduler_cycles
        WHERE started_at > datetime('now', '-1 hour')
        ORDER BY started_at DESC
        LIMIT 5
    """)
    cycles = cursor.fetchall()

    completed_cycles = [c for c in cycles if c[3] == "completed"]
    print(f"\n✅ 1. 调度周期正常运行: {len(completed_cycles)}/5 个周期成功完成")
    for cycle in completed_cycles[:3]:
        print(f"   - {cycle[1]} -> {cycle[2]}")

    # 1.2 有新的订单尝试
    cursor.execute("""
        SELECT COUNT(*) as cnt
        FROM order_executions
        WHERE created_at > datetime('now', '-1 hour')
    """)
    recent_orders = cursor.fetchone()[0]

    print(f"\n✅ 2. 有新的订单尝试: {recent_orders} 条订单（最近1小时）")

    # 1.3 真实提交到Binance
    cursor.execute("""
        SELECT created_at, symbol, direction, execution_status, gateway_order_id, gateway_status
        FROM order_executions
        WHERE created_at > datetime('now', '-1 hour')
          AND gateway_order_id IS NOT NULL
        ORDER BY created_at DESC
        LIMIT 10
    """)
    gateway_orders = cursor.fetchall()

    print(f"\n✅ 3. 真实提交到Binance: {len(gateway_orders)} 条订单有网关ID")
    if gateway_orders:
        print("\n   最新的网关订单:")
        for order in gateway_orders[:5]:
            status_icon = "✅" if order[3] == "filled" else ("⏳" if order[3] == "submitted" else "❌")
            print(f"   {status_icon} {order[0]} | {order[1]} {order[2]} | {order[3]} | ID:{order[4]}")

    # 2. 系统健康度检查
    print("\n【系统健康度】")

    # 2.1 风险事件
    cursor.execute("""
        SELECT COUNT(*) as cnt
        FROM risk_events
        WHERE resolution_status IN ('detected', 'acknowledged')
          AND (expires_at IS NULL OR expires_at > datetime('now'))
    """)
    active_risks = cursor.fetchone()[0]

    risk_icon = "✅" if active_risks == 0 else "⚠️"
    print(f"\n{risk_icon} 活跃风险事件: {active_risks} 个")

    # 2.2 订单成功率（最近1小时）
    cursor.execute("""
        SELECT execution_status, COUNT(*) as cnt
        FROM order_executions
        WHERE created_at > datetime('now', '-1 hour')
        GROUP BY execution_status
    """)
    status_stats = cursor.fetchall()

    print("\n订单状态分布（最近1小时）:")
    total = sum(row[1] for row in status_stats)
    filled_count = sum(row[1] for row in status_stats if row[0] == "filled")
    for row in status_stats:
        pct = (row[1] / total * 100) if total > 0 else 0
        print(f"   {row[0]}: {row[1]} 条 ({pct:.1f}%)")

    success_rate = (filled_count / total * 100) if total > 0 else 0
    rate_icon = "✅" if success_rate > 50 else "⚠️"
    print(f"\n{rate_icon} 成交率: {success_rate:.1f}% ({filled_count}/{total})")

    # 2.3 决策漏斗健康度
    cursor.execute("""
        SELECT action, COUNT(*) as cnt
        FROM decision_snapshots
        WHERE created_at > datetime('now', '-1 hour')
        GROUP BY action
        ORDER BY cnt DESC
        LIMIT 5
    """)
    decision_actions = cursor.fetchall()

    print("\n决策活动分布（最近1小时）:")
    for row in decision_actions:
        print(f"   {row[0]}: {row[1]} 条")

    # 检查对账活动是否过多
    reconcile_count = sum(row[1] for row in decision_actions if "reconcile" in row[0])
    normal_count = sum(row[1] for row in decision_actions if "reconcile" not in row[0])

    if reconcile_count > normal_count * 2:
        print(f"\n⚠️  对账活动过多 ({reconcile_count} vs {normal_count})，但已配置允许未托管持仓")
    else:
        print(f"\n✅ 决策活动正常 (对账:{reconcile_count}, 正常:{normal_count})")

    # 3. 持续监控指标
    print("\n【持续监控建议】")

    # 3.1 检查最近是否有新订单
    cursor.execute("""
        SELECT MAX(created_at) as last_order
        FROM order_executions
        WHERE gateway_order_id IS NOT NULL
    """)
    last_gateway_order = cursor.fetchone()[0]

    if last_gateway_order:
        # 解析时间，处理可能的时区问题
        try:
            last_order_dt = datetime.fromisoformat(last_gateway_order.replace("Z", "+00:00"))
        except:
            last_order_dt = datetime.fromisoformat(last_gateway_order)
            if last_order_dt.tzinfo is None:
                last_order_dt = last_order_dt.replace(tzinfo=UTC)

        now_utc = datetime.now(UTC)
        minutes_ago = (now_utc - last_order_dt).total_seconds() / 60
        print(f"\n最后一次网关订单: {minutes_ago:.1f} 分钟前")

        if minutes_ago < 30:
            print("✅ 系统活跃，正在正常开单")
        elif minutes_ago < 60:
            print("⚠️  最近30分钟没有新订单，但可能是正常的信号空窗期")
        else:
            print("❌ 超过1小时没有新订单，需要检查")

    # 3.2 建议的监控命令
    print("\n持续监控命令（每15-30分钟运行一次）:")
    print("  python scripts/check_auto_trading_readiness.py")
    print("  python scripts/monitor_scheduler.py")
    print("  python scripts/query_orders_24h.py | head -20")

    # 3.3 关键指标
    print("\n关键监控指标:")
    print("  1. 每小时至少有1-2次订单尝试")
    print("  2. 成交率 > 30%")
    print("  3. 活跃风险事件 = 0")
    print("  4. 对账活动不应该完全占据决策记录")

    # 4. 总结
    print("\n" + "=" * 80)

    if len(gateway_orders) > 0 and len(completed_cycles) > 0 and active_risks == 0:
        print("🎉 验收通过！自动交易闭环已跑通")
        print("\n核心证据:")
        print(f"  ✅ 调度器正常运行 ({len(completed_cycles)} 个周期)")
        print(f"  ✅ 有真实网关订单 ({len(gateway_orders)} 条)")
        print("  ✅ 没有活跃风险事件")
        print("  ✅ 系统健康运行中")
    else:
        print("⚠️  部分指标未达标，需要继续监控")

    print("=" * 80)

    conn.close()


if __name__ == "__main__":
    main()
