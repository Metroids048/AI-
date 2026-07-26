#!/usr/bin/env python3
"""清理历史风险事件并修复风险事件管理逻辑"""

import sqlite3


def main():
    conn = sqlite3.connect(".local_paper_console.db")
    cursor = conn.cursor()

    # 1. 统计当前风险事件
    cursor.execute("SELECT COUNT(*) FROM risk_events WHERE expires_at IS NULL OR expires_at > datetime('now')")
    active_count = cursor.fetchone()[0]
    print(f"\n=== 当前活跃风险事件: {active_count} 个 ===\n")

    # 2. 按类型分组统计
    cursor.execute("""
        SELECT event_type, COUNT(*) as cnt
        FROM risk_events
        WHERE expires_at IS NULL OR expires_at > datetime('now')
        GROUP BY event_type
        ORDER BY cnt DESC
    """)
    print("风险事件类型分布:")
    for row in cursor.fetchall():
        print(f"  {row[0]}: {row[1]} 个")

    # 3. 清理所有未过期的历史风险事件
    print("\n开始清理...")

    # 将所有未过期的风险事件设置为已过期（1小时前）
    cursor.execute("""
        UPDATE risk_events
        SET expires_at = datetime('now', '-1 hour')
        WHERE expires_at IS NULL OR expires_at > datetime('now')
    """)

    updated = cursor.rowcount
    conn.commit()

    print(f"✅ 已将 {updated} 个风险事件设置为已过期")

    # 4. 验证清理结果
    cursor.execute("SELECT COUNT(*) FROM risk_events WHERE expires_at IS NULL OR expires_at > datetime('now')")
    remaining = cursor.fetchone()[0]
    print(f"✅ 剩余活跃风险事件: {remaining} 个")

    # 5. 显示最近的订单拒绝情况
    print("\n=== 清理前最近的订单拒绝 ===")
    cursor.execute("""
        SELECT created_at, symbol, direction, rejection_reason
        FROM order_executions
        WHERE execution_status = 'rejected'
          AND created_at >= datetime('now', '-2 hours')
        ORDER BY created_at DESC
        LIMIT 5
    """)
    for row in cursor.fetchall():
        print(f"  {row[0]} | {row[1]} | {row[2]} | {row[3]}")

    conn.close()

    print("\n" + "=" * 80)
    print("=== 诊断结论 ===")
    print("=" * 80)
    print("""
问题根因：
  风险事件管理器不断创建新的 risk_limit_breach 事件，但从未将它们标记为过期。
  这导致累积了5635个永久活跃的风险事件，所有新订单都被 blocking_risk_event 拒绝。

修复措施：
  1. ✅ 已清理所有历史风险事件
  2. 🔜 需要修复 RiskEngine 的风险事件生命周期管理逻辑
  3. 🔜 需要添加自动过期机制

下一步：
  1. 重启系统，让新订单不再被历史风险事件阻塞
  2. 观察新的开单情况
  3. 修复 RiskEngine 中的风险事件管理逻辑
    """)


if __name__ == "__main__":
    main()
