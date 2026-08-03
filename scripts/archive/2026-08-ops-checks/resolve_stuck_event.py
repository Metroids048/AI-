#!/usr/bin/env python3
"""解决单个卡住的风险事件"""

import sqlite3


def main():
    conn = sqlite3.connect(".local_paper_console.db")
    cursor = conn.cursor()

    event_id = "e91655fa-1520-4cb5-9796-29dae147abfd"

    # 将其标记为已过期
    cursor.execute(
        """
        UPDATE risk_events
        SET expires_at = datetime('now', '-1 minute')
        WHERE id = ?
    """,
        (event_id,),
    )

    conn.commit()
    print(f"✅ 已将事件 {event_id[:12]}... 标记为过期")

    # 验证
    cursor.execute("""
        SELECT COUNT(*) FROM risk_events
        WHERE resolution_status IN ('detected', 'acknowledged')
          AND (expires_at IS NULL OR expires_at > datetime('now'))
    """)
    remaining = cursor.fetchone()[0]
    print(f"✅ 剩余活跃风险事件: {remaining} 个")

    conn.close()


if __name__ == "__main__":
    main()
