#!/usr/bin/env python3
"""检查订单归属的run_id，避免把不同PaperRun的account_equity混在一起比较"""

import json
import sqlite3

conn = sqlite3.connect(".local_paper_console.db")
cursor = conn.cursor()

since = "2026-07-25 11:03:49"

cursor.execute(
    """
    SELECT order_execution_id, created_at, symbol, direction, execution_status,
           rejection_reason, gateway_order_id, evaluated_risk_state, paper_run_id
    FROM order_executions
    WHERE created_at > ?
    ORDER BY created_at ASC
""",
    (since,),
)

rows = cursor.fetchall()

# 拿两个run的execution_profile确认哪个是mirror_to_gateway=True的主策略
cursor.execute("SELECT paper_run_id, execution_profile FROM paper_runs")
run_labels = {}
for run_id, profile_raw in cursor.fetchall():
    try:
        profile = json.loads(profile_raw) if profile_raw else {}
        key = profile.get("auto_paper_runtime_key", "unknown")
        mirror = profile.get("mirror_to_gateway", False)
        run_labels[run_id] = f"{key} (mirror_to_gateway={mirror})"
    except Exception:
        run_labels[run_id] = "parse_error"

print("=== Run标签映射 ===")
for rid, label in run_labels.items():
    print(f"  {rid[:12]}... -> {label}")

print("\n=== 订单详情（含run归属） ===\n")
for row in rows:
    run_id = row[8]
    label = run_labels.get(run_id, "unknown_run")
    print(f"{row[1]} | {row[2]} {row[3]} | {row[4]} | run={run_id[:12] if run_id else 'None'}... ({label})")
    print(f"  reject={row[5]} | gw={row[6]}")
    if row[7]:
        try:
            rs = json.loads(row[7])
            eq = rs.get("account_equity")
            peak = rs.get("equity_peak")
            dd = None
            if eq is not None and peak and peak > 0:
                dd = round(max(0.0, (peak - eq) / peak) * 100, 2)
            print(f"  account_equity={eq} equity_peak={peak} implied_drawdown={dd}%")
        except Exception as e:
            print(f"  (parse failed: {e})")
    else:
        print("  (no evaluated_risk_state)")
    print()

conn.close()
