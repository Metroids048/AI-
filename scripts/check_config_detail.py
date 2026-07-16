import sqlite3
import json

conn = sqlite3.connect('.local_paper_console.db')
cursor = conn.cursor()

print("="*60)
print("系统配置与运行状态详细检查")
print("="*60)

# 1. 检查成交订单
print("\n【1】成交订单统计")
cursor.execute("SELECT COUNT(*) FROM order_executions WHERE execution_status = 'filled'")
filled = cursor.fetchone()[0]
cursor.execute("SELECT COUNT(*) FROM order_executions WHERE execution_status = 'rejected'")
rejected = cursor.fetchone()[0]
print(f"成交: {filled}笔")
print(f"拒绝: {rejected}笔")

# 2. 检查最新成交订单的配置
if filled > 0:
    print("\n【2】最新成交订单配置")
    cursor.execute("""
        SELECT symbol, entry_context
        FROM order_executions
        WHERE execution_status = 'filled'
        ORDER BY created_at DESC
        LIMIT 1
    """)
    symbol, ctx_json = cursor.fetchone()
    print(f"币种: {symbol}")

    if ctx_json:
        ctx = json.loads(ctx_json)
        print(f"  手续费: {ctx.get('estimated_round_trip_cost_bps', 'N/A')}bps")
        print(f"  组合风险上限: {ctx.get('max_portfolio_initial_risk_fraction', 'N/A')}")
        print(f"  MetaLabel胜率: {ctx.get('meta_label_win_rate', 'N/A')}")
        print(f"  平均盈利: {ctx.get('meta_label_average_win', 'N/A')}R")
        print(f"  平均亏损: {ctx.get('meta_label_average_loss', 'N/A')}R")
        print(f"  净期望: {ctx.get('estimated_net_edge_after_cost', 'N/A')}R")

# 3. 检查Paper Run配置
print("\n【3】Paper Run配置（数据库中）")
cursor.execute("""
    SELECT paper_run_id, strategy_id, auto_trading_settings
    FROM paper_runs
    WHERE paper_status = 'running'
""")
runs = cursor.fetchall()

if runs:
    for run_id, strat_id, settings_json in runs:
        print(f"\nRun: {run_id}")
        print(f"策略: {strat_id}")

        if settings_json:
            settings = json.loads(settings_json)
            entry_rules = settings.get('entry_rules', {})
            pos_rules = settings.get('position_rules', {})

            meta_threshold = entry_rules.get('meta_label_min_win_rate', 'N/A')
            core_fee = entry_rules.get('core_fee_bps', 'N/A')
            std_fee = entry_rules.get('standard_fee_bps', 'N/A')
            risk_per = pos_rules.get('risk_per_trade', 'N/A')
            portfolio_risk = pos_rules.get('max_portfolio_initial_risk_fraction', 'N/A')
            leverage = pos_rules.get('max_leverage', 'N/A')

            print(f"  MetaLabel阈值: {meta_threshold} (预期: 0.42)")
            print(f"  手续费(core): {core_fee}bps (预期: 5bps)")
            print(f"  手续费(std): {std_fee}bps (预期: 5bps)")
            print(f"  单笔风险: {risk_per} (预期: 0.05)")
            print(f"  组合风险上限: {portfolio_risk} (预期: 0.25)")
            print(f"  最大杠杆: {leverage} (预期: 40)")

            # 判断配置是否正确
            print("\n  配置状态:")
            if meta_threshold == 0.42:
                print("    ✅ MetaLabel阈值正确")
            else:
                print(f"    ❌ MetaLabel阈值错误: {meta_threshold}")

            if core_fee == 5.0:
                print("    ✅ 手续费正确")
            else:
                print(f"    ❌ 手续费错误: {core_fee}bps")

            if portfolio_risk == 0.25:
                print("    ✅ 组合风险上限正确")
            else:
                print(f"    ❌ 组合风险上限错误: {portfolio_risk}")
else:
    print("没有找到运行中的Paper Run")

# 4. 检查扫描的币种
print("\n【4】最近扫描的币种")
cursor.execute("""
    SELECT DISTINCT symbol
    FROM order_executions
    WHERE created_at >= datetime('now', '-30 minutes')
    ORDER BY symbol
""")
symbols = [row[0] for row in cursor.fetchall()]
print(f"扫描币种: {symbols}")
print(f"币种数量: {len(symbols)}")

conn.close()

print("\n" + "="*60)
print("检查完成")
print("="*60)
