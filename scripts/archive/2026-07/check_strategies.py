import sqlite3
import json

conn = sqlite3.connect('.local_paper_console.db')
cursor = conn.cursor()

print("=== 检查strategies表中的配置 ===")
cursor.execute("""
    SELECT strategy_id, name, rules
    FROM strategies
    WHERE strategy_id IN (
        SELECT strategy_id FROM paper_runs WHERE paper_status = 'running'
    )
""")

strategies = cursor.fetchall()
print(f"找到 {len(strategies)} 个运行中的策略\n")

for strat_id, name, rules_json in strategies:
    print(f"策略: {name}")
    print(f"ID: {strat_id[:8]}...")

    if rules_json:
        rules = json.loads(rules_json)
        entry_rules = rules.get('entry_rules', {})
        pos_rules = rules.get('position_rules', {})

        meta_threshold = entry_rules.get('meta_label_min_win_rate', 'N/A')
        core_fee = entry_rules.get('core_fee_bps', 'N/A')
        risk_per = pos_rules.get('risk_per_trade', 'N/A')
        portfolio_risk = pos_rules.get('max_portfolio_initial_risk_fraction', 'N/A')
        leverage = pos_rules.get('max_leverage', 'N/A')

        print(f"  MetaLabel阈值: {meta_threshold} (预期: 0.42)")
        print(f"  手续费: {core_fee}bps (预期: 5)")
        print(f"  单笔风险: {risk_per} (预期: 0.05)")
        print(f"  组合风险: {portfolio_risk} (预期: 0.25)")
        print(f"  最大杠杆: {leverage} (预期: 40)")

        # 判断
        all_correct = (
            meta_threshold == 0.42 and
            core_fee == 5.0 and
            portfolio_risk == 0.25
        )

        if all_correct:
            print("  ✅ 配置正确")
        else:
            print("  ❌ 配置错误")
    else:
        print("  ❌ 没有rules配置")

    print()

conn.close()
