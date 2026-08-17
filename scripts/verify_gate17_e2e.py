#!/usr/bin/env python3
"""Gate 17 端到端验收脚本 (2026-08-07)

验证三个修复是否真正打通了自动开单链路：
- R-01: 最小名义金额优先级修复
- R-02: MTF UNCERTAIN 下允许大多数一致
- R-03: ADX 15-25 with EMA bias 正确进入 TREND

不仅检查统计数字，还检查漏斗各阶段的通过情况。

使用方法:
    python scripts/verify_gate17_e2e.py --database-url sqlite:///.local_paper_console.db
"""

import argparse
import sqlite3
from datetime import UTC, datetime, timedelta


def check_mtf_passes(cursor: sqlite3.Cursor, lookback_hours: int = 2) -> dict:
    """检查 MTF 是否在 UNCERTAIN 下有通过记录."""
    cutoff = (datetime.now(UTC) - timedelta(hours=lookback_hours)).isoformat()

    cursor.execute(
        """
        SELECT COUNT(*)
        FROM decision_snapshots
        WHERE created_at > ?
        AND trace LIKE '%multi_timeframe_disagreement%'
    """,
        (cutoff,),
    )
    mtf_blocked = cursor.fetchone()[0]

    cursor.execute(
        """
        SELECT COUNT(*)
        FROM decision_snapshots
        WHERE created_at > ?
        AND trace LIKE '%uncertain_majority_confirmed%'
    """,
        (cutoff,),
    )
    mtf_uncertain_passed = cursor.fetchone()[0]

    return {
        "mtf_blocked": mtf_blocked,
        "mtf_uncertain_passed": mtf_uncertain_passed,
    }


def check_regime_distribution(cursor: sqlite3.Cursor, lookback_hours: int = 2) -> dict:
    """检查 regime 分布是否符合预期."""
    cutoff = (datetime.now(UTC) - timedelta(hours=lookback_hours)).isoformat()

    cursor.execute(
        """
        SELECT
            CASE
                WHEN correlation_matrix_ref LIKE '%''market_regime'': ''range''%' THEN 'range'
                WHEN correlation_matrix_ref LIKE '%''market_regime'': ''trend_up''%' THEN 'trend_up'
                WHEN correlation_matrix_ref LIKE '%''market_regime'': ''trend_down''%' THEN 'trend_down'
                WHEN correlation_matrix_ref LIKE '%''market_regime'': ''uncertain''%' THEN 'uncertain'
                ELSE 'other'
            END as regime,
            COUNT(*) as cnt
        FROM signal_ensembles
        WHERE created_at > ?
        GROUP BY regime
    """,
        (cutoff,),
    )

    results = cursor.fetchall()
    total = sum(cnt for _, cnt in results)

    distribution = dict(results)
    percentages = {regime: (cnt / total * 100 if total > 0 else 0) for regime, cnt in results}

    return {
        "total": total,
        "distribution": distribution,
        "percentages": percentages,
    }


def check_trade_intents(cursor: sqlite3.Cursor, lookback_hours: int = 2) -> dict:
    """检查是否生成了 TradeIntent."""
    cutoff = (datetime.now(UTC) - timedelta(hours=lookback_hours)).isoformat()

    cursor.execute(
        """
        SELECT COUNT(*)
        FROM decision_snapshots
        WHERE created_at > ?
        AND bet_taken = 1
    """,
        (cutoff,),
    )
    trade_intents = cursor.fetchone()[0]

    return {"trade_intents": trade_intents}


