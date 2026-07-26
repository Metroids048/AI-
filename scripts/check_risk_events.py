#!/usr/bin/env python3
"""检查活跃的风险事件"""

import sqlite3


def main():
    conn = sqlite3.connect(".local_paper_console.db")
    cursor = conn.cursor()

    cursor.execute("""
        SELECT event_type, level, created_at, expires_at, description, affected_symbols
        FROM risk_events
        WHERE expires_at IS NULL OR expires_at > datetime('now')
        ORDER BY created_at DESC
    """)

    rows = cursor.fetchall()

    print(f"\n=== 当前活跃的风险事件 (共{len(rows)}个) ===\n")

    if rows:
        for i, row in enumerate(rows, 1):
            print(f"{i}. {row[0]} | 级别:{row[1]} | 创建:{row[2]} | 过期:{row[3]}")
            print(f"   描述: {row[4]}")
            print(f"   影响币种: {row[5]}\n")
    else:
        print("✅ 没有活跃的风险事件\n")

    conn.close()


if __name__ == "__main__":
    main()
