#!/usr/bin/env python3
"""检查strategy_equity修复是否真的在生效"""

import json
import sqlite3


def main():
    conn = sqlite3.connect(".local_paper_console.db")
    cursor = conn.cursor()

    run_id = "35298c65-cdbe-4bee-bee3-b7ded07c3204"

    print("\n" + "=" * 80)
    print("检查strategy_equity修复效果")
    print("=" * 80)

    cursor.execute(
        """
        SELECT paper_metrics_summary
        FROM paper_runs
        WHERE paper_run_id = ?
    """,
        (run_id,),
    )

    row = cursor.fetchone()
    if row:
        metrics = json.loads(row[0]) if row[0] else {}

        print(f"\naccount_equity: {metrics.get('account_equity')}")
        print(f"equity_peak: {metrics.get('equity_peak')}")
        print(f"strategy_equity_peak: {metrics.get('strategy_equity_peak', '❌ 字段不存在，说明修复代码从未运行过')}")

        if "strategy_equity_peak" in metrics:
            print("\n✅ 修复代码已经运行过，strategy_equity_peak字段存在")
        else:
            print("\n❌ 修复代码从未成功运行，说明risk_profile_sweep里的手动持仓排除逻辑没有执行")
            print("   可能原因：gateway.reconcile()调用失败，或者Celery worker还在用旧代码")

    conn.close()


if __name__ == "__main__":
    main()
