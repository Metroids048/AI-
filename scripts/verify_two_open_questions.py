#!/usr/bin/env python3
"""核实两个疑点：
1. 3820b851这个run是什么，为什么没有evaluated_risk_state
2. run 35298c65的drawdown数值是否在多个周期里持续保持低位（不是偶然一次）
"""

import json
import sqlite3

conn = sqlite3.connect(".local_paper_console.db")
cursor = conn.cursor()

print("=== 疑点1: run 3820b851 的完整信息 ===\n")
cursor.execute(
    "SELECT paper_run_id, strategy_id, paper_status, execution_profile FROM paper_runs WHERE paper_run_id LIKE '3820b851%'"
)
row = cursor.fetchone()
if row:
    profile = json.loads(row[3]) if row[3] else {}
    print(f"run_id: {row[0]}")
    print(f"strategy_id: {row[1]}")
    print(f"status: {row[2]}")
    print(f"execution_mode: {profile.get('execution_mode')}")
    print(f"mirror_to_gateway: {profile.get('mirror_to_gateway')}")
    print(f"auto_paper_runtime_key: {profile.get('auto_paper_runtime_key')}")
else:
    print("找不到这个run")

print("\n对应订单的order_origin:")
cursor.execute("""
    SELECT created_at, order_origin, execution_status, gateway_order_id, evaluated_risk_state
    FROM order_executions
    WHERE paper_run_id LIKE '3820b851%'
    ORDER BY created_at DESC LIMIT 3
""")
for r in cursor.fetchall():
    print(f"  {r[0]} | origin={r[1]} | {r[2]} | gw={r[3]} | has_risk_state={r[4] is not None}")

print("\n=== 疑点2: run 35298c65 在risk_profile_sweep里的策略权益趋势(最近10次) ===\n")
cursor.execute("""
    SELECT paper_metrics_summary FROM paper_runs WHERE paper_run_id = '35298c65-cdbe-4bee-bee3-b7ded07c3204'
""")
metrics = json.loads(cursor.fetchone()[0])
print(f"当前 account_equity: {metrics.get('account_equity')}")
print(f"当前 equity_peak (旧/污染): {metrics.get('equity_peak')}")
print(f"当前 strategy_equity_peak (新/修复): {metrics.get('strategy_equity_peak')}")

acct = float(metrics.get("account_equity", 0))
old_peak = float(metrics.get("equity_peak", acct))
new_peak = float(metrics.get("strategy_equity_peak", acct))

old_dd = max(0.0, (old_peak - acct) / old_peak) if old_peak > 0 else 0
print(f"\n如果继续用旧equity_peak算: drawdown = {old_dd * 100:.2f}% (会被阻塞)")
print("用新strategy_equity_peak算的口径应该是: 需要减去manual_pnl才准确")

print("\n=== 该run最近所有订单的account_equity/equity_peak时间序列 ===\n")
cursor.execute("""
    SELECT created_at, execution_status, rejection_reason, evaluated_risk_state
    FROM order_executions
    WHERE paper_run_id = '35298c65-cdbe-4bee-bee3-b7ded07c3204'
    ORDER BY created_at DESC LIMIT 10
""")
for r in cursor.fetchall():
    if r[3]:
        try:
            rs = json.loads(r[3])
            eq, peak = rs.get("account_equity"), rs.get("equity_peak")
            dd = round(max(0.0, (peak - eq) / peak) * 100, 2) if eq is not None and peak else None
            print(f"{r[0]} | {r[1]} | reject={r[2]} | eq={eq} peak={peak} dd={dd}%")
        except Exception:
            print(f"{r[0]} | {r[1]} | (parse error)")
    else:
        print(f"{r[0]} | {r[1]} | reject={r[2]} | no risk_state")

conn.close()
