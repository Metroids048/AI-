#!/usr/bin/env python3
"""最终验证：修复后的策略drawdown是否脱离阻塞阈值"""

import json
import sqlite3

conn = sqlite3.connect(".local_paper_console.db")
cursor = conn.cursor()

run_id = "35298c65-cdbe-4bee-bee3-b7ded07c3204"
cursor.execute("SELECT paper_metrics_summary FROM paper_runs WHERE paper_run_id = ?", (run_id,))
metrics = json.loads(cursor.fetchone()[0])

account_equity = float(metrics["account_equity"])
manual_pnl = -757.96546533  # 刚才debug脚本算出的
strategy_equity = account_equity - manual_pnl
strategy_equity_peak = float(metrics["strategy_equity_peak"])
strategy_equity_peak = max(strategy_equity_peak, strategy_equity)

drawdown = max(0.0, (strategy_equity_peak - strategy_equity) / max(strategy_equity_peak, 1.0))

print(f"\naccount_equity (含手动ETH浮亏): {account_equity:.2f}")
print(f"手动持仓浮亏(manual_pnl): {manual_pnl:.2f}")
print(f"strategy_equity (扣除后): {strategy_equity:.2f}")
print(f"strategy_equity_peak: {strategy_equity_peak:.2f}")
print(f"策略drawdown: {drawdown:.4f} ({drawdown * 100:.2f}%)")
print("drawdown_limit: 0.25 (25%)")
print(f"\n{'✅ 已脱离阻塞区间' if drawdown < 0.25 else '❌ 仍会触发阻塞'}")

conn.close()
