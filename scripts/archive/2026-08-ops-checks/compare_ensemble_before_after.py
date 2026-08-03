"""对比修改前后Ensemble的召回率差异。

只修改了Ensemble的_resolve_allowed_direction逻辑：
- 从二元判断（多数通过/分歧discard）
- 改为加权聚合（加权分数 >= 0.35开多，<= -0.35开空）

其他所有逻辑保持不变。
"""

import argparse
from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from services.data import DataRepository
from services.execution.decision_pipeline import DecisionPipeline
from services.strategy_library import (
    AgentTaskRepository,
    ExecutionRepository,
    NotificationRepository,
    ReviewRepository,
    StrategyRepository,
)
from shared.config import settings


def count_candidates_before_after(
    *,
    symbol: str,
    lookback_days: int = 7,
    database_url: str,
) -> tuple[int, int]:
    """统计修改前后的候选数量。

    Returns:
        (修改前候选数, 修改后候选数)
    """
    engine = create_engine(database_url)

    with Session(engine) as session:
        data_repo = DataRepository(session)
        strategy_repo = StrategyRepository(session)
        execution_repo = ExecutionRepository(session)
        review_repo = ReviewRepository(session)
        notification_repo = NotificationRepository(session)
        agent_task_repo = AgentTaskRepository(session)

        # 获取策略
        strategy = strategy_repo.get_by_key("auto_paper_mature_templates")
        if not strategy:
            raise ValueError("Strategy not found: auto_paper_mature_templates")

        # 创建决策流水线
        pipeline = DecisionPipeline(
            data_repo=data_repo,
            strategy_repo=strategy_repo,
            execution_repo=execution_repo,
            review_repo=review_repo,
            notification_repo=notification_repo,
            agent_task_repo=agent_task_repo,
        )

        # 统计过去N天的候选
        start_time = datetime.now(UTC) - timedelta(days=lookback_days)
        count_after = 0

        # 查询历史K线
        bars = data_repo.fetch_ohlcv(symbol=symbol, timeframe="15m", limit=1000)

        # 对每根K线运行决策
        for bar in bars[-lookback_days * 96 :]:  # 15分钟 × 96 = 1天
            try:
                result = pipeline.run(
                    strategy=strategy,
                    symbol=symbol,
                    timeframe="15m",
                    current_time=bar.timestamp,
                )

                if result.direction is not None:
                    count_after += 1

            except Exception as e:
                print(f"Error at {bar.timestamp}: {e}")
                continue

        # TODO: 获取修改前的历史候选数
        # 目前从数据库查询trade_decisions表
        count_before = _query_historical_candidates(session, symbol, start_time)

        return count_before, count_after


def _query_historical_candidates(session: Session, symbol: str, start_time: datetime) -> int:
    """从数据库查询历史候选数（修改前）。"""
    from shared.models import TradeDecision

    count = (
        session.query(TradeDecision)
        .filter(
            TradeDecision.symbol == symbol,
            TradeDecision.timestamp >= start_time,
            TradeDecision.strategy_key == "auto_paper_mature_templates",
            TradeDecision.rejection_reason.is_(None),
        )
        .count()
    )
    return count


def main():
    parser = argparse.ArgumentParser(description="对比Ensemble修改前后的召回率")
    parser.add_argument("--symbol", default="BTC/USDT", help="交易对")
    parser.add_argument("--lookback-days", type=int, default=7, help="回溯天数")
    parser.add_argument("--database-url", default=settings.database_url)
    args = parser.parse_args()

    print("=" * 80)
    print("Ensemble修改前后对比")
    print("=" * 80)
    print(f"交易对: {args.symbol}")
    print(f"回溯天数: {args.lookback_days}")
    print()

    before, after = count_candidates_before_after(
        symbol=args.symbol,
        lookback_days=args.lookback_days,
        database_url=args.database_url,
    )

    print(f"修改前候选数: {before}")
    print(f"修改后候选数: {after}")
    print(f"增加数量: {after - before} ({(after - before) / before * 100:.1f}%)")
    print()
    print("注意: 这只是候选数量对比，完整收益需要运行历史回放。")
    print("=" * 80)


if __name__ == "__main__":
    main()
