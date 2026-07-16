import sqlite3
from datetime import datetime
import json

conn = sqlite3.connect('.local_paper_console.db')
cursor = conn.cursor()

# 检查最近10分钟的所有订单
cursor.execute('''
SELECT
    datetime(created_at, 'localtime') as created,
    symbol,
    direction,
    execution_status,
    entry_context
FROM order_executions
WHERE created_at >= datetime('now', '-10 minutes')
ORDER BY created_at DESC
LIMIT 10
''')

orders = cursor.fetchall()
print(f'最近10分钟订单数: {len(orders)}')

if orders:
    for created, symbol, direction, status, ctx_json in orders:
        print(f'\n{created}: {symbol} {direction} ({status})')
        if ctx_json:
            ctx = json.loads(ctx_json)
            has_pipeline = 'decision_pipeline' in ctx
            print(f'  有decision_pipeline: {has_pipeline}')
            if has_pipeline:
                print('  ✅ 这是新的自动策略订单！')
                print(f'  手续费: {ctx.get("estimated_round_trip_cost_bps")}bps')
                print(f'  平均盈: {ctx.get("meta_label_average_win")}R')
else:
    print('  没有新订单')

# 检查scheduler状态
print('\n=== Scheduler状态 ===')
with open('logs/scheduler-state.json', 'r') as f:
    state = json.load(f)
    print(f'运行中: {state["running"]}')
    print(f'上次周期: {state["last_auto_cycle_at"]}')
    print(f'Top20覆盖: {state.get("top20_coverage_count", "N/A")}')
    print(f'数据新鲜: {state.get("data_fresh", "N/A")}')
    if state.get('scheduler_error'):
        err = state['scheduler_error']
        if len(err) > 200:
            err = err[:200] + '...'
        print(f'错误: {err}')
    else:
        print('错误: 无')

conn.close()
