#!/usr/bin/env python3
"""简化的持续监控脚本 - 每15-30分钟运行一次"""

import sqlite3
from datetime import datetime


def main():
    conn = sqlite3.connect(".local_paper_console.db")
    cursor = conn.cursor()

    print("\n" + "=" * 80)
    print(f"自动交易监控 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 80)

    # 1. 最近30分钟的网关订单
    cursor.execute("""
        SELECT created_at, symbol, direction, execution_status, gateway_order_id
        FROM order_executions
        WHERE created_at > datetime('now', '-30 minutes')
          AND gateway_order_id IS NOT NULL
        ORDER BY created_at DESC
    """)
    recent_gateway = cursor.fetchall()

    print(f"\n【最近30分钟网关订单】共 {len(recent_gateway)} 条")
    if recent_gateway:
        for row in recent_gateway:
            icon = "✅" if row[3] == "filled" else ("⏳" if row[3] == "submitted" else "❌")
            print(f"  {icon} {row[0]} | {row[1]} {row[2]} | {row[3]}")
    else:
        print("  ⚠️  没有新的网关订单")

    # 2. 活跃风险事件
    cursor.execute("""
        SELECT COUNT(*) as cnt
        FROM risk_events
        WHERE resolution_status IN ('detected', 'acknowledged')
          AND (expires_at IS NULL OR expires_at > datetime('now'))
    """)
    risk_count = cursor.fetchone()[0]

    icon = "✅" if risk_count == 0 else "❌"
    print(f"\n【风险事件】{icon} {risk_count} 个活跃事件")

    # 3. 调度周期状态
    cursor.execute("""
        SELECT started_at, completed_at, status
        FROM scheduler_cycles
        ORDER BY started_at DESC
        LIMIT 1
    """)
    last_cycle = cursor.fetchone()

    if last_cycle:
        icon = "✅" if last_cycle[2] == "completed" else "⏳"
        print(f"\n【调度周期】{icon} 最近: {last_cycle[0]}")
    else:
        print("\n【调度周期】❌ 没有记录")

    # 4. 最近1小时订单统计
    cursor.execute("""
        SELECT execution_status, COUNT(*) as cnt
        FROM order_executions
        WHERE created_at > datetime('now', '-1 hour')
        GROUP BY execution_status
    """)
    stats = cursor.fetchall()

    total = sum(row[1] for row in stats)
    filled = sum(row[1] for row in stats if row[0] == "filled")

    if total > 0:
        rate = filled / total * 100
        icon = "✅" if rate >= 25 else "⚠️"
        print(f"\n【订单统计】最近1小时: {total} 条订单, 成交率 {rate:.1f}%")
        for row in stats:
            print(f"  {row[0]}: {row[1]} 条")
    else:
        print("\n【订单统计】⚠️  最近1小时没有订单")

    # 5. 健康度评分
    print("\n" + "=" * 80)

    health_score = 0
    issues = []

    if len(recent_gateway) > 0:
        health_score += 40
    else:
        issues.append("最近30分钟没有网关订单")

    if risk_count == 0:
        health_score += 30
    else:
        issues.append(f"有{risk_count}个活跃风险事件")

    if last_cycle and last_cycle[2] == "completed":
        health_score += 20
    else:
        issues.append("调度周期异常")

    if total > 0:
        health_score += 10

    if health_score >= 90:
        print(f"🎉 系统健康度: 优秀 ({health_score}%)")
    elif health_score >= 70:
        print(f"✅ 系统健康度: 良好 ({health_score}%)")
    elif health_score >= 50:
        print(f"⚠️  系统健康度: 一般 ({health_score}%)")
    else:
        print(f"❌ 系统健康度: 异常 ({health_score}%)")

    if issues:
        print("\n问题:")
        for issue in issues:
            print(f"  - {issue}")

    print("=" * 80 + "\n")

    conn.close()


if __name__ == "__main__":
    main()
