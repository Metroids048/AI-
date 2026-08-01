"""验证量化交易系统是否准备好 7x24 小时自动交易。

运行方式:
    python scripts/verify-system-ready.py

检查项:
    1. 数据库连接
    2. Redis 连接
    3. Binance API 连接
    4. Top3 研究范围加载
    5. 信号生成（验证 confidence_multiplier 不会归零）
    6. 风控参数合理性
"""

# Project-root path bootstrap intentionally precedes application imports.
# ruff: noqa: E402

import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import traceback

from services.data import DataRepository
from services.database import get_session_factory
from services.execution.decision_pipeline import DecisionPipeline
from services.strategy_library import StrategyRepository
from shared.config import settings


def check_database():
    """检查数据库连接。"""
    print("\n[1/6] 检查数据库连接...")
    try:
        with get_session_factory()() as session:
            result = session.execute("SELECT 1").scalar()
            if result == 1:
                print("✓ 数据库连接正常")
                return True
    except Exception as e:
        print(f"✗ 数据库连接失败: {e}")
        print(f"  当前配置: {settings.postgres_url}")
        print("  修复建议: 检查 docker-compose.yml 是否暴露了 5432 端口")
        print("           或使用 .env.local 配置（localhost 模式）")
        return False


def check_redis():
    """检查 Redis 连接。"""
    print("\n[2/6] 检查 Redis 连接...")
    try:
        import redis

        client = redis.from_url(settings.redis_url)
        client.ping()
        print("✓ Redis 连接正常")
        return True
    except Exception as e:
        print(f"✗ Redis 连接失败: {e}")
        print(f"  当前配置: {settings.redis_url}")
        return False


def check_binance_api():
    """检查 Binance API 连接。"""
    print("\n[3/6] 检查 Binance API 连接...")
    try:
        from services.data.binance_client import BinanceCcxtClient

        client = BinanceCcxtClient()
        ticker = client.fetch_ticker("BTC/USDT")
        print(f"✓ Binance API 正常 (BTC/USDT: ${ticker['last']})")
        return True
    except Exception as e:
        print(f"✗ Binance API 连接失败: {e}")
        print(f"  Testnet 模式: {settings.binance_use_testnet}")
        return False


def check_research_universe():
    """检查当前自动研究范围。"""
    print("\n[4/6] 检查 Top3 研究范围...")
    try:
        from services.data.universe import AUTO_PAPER_RESEARCH_SYMBOLS

        if len(AUTO_PAPER_RESEARCH_SYMBOLS) == 3:
            print(f"✓ Top3 研究范围正常 ({len(AUTO_PAPER_RESEARCH_SYMBOLS)} 个币种)")
            print(f"  标的: {', '.join(AUTO_PAPER_RESEARCH_SYMBOLS)}")
            return True
        else:
            print(f"✗ Top3 研究范围数量错误: {len(AUTO_PAPER_RESEARCH_SYMBOLS)}")
            return False
    except Exception as e:
        print(f"✗ Top3 研究范围加载失败: {e}")
        return False


def check_signal_generation():
    """检查信号生成（最关键的验证）。"""
    print("\n[5/6] 检查信号生成（confidence_multiplier 不会归零）...")
    try:
        with get_session_factory()() as session:
            data_repo = DataRepository(session)
            strategy_repo = StrategyRepository(session)

            # 获取第一个策略
            strategies = strategy_repo.list_strategies()
            if not strategies:
                print("⚠ 没有找到策略，跳过信号生成测试")
                return True

            strategy = strategies[0]
            print(f"  使用策略: {strategy.strategy_key}")

            # 测试信号生成
            pipeline = DecisionPipeline(data_repo=data_repo, strategy_repo=strategy_repo)
            result = pipeline.evaluate(
                strategy=strategy, symbol="BTC/USDT", timeframe="15m", enable_decision_veto=False, relaxed_signals=False
            )

            print(f"  confidence_multiplier: {result.confidence_multiplier}")
            print(f"  should_trade: {result.should_trade}")
            print(f"  reason: {result.reason}")

            # 验证修复生效
            if result.confidence_multiplier == 0.0 and not result.should_trade:
                print("✗ CRITICAL: confidence_multiplier 仍然为 0.0")
                print("  这会导致 notional=0 和 sizing_sentinel 拒绝")
                print("  修复未生效！请检查 decision_pipeline.py 第 615 行")
                return False

            print("✓ 信号生成正常（confidence_multiplier 不会归零）")
            return True

    except Exception as e:
        print(f"✗ 信号生成测试失败: {e}")
        traceback.print_exc()
        return False


def check_risk_params():
    """检查风控参数合理性。"""
    print("\n[6/6] 检查风控参数...")
    try:
        with get_session_factory()() as session:
            strategy_repo = StrategyRepository(session)
            strategies = strategy_repo.list_strategies()

            if not strategies:
                print("⚠ 没有策略，跳过风控检查")
                return True

            issues = []
            for strategy in strategies:
                risk_per_trade = strategy.rules.position_rules.get("risk_per_trade", 0)
                max_leverage = strategy.rules.position_rules.get("max_leverage", 0)

                if risk_per_trade <= 0 or risk_per_trade > 0.1:
                    issues.append(f"{strategy.strategy_key}: risk_per_trade={risk_per_trade} 不合理")

                if max_leverage <= 0 or max_leverage > 125:
                    issues.append(f"{strategy.strategy_key}: max_leverage={max_leverage} 不合理")

            if issues:
                print("✗ 风控参数存在问题:")
                for issue in issues:
                    print(f"  - {issue}")
                return False

            print(f"✓ 风控参数正常 ({len(strategies)} 个策略)")
            return True

    except Exception as e:
        print(f"✗ 风控参数检查失败: {e}")
        return False


def main():
    """运行所有检查。"""
    print("=" * 60)
    print("量化交易系统就绪状态验证")
    print("=" * 60)

    checks = [
        check_database,
        check_redis,
        check_binance_api,
        check_research_universe,
        check_signal_generation,
        check_risk_params,
    ]

    results = []
    for check in checks:
        try:
            result = check()
            results.append(result)
        except Exception as e:
            print(f"\n✗ 检查过程中发生异常: {e}")
            traceback.print_exc()
            results.append(False)

    # 总结
    print("\n" + "=" * 60)
    print("验证总结")
    print("=" * 60)

    passed = sum(results)
    total = len(results)

    print(f"\n通过: {passed}/{total}")

    if all(results):
        print("\n✓ 系统已准备好 7x24 小时自动交易！")
        print("\n下一步:")
        print("  1. 启动 Docker 容器: docker-compose up -d timescaledb redis")
        print("  2. 运行 API 服务: python -m uvicorn apps.api.main:app --reload")
        print("  3. 监控日志查看开单情况")
        return 0
    else:
        print("\n✗ 系统尚未准备好，请修复以上问题")
        print("\n参考文档:")
        print("  - docs/diagnosis/system-readiness-2026-07-15.md")
        print("  - docs/hotfix/HOTFIX-001-carry-hedge-indentation.md")
        return 1


if __name__ == "__main__":
    sys.exit(main())
