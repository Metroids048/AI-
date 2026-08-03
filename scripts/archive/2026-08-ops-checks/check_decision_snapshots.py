#!/usr/bin/env python3
"""检查最近的决策快照详情"""

import json
import sqlite3


def main():
    conn = sqlite3.connect(".local_paper_console.db")
    cursor = conn.cursor()

    # 查看最近的决策快照
    print("\n=== 最近20个决策快照 ===\n")
    cursor.execute("""
        SELECT decision_snapshot_id, paper_run_id, symbol, created_at,
               action, pipeline_status, reason, decision_trace
        FROM decision_snapshots
        WHERE created_at > datetime('now', '-1 hour')
        ORDER BY created_at DESC
        LIMIT 20
    """)

    rows = cursor.fetchall()
    print(f"共 {len(rows)} 条\n")

    for i, row in enumerate(rows, 1):
        print(f"{i}. ID: {row[0][:8]}... | Run: {row[1][:8] if row[1] else 'NULL'}... | {row[2]} | {row[3]}")
        print(f"   动作: {row[4]}")
        print(f"   管道状态: {row[5]}")
        print(f"   原因: {row[6]}")

        # 解析decision_trace
        if row[7]:
            try:
                trace = json.loads(row[7]) if isinstance(row[7], str) else row[7]
                if trace:
                    print(f"   跟踪: {json.dumps(trace, indent=4)[:200]}...")
            except:
                pass
        print()

    # 统计pipeline_status
    print("\n=== 最近1小时decision_snapshots的pipeline_status分布 ===")
    cursor.execute("""
        SELECT pipeline_status, COUNT(*) as cnt
        FROM decision_snapshots
        WHERE created_at > datetime('now', '-1 hour')
        GROUP BY pipeline_status
        ORDER BY cnt DESC
    """)
    for row in cursor.fetchall():
        print(f"  {row[0]}: {row[1]} 条")

    # 统计action
    print("\n=== 最近1小时decision_snapshots的action分布 ===")
    cursor.execute("""
        SELECT action, COUNT(*) as cnt
        FROM decision_snapshots
        WHERE created_at > datetime('now', '-1 hour')
        GROUP BY action
        ORDER BY cnt DESC
    """)
    for row in cursor.fetchall():
        print(f"  {row[0]}: {row[1]} 条")

    # 检查对应的PaperRun状态
    print("\n=== 对应的PaperRun状态 ===")
    cursor.execute("""
        SELECT DISTINCT pr.paper_run_id, pr.strategy_id, pr.paper_status, pr.symbol_scope,
               pr.execution_profile
        FROM decision_snapshots ds
        JOIN paper_runs pr ON ds.paper_run_id = pr.paper_run_id
        WHERE ds.created_at > datetime('now', '-1 hour')
    """)
    runs = cursor.fetchall()
    if runs:
        for row in runs:
            print(f"\nRun ID: {row[0][:12]}...")
            print(f"  策略: {row[1]}")
            print(f"  状态: {row[2]}")
            print(f"  币种范围: {row[3]}")
            print(f"  执行配置: {row[4]}")
    else:
        print("  没有找到对应的PaperRun")

    conn.close()


if __name__ == "__main__":
    main()
