"""简单直接的数据库检查"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import text

from services.database import get_db_session


def main():
    print("=" * 80)
    print("数据库诊断：为什么没有开单")
    print("=" * 80)

    for session in get_db_session():
        # 1. 检查最近的订单执行
        print("\n【1. 最近订单执行记录 (order_executions)】")
        result = session.execute(
            text("""
            SELECT
                created_at,
                symbol,
                side,
                status,
                rejection_reason,
                strategy_key
            FROM order_executions
            ORDER BY created_at DESC
            LIMIT 20
        """)
        )
        orders = result.fetchall()

        if orders:
            print(f"找到 {len(orders)} 条最近订单:")
            for o in orders:
                print(f"  {o[0]} | {o[1]:12} | {o[2]:5} | {o[3]:12} | {o[5]:25} | {o[4] or ''}")
        else:
            print("  ⚠️  order_executions表中没有任何订单！")

        # 2. 统计最近24小时订单
        print("\n【2. 最近24小时订单统计】")
        result = session.execute(
            text("""
            SELECT
                COUNT(*) as total,
                COUNT(CASE WHEN status = 'rejected' THEN 1 END) as rejected,
                COUNT(CASE WHEN status = 'filled' THEN 1 END) as filled,
                COUNT(CASE WHEN status = 'pending' THEN 1 END) as pending
            FROM order_executions
            WHERE created_at >= NOW() - INTERVAL '24 hours'
        """)
        )
        stats = result.fetchone()
        print(f"  总订单: {stats[0]}")
        print(f"  已拒绝: {stats[1]}")
        print(f"  已成交: {stats[2]}")
        print(f"  待处理: {stats[3]}")

        # 3. 拒单原因统计
        if stats[1] > 0:
            print("\n【3. 拒单原因统计（最近24小时）】")
            result = session.execute(
                text("""
                SELECT
                    rejection_reason,
                    COUNT(*) as count
                FROM order_executions
                WHERE status = 'rejected'
                  AND created_at >= NOW() - INTERVAL '24 hours'
                  AND rejection_reason IS NOT NULL
                GROUP BY rejection_reason
                ORDER BY count DESC
                LIMIT 10
            """)
            )
            reasons = result.fetchall()
            for reason, count in reasons:
                print(f"  {reason}: {count}次")

        # 4. 检查策略活跃度
        print("\n【4. 策略活跃度（最近24小时）】")
        result = session.execute(
            text("""
            SELECT
                strategy_key,
                COUNT(*) as signal_count
            FROM order_executions
            WHERE created_at >= NOW() - INTERVAL '24 hours'
            GROUP BY strategy_key
            ORDER BY signal_count DESC
            LIMIT 10
        """)
        )
        strategies = result.fetchall()
        if strategies:
            for strategy, count in strategies:
                print(f"  {strategy or 'NULL':35}: {count}个订单尝试")
        else:
            print("  没有任何策略产生订单")

        # 5. 检查系统是否在运行
        print("\n【5. 调度器心跳检查】")
        result = session.execute(
            text("""
            SELECT
                lease_name,
                owner_id,
                hostname,
                heartbeat_at,
                expires_at,
                CASE
                    WHEN expires_at > NOW() THEN 'ACTIVE'
                    ELSE 'EXPIRED'
                END as status
            FROM scheduler_leases
            ORDER BY heartbeat_at DESC
            LIMIT 5
        """)
        )
        leases = result.fetchall()
        if leases:
            for lease in leases:
                print(f"  {lease[0]:30} | {lease[5]:8} | 最后心跳: {lease[3]}")
        else:
            print("  ⚠️  没有找到调度器租约记录！系统可能没有在运行")

    print("\n" + "=" * 80)


if __name__ == "__main__":
    main()
