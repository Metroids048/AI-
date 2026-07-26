#!/usr/bin/env python3
"""检查最近创建的风险事件"""

import sqlite3


def main():
    conn = sqlite3.connect(".local_paper_console.db")
    cursor = conn.cursor()

    cursor.execute("""
        SELECT event_type, level, created_at, expires_at, description
        FROM risk_events
        WHERE created_at > datetime('now', '-1 hour')
        ORDER BY created_at DESC
        LIMIT 20
    """)

    rows = cursor.fetchall()

    print(f"\n=== 最近1小时新创建的风险事件 (共{len(rows)}个) ===\n")

    if rows:
        for row in rows:
            print(f"{row[0]} | {row[1]} | {row[2]} | 过期:{row[3]}")
            print(f"  {row[4]}\n")
    else:
        print("✅ 没有新的风险事件\n")

    conn.close()


if __name__ == "__main__":
    main()
