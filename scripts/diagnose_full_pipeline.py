"""完整的信号-订单链路诊断（修正版）

从 signal_generator.generate_order() 到 Binance 订单提交的每一步，
找出所有可能的拒绝点和当前状态。
"""

import json
import sqlite3
from pathlib import Path

db = Path(__file__).parents[1] / ".local_paper_console.db"
conn = sqlite3.connect(str(db))

print("=== 1. 最近信号决策管道输出 ===")
row = conn.execute(
    "SELECT paper_metrics_summary FROM paper_runs WHERE paper_run_id LIKE '35298c65%' LIMIT 1"
).fetchone()
if row:
    m = json.loads(row[0] or "{}")
    actions = m.get("last_cycle_actions", [])
    for a in actions[:6]:
        if a.get("action") in ("skip_no_trade_decision", "rejected", "skip_duplicate_cycle"):
            trace = a.get("decision_trace", {})
            reason = a.get("reason", "")
            print(f"  {a['symbol']:10} action={a['action']:30} reason={reason[:80]}")
            if trace:
                print(f"             trace={json.dumps(trace, ensure_ascii=False)[:200]}")

print("\n=== 2. 决策事件：最近10次信号评估结果 ===")
rows = conn.execute(
    """
    SELECT symbol, event_type, payload, created_at
    FROM decision_events
    WHERE event_type IN ('execution_contract_rejected', 'candidate_accepted', 'order_submitted')
    ORDER BY created_at DESC LIMIT 10
    """
).fetchall()
for r in rows:
    p = json.loads(r[2] or "{}")
    print(f"  {r[3]} {r[0]:10} {r[1]:30} error={p.get('error', '')[:60]}")

print("\n=== 3. 实际提交到 Binance 的订单 ===")
tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
if "order_executions" in tables:
    rows = conn.execute(
        """
        SELECT symbol, side, status, exchange_order_id, created_at
        FROM order_executions
        WHERE exchange_order_id IS NOT NULL
        ORDER BY created_at DESC LIMIT 5
        """
    ).fetchall()
    if rows:
        for r in rows:
            print(f"  {r[4]} {r[0]:10} {r[1]:5} status={r[2]:15} exchange_id={r[3]}")
    else:
        print("  (无 Binance 订单)")
else:
    print("  (order_executions 表不存在)")

conn.close()

print("\n=== 诊断结论 ===")
print("最新决策事件停在 15:46，但系统已重启（新instance_id）并运行了25个周期。")
print("16:00之后的K线评估没有产生任何决策事件 → 信号在决策管道被静默拒绝。")
print()
print("下一步：查 decision_pipeline.py 看 should_trade=False 的具体原因。")
