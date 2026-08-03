#!/usr/bin/env python3
"""调查drawdown_limit_breached的根本原因"""

import json
import sqlite3


def main():
    conn = sqlite3.connect(".local_paper_console.db")
    cursor = conn.cursor()

    print("\n" + "=" * 80)
    print("Drawdown触发根本原因调查")
    print("=" * 80)

    # 1. 查看触发drawdown的PaperRun
    run_id = "35298c65-cdbe-4bee-bee3-b7ded07c3204"

    print(f"\n=== 1. 问题PaperRun: {run_id[:12]}... ===")
    cursor.execute(
        """
        SELECT paper_run_id, strategy_id, paper_status, paper_metrics_summary, execution_profile
        FROM paper_runs
        WHERE paper_run_id = ?
    """,
        (run_id,),
    )

    row = cursor.fetchone()
    if row:
        print(f"策略: {row[1]}")
        print(f"状态: {row[2]}")

        # 解析metrics
        try:
            metrics = json.loads(row[3]) if row[3] else {}
            print("\n关键指标:")
            print(f"  账户权益 (account_equity): {metrics.get('account_equity')}")
            print(f"  权益峰值 (equity_peak): {metrics.get('equity_peak')}")
            print(f"  连续亏损 (consecutive_losses): {metrics.get('consecutive_losses')}")
            print(f"  日内已实现盈亏: {metrics.get('daily_realized_pnl')}")
            print(f"  周已实现盈亏: {metrics.get('weekly_realized_pnl')}")

            # 计算drawdown
            equity = float(metrics.get("account_equity") or 10000)
            peak = float(metrics.get("equity_peak") or equity)
            drawdown = max(0.0, (peak - equity) / max(peak, 1.0))
            print(f"\n  计算的drawdown: {drawdown:.4f} ({drawdown * 100:.2f}%)")

        except Exception as e:
            print(f"  解析metrics失败: {e}")
            print(f"  原始metrics: {row[3][:200]}")

        # 解析execution_profile中的风险参数
        try:
            profile = json.loads(row[4]) if row[4] else {}
            print("\n风险参数:")
            print(f"  daily_loss_limit: {profile.get('daily_loss_limit')}")
            print(f"  risk_per_trade: {profile.get('risk_per_trade')}")
            print(f"  max_leverage: {profile.get('max_leverage')}")
        except Exception as e:
            print(f"  解析profile失败: {e}")

    # 2. 查看风险配置的drawdown_limit
    print("\n=== 2. RiskProfile的drawdown_limit配置 ===")
    cursor.execute("""
        SELECT risk_profile_id, drawdown_limit, hard_stop_drawdown_limit,
               consecutive_loss_limit, daily_loss_limit
        FROM risk_profiles
        LIMIT 5
    """)

    profiles = cursor.fetchall()
    if profiles:
        for row in profiles:
            print(f"\nProfile: {row[0]}")
            print(f"  drawdown_limit: {row[1]}")
            print(f"  hard_stop_drawdown_limit: {row[2]}")
            print(f"  consecutive_loss_limit: {row[3]}")
            print(f"  daily_loss_limit: {row[4]}")
    else:
        print("  没有找到RiskProfile记录（使用默认值）")

    # 3. 查看最近的持仓和盈亏
    print("\n=== 3. 最近的持仓盈亏情况 ===")
    cursor.execute(
        """
        SELECT symbol, position_side, quantity, management_status, opened_at
        FROM position_records
        WHERE run_id = ? AND management_status != 'CLOSED'
        ORDER BY opened_at DESC
        LIMIT 10
    """,
        (run_id,),
    )

    positions = cursor.fetchall()
    if positions:
        print(f"活跃持仓 ({len(positions)} 条):")
        for row in positions:
            print(f"  {row[0]} | {row[1]} | 数量:{row[2]} | {row[3]} | {row[4]}")
    else:
        print("  没有活跃持仓")

    # 4. 查看position_snapshots了解权益变化
    print("\n=== 4. 最近的权益快照 ===")
    cursor.execute("""
        SELECT snapshot_time, symbol, unrealized_pnl, mark_price
        FROM position_snapshots
        WHERE created_at > datetime('now', '-1 hour')
        ORDER BY snapshot_time DESC
        LIMIT 10
    """)

    snapshots = cursor.fetchall()
    if snapshots:
        print("最近权益快照:")
        for row in snapshots:
            print(f"  {row[0]} | {row[1]} | 未实现:{row[2]} | 标记价:{row[3]}")
    else:
        print("  没有权益快照记录")

    # 5. drawdown触发频率
    print("\n=== 5. Drawdown触发频率 ===")
    cursor.execute("""
        SELECT COUNT(*) as cnt, MIN(created_at) as first, MAX(created_at) as last
        FROM risk_events
        WHERE event_type = 'risk_limit_breach'
          AND description LIKE '%drawdown%'
          AND created_at > datetime('now', '-2 hours')
    """)

    row = cursor.fetchone()
    if row and row[0] > 0:
        print(f"最近2小时触发次数: {row[0]}")
        print(f"首次: {row[1]}")
        print(f"最近: {row[2]}")
        print("\n⚠️  这是每分钟的risk_profile_sweep任务在重复检测同一个drawdown状态")
    else:
        print("  最近2小时没有drawdown触发")

    # 6. 诊断结论
    print("\n" + "=" * 80)
    print("诊断结论")
    print("=" * 80)

    print("""
问题本质:
  risk_profile_sweep任务每分钟运行一次，检测到账户drawdown超过限制，
  就创建一个新的risk_limit_breach事件。

  即使我修复了expires_at（24小时过期），但只要drawdown状态持续存在，
  每分钟就会创建一个新事件，这些新事件在24小时内都是活跃的，
  会阻塞BTC/ETH的入场决策。

根本原因（二选一）:
  A. 账户确实处于drawdown状态（亏损），这是正常的风控触发
  B. drawdown计算逻辑有问题（比如equity_peak记录错误）

需要确认:
  1. account_equity vs equity_peak的实际数值
  2. drawdown_limit的配置值
  3. 是否是真实亏损还是计算错误
    """)

    conn.close()


if __name__ == "__main__":
    main()
