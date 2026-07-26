#!/usr/bin/env python3
"""全面检查自动开单配置和潜在问题"""

import json
import sqlite3


def main():
    conn = sqlite3.connect(".local_paper_console.db")
    cursor = conn.cursor()

    print("\n" + "=" * 80)
    print("自动开单配置与问题检查")
    print("=" * 80)

    # 1. 检查自动PaperRun配置
    print("\n=== 1. 自动PaperRun配置 ===")
    cursor.execute("""
        SELECT paper_run_id, strategy_id, paper_status, execution_profile
        FROM paper_runs
        WHERE paper_status = 'running'
        ORDER BY created_at DESC
    """)

    runs = cursor.fetchall()
    auto_runs = []

    for row in runs:
        try:
            profile = json.loads(row[3]) if row[3] else {}
            runtime_key = profile.get("auto_paper_runtime_key")
            if runtime_key:
                auto_runs.append((row[0], row[1], runtime_key, profile))
                print(f"\nRun ID: {row[0][:12]}...")
                print(f"  策略: {row[1]}")
                print(f"  Runtime Key: {runtime_key}")
                print(f"  mirror_to_gateway: {profile.get('mirror_to_gateway', False)}")
                print(f"  execution_mode: {profile.get('execution_mode', 'N/A')}")
                print(
                    f"  allow_entry_with_unmanaged_positions: {profile.get('allow_entry_with_unmanaged_positions', False)}"
                )
                print(f"  testnet_acceptance_verified_at: {profile.get('testnet_acceptance_verified_at', 'N/A')}")
                print(f"  symbol_scope: {profile.get('symbol_scope', row[2])}")
        except Exception as e:
            print(f"  解析失败: {e}")

    if not auto_runs:
        print("❌ 没有找到自动运行的PaperRun")
        return

    # 2. 检查风险事件（应该为0）
    print("\n=== 2. 活跃风险事件 ===")
    cursor.execute("""
        SELECT COUNT(*) as cnt
        FROM risk_events
        WHERE resolution_status IN ('detected', 'acknowledged')
          AND (expires_at IS NULL OR expires_at > datetime('now'))
    """)
    active_risk_events = cursor.fetchone()[0]

    if active_risk_events > 0:
        print(f"⚠️  有 {active_risk_events} 个活跃风险事件")
        cursor.execute("""
            SELECT event_type, level, created_at, expires_at, description
            FROM risk_events
            WHERE resolution_status IN ('detected', 'acknowledged')
              AND (expires_at IS NULL OR expires_at > datetime('now'))
            ORDER BY created_at DESC
            LIMIT 5
        """)
        for row in cursor.fetchall():
            print(f"  {row[0]} | {row[1]} | {row[2]}")
            print(f"    {row[4][:80]}")
    else:
        print("✅ 没有活跃的风险事件")

    # 3. 检查最近的决策情况
    print("\n=== 3. 最近30分钟的决策情况 ===")
    cursor.execute("""
        SELECT action, COUNT(*) as cnt
        FROM decision_snapshots
        WHERE created_at > datetime('now', '-30 minutes')
        GROUP BY action
        ORDER BY cnt DESC
    """)
    decision_actions = cursor.fetchall()

    if decision_actions:
        for row in decision_actions:
            print(f"  {row[0]}: {row[1]} 条")
    else:
        print("  没有最近的决策记录")

    # 4. 检查订单情况
    print("\n=== 4. 最近30分钟的订单情况 ===")
    cursor.execute("""
        SELECT execution_status, COUNT(*) as cnt
        FROM order_executions
        WHERE created_at > datetime('now', '-30 minutes')
        GROUP BY execution_status
    """)
    order_stats = cursor.fetchall()

    if order_stats:
        for row in order_stats:
            print(f"  {row[0]}: {row[1]} 条")

        # 检查是否有真实网关订单
        cursor.execute("""
            SELECT COUNT(*) as cnt
            FROM order_executions
            WHERE created_at > datetime('now', '-30 minutes')
              AND gateway_order_id IS NOT NULL
        """)
        gateway_orders = cursor.fetchone()[0]

        if gateway_orders > 0:
            print(f"\n  ✅ 有 {gateway_orders} 条真实提交到Binance的订单")
        else:
            print("\n  ❌ 没有真实提交到Binance的订单")
    else:
        print("  没有最近的订单记录")

    # 5. 检查持仓对账情况
    print("\n=== 5. 持仓对账情况 ===")
    cursor.execute("""
        SELECT symbol, COUNT(*) as cnt
        FROM decision_snapshots
        WHERE created_at > datetime('now', '-30 minutes')
          AND action LIKE '%reconcile%'
        GROUP BY symbol
        ORDER BY cnt DESC
    """)
    reconcile_stats = cursor.fetchall()

    if reconcile_stats:
        print("  对账活动:")
        for row in reconcile_stats:
            print(f"    {row[0]}: {row[1]} 次")
    else:
        print("  ✅ 没有对账活动")

    # 6. 检查调度器状态
    print("\n=== 6. 最近的调度周期 ===")
    cursor.execute("""
        SELECT job_name, started_at, completed_at, status
        FROM scheduler_cycles
        ORDER BY started_at DESC
        LIMIT 3
    """)
    cycles = cursor.fetchall()

    if cycles:
        for row in cycles:
            status_icon = "✅" if row[3] == "completed" else "❌"
            print(f"  {status_icon} {row[0]} | {row[1]} -> {row[2]} | {row[3]}")
    else:
        print("  没有调度周期记录")

    # 7. 总结和建议
    print("\n" + "=" * 80)
    print("诊断总结")
    print("=" * 80)

    issues = []

    # 检查是否启用了allow_entry_with_unmanaged_positions
    for run_id, strategy_id, runtime_key, profile in auto_runs:
        if runtime_key == "auto_paper_mature_templates":
            if not profile.get("allow_entry_with_unmanaged_positions"):
                issues.append("❌ 主要自动run未启用 allow_entry_with_unmanaged_positions")
            if not profile.get("mirror_to_gateway"):
                issues.append("❌ 主要自动run未启用 mirror_to_gateway")

    if active_risk_events > 0:
        issues.append(f"⚠️  有 {active_risk_events} 个活跃风险事件")

    if issues:
        print("\n发现的问题:")
        for issue in issues:
            print(f"  {issue}")

        print("\n建议操作:")
        if any("allow_entry_with_unmanaged_positions" in i for i in issues):
            print("  1. 运行: python scripts/enable_entry_with_external_positions.py")
        if active_risk_events > 0:
            print("  2. 运行: python scripts/fix_risk_events.py")
        print("  3. 重启系统")
    else:
        print("\n✅ 配置检查通过，准备重启系统")

    conn.close()


if __name__ == "__main__":
    main()
