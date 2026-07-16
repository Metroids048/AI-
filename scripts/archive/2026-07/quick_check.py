import sqlite3
import json

conn = sqlite3.connect('.local_paper_console.db')
cursor = conn.cursor()

print("=== 检查paper_runs表结构 ===")
cursor.execute("PRAGMA table_info(paper_runs)")
columns = [row[1] for row in cursor.fetchall()]
print(f"列: {columns}")

print("\n=== 检查是否有运行中的paper_run ===")
cursor.execute("SELECT paper_run_id, strategy_id, paper_status FROM paper_runs")
runs = cursor.fetchall()
print(f"找到 {len(runs)} 个paper_run:")
for run_id, strat_id, status in runs:
    print(f"  {run_id}: {strat_id} (状态: {status})")

print("\n=== 检查最新订单详情 ===")
cursor.execute("""
    SELECT symbol, direction, entry_context, execution_status
    FROM order_executions
    ORDER BY created_at DESC
    LIMIT 5
""")
orders = cursor.fetchall()

for symbol, direction, ctx_json, status in orders:
    print(f"\n{symbol} {direction} ({status})")
    if ctx_json:
        ctx = json.loads(ctx_json)
        # 只打印关键字段
        print(f"  来源: {ctx.get('paper_signal_source', 'N/A')}")
        print(f"  有decision_pipeline: {'是' if 'decision_pipeline' in ctx else '否'}")
        print(f"  有meta_label配置: {'是' if 'meta_label_win_rate' in ctx else '否'}")

conn.close()
