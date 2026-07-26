#!/usr/bin/env python3
"""检查最近10分钟的风险事件创建情况，验证修复是否生效"""

import sqlite3


def main():
    conn = sqlite3.connect(".local_paper_console.db")
    cursor = conn.cursor()

    print("\n" + "=" * 80)
    print("验证修复是否生效 - 检查最近10分钟")
    print("=" * 80)

    # 检查最近10分钟创建的风险事件
    cursor.execute("""
        SELECT created_at, event_type, description, expires_at
        FROM risk_events
        WHERE created_at > datetime('now', '-10 minutes')
        ORDER BY created_at DESC
    """)

    recent_events = cursor.fetchall()

    print(f"\n最近10分钟创建的风险事件: {len(recent_events)} 个")

    if recent_events:
        print("\n⚠️  修复未生效！仍在创建新事件:\n")
        for row in recent_events[:10]:
            has_expiry = "✅" if row[3] else "❌"
            print(f"{has_expiry} {row[0]} | {row[1]}")
            print(f"   描述: {row[2][:70]}")
            print(f"   过期: {row[3] or 'None (永久活跃)'}\n")
    else:
        print("\n✅ 修复生效！最近10分钟没有创建新的风险事件")

    # 检查最近30分钟的订单拒绝情况
    print("\n" + "=" * 80)
    print("最近30分钟的订单拒绝情况")
    print("=" * 80)

    cursor.execute("""
        SELECT created_at, symbol, direction, rejection_reason
        FROM order_executions
        WHERE execution_status = 'rejected'
          AND created_at > datetime('now', '-30 minutes')
        ORDER BY created_at DESC
        LIMIT 10
    """)

    rejections = cursor.fetchall()

    if rejections:
        print(f"\n最近30分钟被拒绝的订单: {len(rejections)} 条\n")
        has_blocking = False
        for row in rejections:
            reason = row[3] or "unknown"
            icon = "🔴" if "blocking_risk_event" in reason else "⚠️"
            print(f"{icon} {row[0]} | {row[1]} {row[2]}")
            print(f"   原因: {reason}\n")
            if "blocking_risk_event" in reason:
                has_blocking = True

        if has_blocking:
            print("🔴 仍有订单被blocking_risk_event拒绝（可能是清理前的）")
        else:
            print("✅ 没有订单被blocking_risk_event拒绝")
    else:
        print("\n✅ 最近30分钟没有被拒绝的订单")

    # 检查最近30分钟的成功订单
    print("\n" + "=" * 80)
    print("最近30分钟的成功订单")
    print("=" * 80)

    cursor.execute("""
        SELECT created_at, symbol, direction, execution_status, gateway_order_id
        FROM order_executions
        WHERE gateway_order_id IS NOT NULL
          AND created_at > datetime('now', '-30 minutes')
        ORDER BY created_at DESC
        LIMIT 10
    """)

    success_orders = cursor.fetchall()

    if success_orders:
        print(f"\n最近30分钟的网关订单: {len(success_orders)} 条\n")
        for row in success_orders:
            icon = "✅" if row[3] == "filled" else "⏳"
            print(f"{icon} {row[0]} | {row[1]} {row[2]} | {row[3]} | ID:{row[4]}")
    else:
        print("\n⚠️  最近30分钟没有网关订单")

    conn.close()

    # 总结
    print("\n" + "=" * 80)
    print("修复验证总结")
    print("=" * 80)

    if len(recent_events) == 0:
        print("\n✅ 修复完全生效！")
        print("  - 不再创建重复的风险事件")
        print("  - 系统可以正常开单")
        print("\n建议：继续监控30分钟，确保稳定")
    else:
        print("\n❌ 修复未生效或部分生效")
        print("  - 可能需要重启Celery worker")
        print("  - 或者代码有bug")


if __name__ == "__main__":
    main()
