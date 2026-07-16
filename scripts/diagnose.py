import sqlite3
import json

conn = sqlite3.connect('.local_paper_console.db')
cursor = conn.cursor()

print("=" * 70)
print("问题根因诊断")
print("=" * 70)

# 1. 检查strategies表配置
print("\n【1】检查strategies表中的配置")
cursor.execute("SELECT id, strategy_key, entry_rules, position_rules FROM strategies")
strategies = cursor.fetchall()

print(f"找到 {len(strategies)} 个策略\n")

for strat_id, key, entry_json, pos_json in strategies[:3]:
    print(f"策略: {key}")

    if entry_json:
        entry = json.loads(entry_json)
        meta = entry.get('meta_label_min_win_rate', 'N/A')
        fee = entry.get('core_fee_bps', 'N/A')
        print(f"  MetaLabel阈值: {meta} (预期: 0.42)")
        print(f"  手续费: {fee}bps (预期: 5)")

        if meta == 0.42 and fee == 5.0:
            print("  ✅ entry_rules配置正确")
        else:
            print("  ❌ entry_rules配置错误")

    if pos_json:
        pos = json.loads(pos_json)
        risk = pos.get('risk_per_trade', 'N/A')
        portfolio = pos.get('max_portfolio_initial_risk_fraction', 'N/A')
        leverage = pos.get('max_leverage', 'N/A')
        print(f"  单笔风险: {risk} (预期: 0.05)")
        print(f"  组合风险: {portfolio} (预期: 0.25)")
        print(f"  最大杠杆: {leverage} (预期: 40)")

        if portfolio == 0.25 and risk == 0.05:
            print("  ✅ position_rules配置正确")
        else:
            print("  ❌ position_rules配置错误")

    print()

# 2. 核心问题诊断
print("\n【2】核心问题诊断")
print("订单的entry_context中缺少decision_pipeline和meta_label配置")
print("这说明订单不是通过正常的自动策略流程生成的")
print("\n可能原因:")
print("1. bootstrap没有正确执行")
print("2. 运行的代码路径不是最新的decision_pipeline")
print("3. paper_runs使用了旧的execution_profile")

conn.close()
