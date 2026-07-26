#!/usr/bin/env python3
"""检查这个卡住的风险事件的详细信息"""

import sqlite3
from datetime import datetime


def main():
    conn = sqlite3.connect(".local_paper_console.db")
    cursor = conn.cursor()

    print("\n" + "=" * 80)
    print("检查卡住的风险事件")
    print("=" * 80)

    cursor.execute("""
        SELECT id, created_at, expires_at, resolution_status, description
        FROM risk_events
        WHERE created_at = '2026-07-25 07:31:52.381197'
    """)

    row = cursor.fetchone()
    if row:
        print(f"\nID: {row[0]}")
        print(f"创建时间: {row[1]}")
        print(f"过期时间: {row[2]}")
        print(f"状态: {row[3]}")
        print(f"描述: {row[4]}")

        now = datetime.now()
        print(f"\n当前时间: {now}")

        if row[2] is None:
            print("\n❌ 确认BUG：expires_at仍然是None！")
            print("说明这个事件是在代码修复生效之前创建的（07:31时Celery worker可能还没重启）")
        else:
            print(f"\n过期时间已设置: {row[2]}")

    # 检查这之后是否还有新事件（验证去重和24h过期是否生效）
    print("\n" + "=" * 80)
    print("检查07:31之后是否有新的risk_limit_breach事件")
    print("=" * 80)

    cursor.execute("""
        SELECT COUNT(*) as cnt
        FROM risk_events
        WHERE event_type = 'risk_limit_breach'
          AND created_at > '2026-07-25 07:31:52.381197'
    """)

    new_count = cursor.fetchone()[0]
    print(f"\n07:31之后新创建的risk_limit_breach事件: {new_count} 个")

    if new_count == 0:
        print("✅ 去重逻辑生效！65个周期都没有创建新的重复事件")
    else:
        print("⚠️ 仍有新事件被创建")

    conn.close()


if __name__ == "__main__":
    main()
