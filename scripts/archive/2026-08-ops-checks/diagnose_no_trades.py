"""诊断为什么系统没有开单"""

import asyncio
import sys
from pathlib import Path

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from datetime import datetime, timedelta

from sqlalchemy import and_, desc, func, select

from services.database import get_db_session
from services.execution.models import Order
from services.strategy_library.models import Signal


async def main():
    print("=" * 80)
    print("诊断：为什么系统一单没开")
    print("=" * 80)

    async with get_db_session() as session:
        # 1. 检查信号生成情况
        print("\n【1. 信号生成情况】")
        one_day_ago = datetime.utcnow() - timedelta(hours=24)

        signal_count_24h = await session.scalar(
            select(func.count()).select_from(Signal).where(Signal.timestamp >= one_day_ago)
        )
        print(f"最近24小时信号总数: {signal_count_24h}")

        # 最近的信号
        recent_signals_result = await session.execute(select(Signal).order_by(desc(Signal.timestamp)).limit(10))
        recent_signals = recent_signals_result.scalars().all()

        if recent_signals:
            print("\n最近10条信号:")
            for s in recent_signals:
                print(
                    f"  {s.timestamp.strftime('%Y-%m-%d %H:%M:%S')} | "
                    f"{s.symbol:12} | {s.strategy_key:25} | "
                    f"{s.direction:5} | strength={s.strength:.3f}"
                )
        else:
            print("  ⚠️  没有找到任何信号！")

        # 2. 检查订单生成情况
        print("\n【2. 订单生成情况】")
        order_count_24h = await session.scalar(
            select(func.count()).select_from(Order).where(Order.created_at >= one_day_ago)
        )
        print(f"最近24小时订单总数: {order_count_24h}")

        # 最近的订单
        recent_orders_result = await session.execute(select(Order).order_by(desc(Order.created_at)).limit(20))
        recent_orders = recent_orders_result.scalars().all()

        if recent_orders:
            print("\n最近20个订单:")
            for o in recent_orders:
                reason = o.rejection_reason or ""
                print(
                    f"  {o.created_at.strftime('%Y-%m-%d %H:%M:%S')} | "
                    f"{o.symbol:12} | {o.side:5} | {o.status:12} | "
                    f"{reason[:60]}"
                )
        else:
            print("  ⚠️  没有找到任何订单！")

        # 3. 统计拒单原因
        if order_count_24h > 0:
            print("\n【3. 拒单原因统计（最近24小时）】")
            rejected_orders_result = await session.execute(
                select(Order.rejection_reason, func.count(Order.id))
                .where(and_(Order.created_at >= one_day_ago, Order.status == "rejected"))
                .group_by(Order.rejection_reason)
                .order_by(desc(func.count(Order.id)))
            )
            rejected_stats = rejected_orders_result.all()

            if rejected_stats:
                for reason, count in rejected_stats:
                    print(f"  {reason}: {count}次")
            else:
                print("  没有拒单")

        # 4. 检查最近的信号强度分布
        if signal_count_24h > 0:
            print("\n【4. 信号强度分布（最近24小时）】")
            strength_stats_result = await session.execute(
                select(
                    func.min(Signal.strength).label("min_strength"),
                    func.max(Signal.strength).label("max_strength"),
                    func.avg(Signal.strength).label("avg_strength"),
                ).where(Signal.timestamp >= one_day_ago)
            )
            strength_stats = strength_stats_result.first()

            if strength_stats:
                print(f"  最小强度: {strength_stats.min_strength:.3f}")
                print(f"  最大强度: {strength_stats.max_strength:.3f}")
                print(f"  平均强度: {strength_stats.avg_strength:.3f}")

        # 5. 检查策略分布
        if signal_count_24h > 0:
            print("\n【5. 策略信号分布（最近24小时，Top 10）】")
            strategy_stats_result = await session.execute(
                select(Signal.strategy_key, func.count(Signal.id))
                .where(Signal.timestamp >= one_day_ago)
                .group_by(Signal.strategy_key)
                .order_by(desc(func.count(Signal.id)))
                .limit(10)
            )
            strategy_stats = strategy_stats_result.all()

            for strategy, count in strategy_stats:
                print(f"  {strategy:35}: {count}个信号")

    print("\n" + "=" * 80)
    print("诊断完成")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
