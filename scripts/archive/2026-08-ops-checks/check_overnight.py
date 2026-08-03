#!/usr/bin/env python3
"""全面检查一整晚的运行情况"""

import json
import sqlite3

conn = sqlite3.connect(".local_paper_console.db")
cursor = conn.cursor()

RUN_ID = "35298c65-cdbe-4bee-bee3-b7ded07c3204"
SINCE = "2026-07-25 19:03:00"  # 重启后的时间点

print("=" * 70)
print("一整晚运行情况全面检查")
print(f"检查范围: {SINCE} 至今")
print("=" * 70)

# 1. 调度周期统计
cursor.execute(
    """
    SELECT COUNT(*) as total,
           SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) as completed,
           SUM(CASE WHEN status='claimed' THEN 1 ELSE 0 END) as stuck,
           AVG(CASE WHEN status='completed' AND completed_at IS NOT NULL
               THEN CAST((julianday(completed_at) - julianday(started_at)) * 86400 AS INTEGER)
               ELSE NULL END) as avg_duration_s
    FROM scheduler_cycles
    WHERE started_at > ?
""",
    (SINCE,),
)
row = cursor.fetchone()
print(f"\n【调度周期】共{row[0]}次 | 成功:{row[1]} | 卡住:{row[2]} | 平均耗时:{int(row[3] or 0)}秒")

# 2. 策略当前状态
cursor.execute("SELECT paper_status, paper_metrics_summary FROM paper_runs WHERE paper_run_id=?", (RUN_ID,))
run = cursor.fetchone()
if run:
    metrics = json.loads(run[1]) if run[1] else {}
    acct_eq = metrics.get("account_equity", 0)
    strategy_peak = metrics.get("strategy_equity_peak", metrics.get("equity_peak", acct_eq))
    dd = (strategy_peak - acct_eq) / strategy_peak * 100 if strategy_peak > 0 else 0
    print(f"\n【策略状态】{run[0]}")
    print(f"  account_equity: {acct_eq:.2f}")
    print(f"  strategy_equity_peak: {strategy_peak:.2f}")
    print(f"  strategy_drawdown: {dd:.2f}%  {'✅ 安全' if dd < 20 else '⚠️ 危险'}")
    print(f"  最后调度: {metrics.get('last_cycle_at', 'N/A')}")
    if run[0] == "locked":
        print("  ❌ 策略已被hard_drawdown锁定！")
    elif run[0] == "running":
        print("  ✅ 策略运行正常")

# 3. 活跃风险事件
cursor.execute("""
    SELECT COUNT(*) FROM risk_events
    WHERE resolution_status IN ('detected', 'acknowledged')
      AND (expires_at IS NULL OR expires_at > datetime('now'))
""")
risk_count = cursor.fetchone()[0]
print(f"\n【风险事件】活跃: {risk_count} 个  {'✅' if risk_count == 0 else '❌'}")

# 4. 订单执行情况（重启后）
cursor.execute(
    """
    SELECT execution_status, COUNT(*) as cnt
    FROM order_executions
    WHERE paper_run_id = ?
      AND created_at > ?
    GROUP BY execution_status
    ORDER BY cnt DESC
""",
    (RUN_ID, SINCE),
)
print("\n【订单统计】（重启后）")
order_stats = cursor.fetchall()
total_orders = sum(r[1] for r in order_stats)
if order_stats:
    for row in order_stats:
        print(f"  {row[0]}: {row[1]} 条")
else:
    print("  无订单")

# 5. 真实网关订单（最关键）
cursor.execute(
    """
    SELECT created_at, symbol, direction, execution_status, gateway_order_id, evaluated_risk_state
    FROM order_executions
    WHERE paper_run_id = ?
      AND created_at > ?
      AND gateway_order_id IS NOT NULL
    ORDER BY created_at DESC
    LIMIT 10
""",
    (RUN_ID, SINCE),
)
gateway_orders = cursor.fetchall()
print(f"\n【网关订单】（重启后提交到Binance的真实订单: {len(gateway_orders)} 条）")
if gateway_orders:
    for row in gateway_orders:
        icon = "✅" if row[3] == "filled" else "⏳"
        print(f"  {icon} {row[0]} | {row[1]} {row[2]} | {row[3]} | ID:{row[4]}")
        # 验证drawdown数值
        if row[5]:
            try:
                rs = json.loads(row[5])
                eq = rs.get("account_equity")
                peak = rs.get("equity_peak")
                if eq and peak and peak > 0:
                    dd_pct = (peak - eq) / peak * 100
                    print(
                        f"       equity={eq:.2f} peak={peak:.2f} drawdown={dd_pct:.2f}%  {'✅ 已排除手动持仓' if dd_pct < 20 else '⚠️ 检查drawdown'}"
                    )
            except Exception:
                pass
else:
    print("  无网关订单（可能无信号）")

# 6. 拒绝原因分布
cursor.execute(
    """
    SELECT rejection_reason, COUNT(*) as cnt
    FROM order_executions
    WHERE paper_run_id = ?
      AND created_at > ?
      AND execution_status = 'rejected'
    GROUP BY rejection_reason
    ORDER BY cnt DESC
    LIMIT 10
""",
    (RUN_ID, SINCE),
)
rejections = cursor.fetchall()
if rejections:
    print("\n【拒绝原因】")
    for row in rejections:
        print(f"  {row[1]}x {(row[0] or 'None')[:80]}")

# 7. 决策漏斗（新的周期中信号有多少）
cursor.execute(
    """
    SELECT action, COUNT(*) as cnt
    FROM decision_snapshots
    WHERE created_at > ?
    GROUP BY action
    ORDER BY cnt DESC
    LIMIT 8
""",
    (SINCE,),
)
decisions = cursor.fetchall()
if decisions:
    print("\n【决策漏斗】")
    for row in decisions:
        print(f"  {row[0]}: {row[1]}")

print("\n" + "=" * 70)

# 最终结论
has_normal_cycles = (row[1] if row else 0) > 5  # completed > 5
no_risk_events = risk_count == 0
strategy_running = run and run[0] == "running"
drawdown_safe = dd < 20

issues = []
if not has_normal_cycles:
    issues.append("调度周期不足")
if not no_risk_events:
    issues.append(f"有{risk_count}个活跃风险事件")
if not strategy_running:
    issues.append(f"策略状态异常: {run[0] if run else 'N/A'}")
if not drawdown_safe:
    issues.append(f"回撤{dd:.1f}%接近危险阈值20%")

if not issues:
    print("✅ 系统运行正常，可以固化链路")
else:
    print("⚠️  发现问题：")
    for issue in issues:
        print(f"  - {issue}")

conn.close()
