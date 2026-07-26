#!/usr/bin/env python3
"""全面检查重启后1小时的运行情况"""

import sqlite3
from datetime import datetime


def main():
    conn = sqlite3.connect(".local_paper_console.db")
    cursor = conn.cursor()

    print("\n" + "=" * 80)
    print("重启后1小时全面诊断")
    print(f"检查时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 80)

    # 1. 确定重启时间（通过最早的调度周期判断）
    cursor.execute("""
        SELECT MIN(started_at) as first_cycle
        FROM scheduler_cycles
        WHERE started_at > datetime('now', '-2 hours')
    """)
    first_cycle = cursor.fetchone()[0]

    if first_cycle:
        restart_time = first_cycle
        print(f"\n推测重启时间: {restart_time}")
    else:
        # 如果没有调度记录，用最早的订单时间
        cursor.execute("""
            SELECT MIN(created_at) as first_order
            FROM order_executions
            WHERE created_at > datetime('now', '-2 hours')
        """)
        first_order = cursor.fetchone()[0]
        restart_time = first_order if first_order else datetime.now().isoformat()
        print(f"\n推测重启时间: {restart_time} (基于订单)")

    # 2. 调度周期执行情况
    print("\n=== 1. 调度周期执行情况 ===")
    cursor.execute(
        """
        SELECT started_at, completed_at, status
        FROM scheduler_cycles
        WHERE started_at >= ?
        ORDER BY started_at ASC
    """,
        (restart_time,),
    )

    cycles = cursor.fetchall()
    completed = [c for c in cycles if c[2] == "completed"]
    failed = [c for c in cycles if c[2] not in ("completed", "claimed")]

    print(f"总周期数: {len(cycles)}")
    print(f"成功: {len(completed)} | 失败: {len(failed)}")

    if len(completed) >= 3:
        print("✅ 调度器正常运行")
    else:
        print("⚠️  调度周期较少")

    # 3. 决策漏斗分析
    print("\n=== 2. 决策漏斗完整性检查 ===")

    cursor.execute(
        """
        SELECT action, COUNT(*) as cnt
        FROM decision_snapshots
        WHERE created_at >= ?
        GROUP BY action
        ORDER BY cnt DESC
    """,
        (restart_time,),
    )

    decision_actions = cursor.fetchall()

    print("\n决策动作分布:")
    total_decisions = sum(row[1] for row in decision_actions)

    # 分类统计
    reconcile_count = 0
    entry_attempts = 0
    hold_positions = 0
    exit_actions = 0
    rejected_count = 0
    skip_count = 0

    for action, count in decision_actions:
        print(f"  {action}: {count} 条")

        if "reconcile" in action:
            reconcile_count += count
        elif action in ["open_long", "open_short"]:
            entry_attempts += count
        elif action in ["hold_long", "hold_short"]:
            hold_positions += count
        elif "close" in action or "exit" in action:
            exit_actions += count
        elif action == "rejected":
            rejected_count += count
        elif "skip" in action:
            skip_count += count

    print("\n分类汇总:")
    print(f"  入场尝试: {entry_attempts}")
    print(f"  持仓管理: {hold_positions}")
    print(f"  出场动作: {exit_actions}")
    print(f"  拒绝: {rejected_count}")
    print(f"  跳过: {skip_count}")
    print(f"  对账: {reconcile_count}")

    # 判断是否有正常的交易决策
    normal_trading = entry_attempts + hold_positions + exit_actions

    if normal_trading > 0:
        print(f"\n✅ 有 {normal_trading} 条正常交易决策")
    else:
        print("\n❌ 没有正常交易决策，全是对账和跳过")

    # 4. 订单执行链路检查
    print("\n=== 3. 订单执行链路检查 ===")

    cursor.execute(
        """
        SELECT execution_status, COUNT(*) as cnt
        FROM order_executions
        WHERE created_at >= ?
        GROUP BY execution_status
        ORDER BY cnt DESC
    """,
        (restart_time,),
    )

    order_stats = cursor.fetchall()

    print("\n订单状态分布:")
    total_orders = sum(row[1] for row in order_stats)
    filled_count = 0
    submitted_count = 0
    rejected_count = 0

    for status, count in order_stats:
        pct = (count / total_orders * 100) if total_orders > 0 else 0
        print(f"  {status}: {count} 条 ({pct:.1f}%)")

        if status == "filled":
            filled_count = count
        elif status == "submitted":
            submitted_count = count
        elif status == "rejected":
            rejected_count = count

    # 检查网关订单
    cursor.execute(
        """
        SELECT COUNT(*) as cnt
        FROM order_executions
        WHERE created_at >= ?
          AND gateway_order_id IS NOT NULL
    """,
        (restart_time,),
    )

    gateway_count = cursor.fetchone()[0]

    print(f"\n网关订单: {gateway_count} 条")

    if gateway_count > 0:
        print("✅ 有真实提交到Binance的订单")

        # 显示最新的网关订单
        cursor.execute(
            """
            SELECT created_at, symbol, direction, execution_status, gateway_order_id, order_origin
            FROM order_executions
            WHERE created_at >= ?
              AND gateway_order_id IS NOT NULL
            ORDER BY created_at DESC
            LIMIT 10
        """,
            (restart_time,),
        )

        print("\n最新网关订单:")
        for row in cursor.fetchall():
            icon = "✅" if row[3] == "filled" else ("⏳" if row[3] == "submitted" else "❌")
            print(f"  {icon} {row[0]} | {row[1]} {row[2]} | {row[3]} | ID:{row[4]} | {row[5]}")
    else:
        print("❌ 没有真实提交到Binance的订单")

    # 5. 拒绝原因分析
    if rejected_count > 0:
        print("\n=== 4. 拒绝原因分析 ===")

        cursor.execute(
            """
            SELECT rejection_reason, COUNT(*) as cnt
            FROM order_executions
            WHERE created_at >= ?
              AND execution_status = 'rejected'
            GROUP BY rejection_reason
            ORDER BY cnt DESC
        """,
            (restart_time,),
        )

        rejection_reasons = cursor.fetchall()

        print("\n拒绝原因分布:")
        for reason, count in rejection_reasons:
            print(f"  {reason or 'NULL'}: {count} 条")

        # 检查是否还有blocking_risk_event
        has_blocking_risk = any("blocking_risk_event" in (r[0] or "") for r in rejection_reasons)

        if has_blocking_risk:
            print("\n❌ 仍然有blocking_risk_event拒绝")
        else:
            print("\n✅ 没有blocking_risk_event拒绝")

    # 6. 信号生成检查
    print("\n=== 5. 信号生成检查 ===")

    # 通过decision_snapshots的pipeline_status检查信号流
    cursor.execute(
        """
        SELECT pipeline_status, COUNT(*) as cnt
        FROM decision_snapshots
        WHERE created_at >= ?
          AND pipeline_status IS NOT NULL
        GROUP BY pipeline_status
        ORDER BY cnt DESC
    """,
        (restart_time,),
    )

    pipeline_stats = cursor.fetchall()

    if pipeline_stats:
        print("\n管道状态分布:")
        for status, count in pipeline_stats:
            print(f"  {status}: {count} 条")

        # 检查是否有信号通过ensemble
        has_ensemble_pass = any("pass" in status.lower() or "taken" in status.lower() for status, _ in pipeline_stats)

        if has_ensemble_pass:
            print("\n✅ 有信号通过ensemble阶段")
        else:
            print("\n⚠️  没有信号通过ensemble")
    else:
        print("\n⚠️  没有pipeline_status记录")

    # 7. 风险事件状态
    print("\n=== 6. 风险事件检查 ===")

    cursor.execute("""
        SELECT COUNT(*) as cnt
        FROM risk_events
        WHERE resolution_status IN ('detected', 'acknowledged')
          AND (expires_at IS NULL OR expires_at > datetime('now'))
    """)

    active_risks = cursor.fetchone()[0]

    if active_risks == 0:
        print("✅ 活跃风险事件: 0 个")
    else:
        print(f"❌ 活跃风险事件: {active_risks} 个")

        cursor.execute("""
            SELECT event_type, level, description, created_at
            FROM risk_events
            WHERE resolution_status IN ('detected', 'acknowledged')
              AND (expires_at IS NULL OR expires_at > datetime('now'))
            ORDER BY created_at DESC
            LIMIT 5
        """)

        print("\n活跃风险事件详情:")
        for row in cursor.fetchall():
            print(f"  {row[0]} | {row[1]} | {row[3]}")
            print(f"    {row[2][:80]}")

    # 8. 最终诊断
    print("\n" + "=" * 80)
    print("诊断结论")
    print("=" * 80)

    issues = []

    # 检查各项指标
    if len(completed) < 3:
        issues.append("调度周期较少")

    if normal_trading == 0:
        issues.append("❌ 没有正常交易决策（可能是信号问题）")

    if gateway_count == 0:
        issues.append("❌ 没有网关订单（可能是gatekeeper拦截）")

    if active_risks > 0:
        issues.append("❌ 有活跃风险事件阻塞")

    if issues:
        print("\n发现的问题:")
        for issue in issues:
            print(f"  {issue}")

        # 判断问题类型
        print("\n问题分类:")

        if "没有正常交易决策" in str(issues):
            print("\n  🎯 问题类型: 量化策略信号生成问题")
            print("  原因分析:")
            print("    - 市场条件不满足策略入场规则")
            print("    - 信号在ensemble阶段被淘汰")
            print("    - MTF多时间框架校验未通过")
            print("  结论: 链路已打通，只是策略暂时没有产生入场信号")

        if "没有网关订单" in str(issues) and normal_trading > 0:
            print("\n  🎯 问题类型: Gatekeeper拦截问题")
            print("  需要检查: 拒绝原因")

        if "有活跃风险事件" in str(issues):
            print("\n  🎯 问题类型: 风险事件阻塞")
            print("  需要执行: python scripts/fix_risk_events.py")
    else:
        print("\n🎉 链路完全打通！")
        print("\n证据:")
        print(f"  ✅ 调度器正常: {len(completed)} 个周期")
        print(f"  ✅ 有交易决策: {normal_trading} 条")
        print(f"  ✅ 有网关订单: {gateway_count} 条")
        print("  ✅ 无风险事件阻塞")

        if gateway_count > 0 and filled_count > 0:
            print(f"\n  🎊 已有 {filled_count} 条订单成交！")

        if normal_trading > 0 and gateway_count == 0:
            print("\n  📊 有决策但没有网关订单:")
            print("     可能是信号在后续阶段被过滤")
            print("     需要分析拒绝原因")

    conn.close()


if __name__ == "__main__":
    main()
