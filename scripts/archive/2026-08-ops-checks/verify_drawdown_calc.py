#!/usr/bin/env python3
"""验证当前drawdown计算是否会再次触发阻塞"""

import json
import sqlite3


def main():
    conn = sqlite3.connect(".local_paper_console.db")
    cursor = conn.cursor()

    run_id = "35298c65-cdbe-4bee-bee3-b7ded07c3204"

    cursor.execute(
        """
        SELECT paper_metrics_summary
        FROM paper_runs
        WHERE paper_run_id = ?
    """,
        (run_id,),
    )

    row = cursor.fetchone()
    metrics = json.loads(row[0]) if row[0] else {}

    account_equity = float(metrics.get("account_equity", 10000))
    equity_peak = float(metrics.get("equity_peak", account_equity))
    strategy_equity_peak = float(metrics.get("strategy_equity_peak", account_equity))

    old_drawdown = max(0.0, (equity_peak - account_equity) / max(equity_peak, 1.0))

    print("\n=== 旧计算方式（被手动持仓污染） ===")
    print(f"account_equity: {account_equity}")
    print(f"equity_peak: {equity_peak}")
    print(f"drawdown: {old_drawdown:.4f} ({old_drawdown * 100:.2f}%)")
    print(f"  -> {'⚠️ 会触发阻塞(>=25%)' if old_drawdown >= 0.25 else '✅ 不会触发'}")

    print("\n=== 新计算方式（strategy_equity_peak记录） ===")
    print(f"strategy_equity_peak: {strategy_equity_peak}")
    print("\n注意：strategy_equity（当次扣除手动持仓浮亏后的值）没有持久化存储，")
    print("只有strategy_equity_peak被存了。需要看下次sweep运行时重新计算的drawdown。")

    # 估算：如果manual_pnl大致稳定，用strategy_equity_peak和当前account_equity的差值做粗略估计
    # 无法精确复现，因为manual_pnl是实时查询的
    print(f"\n粗略估计：如果strategy_equity接近strategy_equity_peak({strategy_equity_peak:.2f})，")
    print("说明账户里手动持仓浮亏被正确扣除了，drawdown会趋近于0%")

    conn.close()


if __name__ == "__main__":
    main()
