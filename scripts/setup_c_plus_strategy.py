"""启动C+策略Paper测试。

使用场景：
1. 初次启动C+策略的Paper模拟交易
2. 验证五层架构是否正常工作
3. 48-72小时自然开平单验证
"""

import argparse
import logging
import sys
from datetime import UTC, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from services.data import DataRepository
from services.execution.c_plus_adapter import CPlusDecisionAdapter
from services.execution.c_plus_config import (
    C_PLUS_EXECUTION_SYMBOLS,
    C_PLUS_STRATEGY_KEY,
    C_PLUS_STRATEGY_RULES,
    C_PLUS_TIMEFRAME,
)
from services.strategy_library import StrategyRepository
from services.strategy_library.technical.pandas_ta_adapter import generate_all_technical_signals
from shared.config import settings
from shared.models import StrategyContract

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def setup_c_plus_strategy(session: Session) -> StrategyContract:
    """注册或更新C+策略."""
    strategy_repo = StrategyRepository(session)

    # 检查是否已存在
    existing = strategy_repo.get_by_key(C_PLUS_STRATEGY_KEY)
    if existing:
        logger.info(f"C+ strategy already exists: {existing.strategy_id}")
        return existing

    # 创建新策略
    strategy = StrategyContract(
        strategy_id=C_PLUS_STRATEGY_KEY,
        name="C+ Weighted Ensemble",
        version="1.0.0",
        description="Five-layer weighted ensemble strategy with regime routing",
        market="crypto_futures",
        timeframe=C_PLUS_TIMEFRAME,
        symbols=C_PLUS_EXECUTION_SYMBOLS,
        rules=C_PLUS_STRATEGY_RULES,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    strategy_repo.save(strategy)
    logger.info(f"Created C+ strategy: {strategy.strategy_id}")
    return strategy


def verify_c_plus_pipeline(session: Session) -> bool:
    """验证C+决策流程是否正常工作."""
    logger.info("Verifying C+ pipeline...")

    data_repo = DataRepository(session)
    adapter = CPlusDecisionAdapter(data_repo=data_repo)

    strategy = setup_c_plus_strategy(session)

    # 对每个交易对测试一次
    for symbol in C_PLUS_EXECUTION_SYMBOLS:
        logger.info(f"Testing C+ pipeline for {symbol}...")

        # 生成技术信号
        try:
            signals = generate_all_technical_signals(
                symbol=symbol,
                timeframe=C_PLUS_TIMEFRAME,
                data_repo=data_repo,
            )
            logger.info(f"Generated {len(signals)} signals for {symbol}")

            # 运行C+决策
            result = adapter.run_c_plus_decision(
                symbol=symbol,
                timeframe=C_PLUS_TIMEFRAME,
                signals=signals,
                strategy=strategy,
            )

            logger.info(f"C+ decision for {symbol}:")
            logger.info(f"  Decision ID: {result.decision_id}")
            logger.info(f"  Regime: {result.regime}")
            logger.info(f"  Direction: {result.direction}")
            logger.info(f"  Strategy Family: {result.strategy_family}")
            logger.info(f"  Confidence: {result.confidence:.2f}")
            logger.info(f"  Rejection: {result.rejection_reason}")

            if result.direction:
                logger.info(f"  Entry: {result.entry_price}")
                logger.info(f"  Stop: {result.stop_price}")
                logger.info(f"  Target: {result.take_profit_price}")

        except Exception as e:
            logger.exception(f"C+ pipeline test failed for {symbol}: {e}")
            return False

    logger.info("C+ pipeline verification completed successfully")
    return True


def main():
    """主函数."""
    parser = argparse.ArgumentParser(description="启动C+策略Paper测试")
    parser.add_argument(
        "--database-url",
        default=settings.database_url,
        help="数据库连接URL",
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="仅验证流程，不启动Paper运行",
    )
    args = parser.parse_args()

    engine = create_engine(args.database_url)

    with Session(engine) as session:
        # 注册策略
        setup_c_plus_strategy(session)
        session.commit()

        # 验证流程
        if not verify_c_plus_pipeline(session):
            logger.error("C+ pipeline verification failed")
            sys.exit(1)

        if args.verify_only:
            logger.info("Verification completed, exiting")
            return

        logger.info("=" * 80)
        logger.info("C+ Strategy Setup Complete")
        logger.info("=" * 80)
        logger.info(f"Strategy Key: {C_PLUS_STRATEGY_KEY}")
        logger.info(f"Execution Symbols: {', '.join(C_PLUS_EXECUTION_SYMBOLS)}")
        logger.info(f"Timeframe: {C_PLUS_TIMEFRAME}")
        logger.info("")
        logger.info("Next Steps:")
        logger.info("1. 配置Paper调度器使用C+策略")
        logger.info("2. 启动Paper调度器：python -m scripts.run_paper_scheduler")
        logger.info("3. 监控48-72小时自然开平单")
        logger.info("4. 运行历史回放验证：python -m scripts.run_c_plus_backtest")
        logger.info("=" * 80)


if __name__ == "__main__":
    main()