def check_binance_orders(cursor: sqlite3.Cursor, lookback_hours: int = 2) -> dict:
    """检查是否有真实的 Binance Testnet 订单."""
    cutoff = (datetime.now(UTC) - timedelta(hours=lookback_hours)).isoformat()

    cursor.execute(
        """
        SELECT COUNT(*)
        FROM order_executions
        WHERE created_at > ?
        AND exchange_order_id IS NOT NULL
        AND exchange_order_id != ''
    """,
        (cutoff,),
    )
    binance_orders = cursor.fetchone()[0]

    cursor.execute(
        """
        SELECT COUNT(*)
        FROM order_executions
        WHERE created_at > ?
        AND lifecycle_status = 'binance_auto_execute_failed'
    """,
        (cutoff,),
    )
    binance_failures = cursor.fetchone()[0]

    cursor.execute(
        """
        SELECT COUNT(*)
        FROM order_executions
        WHERE created_at > ?
        AND lifecycle_status = 'binance_auto_execute_failed'
        AND failure_reason LIKE '%normalized notional is below exchange minimum%'
    """,
        (cutoff,),
    )
    notional_too_low_failures = cursor.fetchone()[0]

    return {
        "binance_orders": binance_orders,
        "binance_failures": binance_failures,
        "notional_too_low_failures": notional_too_low_failures,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Gate 17 端到端验收脚本")
    parser.add_argument(
        "--database-url",
        default="sqlite:///.local_paper_console.db",
        help="数据库连接字符串",
    )
    parser.add_argument(
        "--lookback-hours",
        type=int,
        default=2,
        help="回溯时间窗口（小时）",
    )
    args = parser.parse_args()

    db_path = args.database_url.replace("sqlite:///", "")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    print("=" * 80)
    print("Gate 17 端到端验收 (2026-08-07)")
    print("=" * 80)
    print(f"\n数据库: {db_path}")
    print(f"时间窗口: 最近 {args.lookback_hours} 小时\n")

    # 1. Regime 分布检查
    print("=" * 80)
    print("1. Regime 分布检查 (R-03)")
    print("=" * 80)
    regime_data = check_regime_distribution(cursor, args.lookback_hours)

    if regime_data["total"] == 0:
        print("\n⚠️  没有找到 ensemble 记录，可能系统未运行或数据库路径错误")
    else:
        print(f"\n总计: {regime_data['total']} 条 ensemble 记录\n")
        for regime, pct in regime_data["percentages"].items():
            cnt = regime_data["distribution"].get(regime, 0)
            print(f"  {regime:<15s} {cnt:>5d} ({pct:>5.1f}%)")

        range_pct = regime_data["percentages"].get("range", 0)
        trend_up_pct = regime_data["percentages"].get("trend_up", 0)
        trend_down_pct = regime_data["percentages"].get("trend_down", 0)
        trend_total_pct = trend_up_pct + trend_down_pct

        print("\n✓ R-03 目标: RANGE ≤ 70%, TREND > 10%")
        r03_pass = range_pct <= 70.0 and trend_total_pct > 10.0
        print(f"  结果: {'✅ PASS' if r03_pass else '❌ FAIL'}")
        print(f"  RANGE={range_pct:.1f}%, TREND={trend_total_pct:.1f}%")

    # 2. MTF 检查
    print("\n" + "=" * 80)
    print("2. MTF 多时间框架检查 (R-02)")
    print("=" * 80)
    mtf_data = check_mtf_passes(cursor, args.lookback_hours)

    print(f"\nMTF 拒绝数: {mtf_data['mtf_blocked']}")
    print(f"MTF UNCERTAIN 大多数通过数: {mtf_data['mtf_uncertain_passed']}")

    r02_pass = mtf_data["mtf_uncertain_passed"] > 0 or mtf_data["mtf_blocked"] == 0
    print("\n✓ R-02 目标: UNCERTAIN 下至少有部分 MTF 通过（大多数一致规则生效）")
    print(f"  结果: {'✅ PASS' if r02_pass else '⚠️  需要更多数据'}")

    # 3. TradeIntent 生成检查
    print("\n" + "=" * 80)
    print("3. TradeIntent 生成检查")
    print("=" * 80)
    intent_data = check_trade_intents(cursor, args.lookback_hours)

    print(f"\nTradeIntent 生成数: {intent_data['trade_intents']}")
    print("\n✓ 目标: 至少生成 1 个 TradeIntent")
    intent_pass = intent_data["trade_intents"] > 0
    print(f"  结果: {'✅ PASS' if intent_pass else '❌ FAIL - 信号未通过融合/MTF/Gatekeeper'}")

    # 4. Binance 订单检查
    print("\n" + "=" * 80)
    print("4. Binance Testnet 订单检查 (R-01)")
    print("=" * 80)
    order_data = check_binance_orders(cursor, args.lookback_hours)

    print(f"\nBinance 成功订单数: {order_data['binance_orders']}")
    print(f"Binance 失败订单数: {order_data['binance_failures']}")
    print(f"  其中名义金额过低失败: {order_data['notional_too_low_failures']}")

    r01_pass = order_data["binance_orders"] > 0 or (
        order_data["binance_failures"] > 0 and order_data["notional_too_low_failures"] == 0
    )
    print("\n✓ R-01 目标: 有成功订单 OR 失败订单中无 'notional too low' 错误")
    print(f"  结果: {'✅ PASS' if r01_pass else '❌ FAIL - 最小金额bug仍存在'}")

    # 总结
    print("\n" + "=" * 80)
    print("验收总结")
    print("=" * 80)

    if regime_data["total"] == 0:
        print("\n❌ 无数据，无法验收。请确保：")
        print("   1. 系统已启动并运行至少 2 小时")
        print("   2. 数据库路径正确")
        print("   3. RuntimeScheduler 正在调度 Paper 周期")
    else:
        all_pass = r03_pass and r02_pass and intent_pass and r01_pass

        if all_pass:
            print("\n🎉 所有检查通过！Gate 17 修复已完整打通自动开单链路。")
        else:
            print("\n⚠️  部分检查未通过，详见上述各项结果。")
            if not intent_pass:
                print("\n💡 提示：如果没有 TradeIntent，检查：")
                print("   - decision_snapshots 表中的 skip_reason")
                print("   - signal_ensembles 表中的 eligible_count")
                print("   - 运行 agent-python scripts/audit_decision_funnel.py 查看完整漏斗")

    conn.close()


if __name__ == "__main__":
    main()
