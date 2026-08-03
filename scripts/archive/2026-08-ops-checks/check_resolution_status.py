#!/usr/bin/env python3
"""检查风险事件的resolution_status状态"""

import sqlite3


def main():
    conn = sqlite3.connect(".local_paper_console.db")
    cursor = conn.cursor()

    # 1. 统计所有风险事件的resolution_status分布
    print("\n=== 1. 所有风险事件的resolution_status分布 ===")
    cursor.execute("""
        SELECT resolution_status, COUNT(*) as cnt
        FROM risk_events
        GROUP BY resolution_status
        ORDER BY cnt DESC
    """)
    for row in cursor.fetchall():
        print(f"  {row[0]}: {row[1]} 个")

    # 2. 检查是否有resolution_status为detected/acknowledged的事件
    print("\n=== 2. resolution_status为detected/acknowledged的活跃事件 ===")
    cursor.execute("""
        SELECT event_type, level, resolution_status, created_at, expires_at, affected_symbols
        FROM risk_events
        WHERE resolution_status IN ('detected', 'acknowledged')
          AND (expires_at IS NULL OR expires_at > datetime('now'))
        ORDER BY created_at DESC
        LIMIT 20
    """)
    rows = cursor.fetchall()
    print(f"共 {len(rows)} 个:")
    for row in rows:
        print(f"  {row[0]} | {row[1]} | 状态:{row[2]} | {row[3]} | 过期:{row[4]}")
        print(f"    影响币种: {row[5]}\n")

    # 3. 检查所有风险事件（不管resolution_status）
    print("\n=== 3. 所有未过期的风险事件（不管resolution_status） ===")
    cursor.execute("""
        SELECT COUNT(*) as cnt
        FROM risk_events
        WHERE expires_at IS NULL OR expires_at > datetime('now')
    """)
    total = cursor.fetchone()[0]
    print(f"总共: {total} 个")

    if total > 0:
        cursor.execute("""
            SELECT event_type, level, resolution_status, created_at, expires_at
            FROM risk_events
            WHERE expires_at IS NULL OR expires_at > datetime('now')
            ORDER BY created_at DESC
            LIMIT 10
        """)
        print("\n最近10个:")
        for row in cursor.fetchall():
            print(f"  {row[0]} | {row[1]} | 状态:{row[2]} | {row[3]} | 过期:{row[4]}")

    conn.close()


if __name__ == "__main__":
    main()
