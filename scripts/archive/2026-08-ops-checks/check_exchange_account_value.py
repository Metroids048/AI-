#!/usr/bin/env python3
"""检查手动持仓记录里实际存储的exchange_account值"""

import sqlite3


def main():
    conn = sqlite3.connect(".local_paper_console.db")
    cursor = conn.cursor()

    cursor.execute("""
        SELECT position_record_id, symbol, position_side, exchange_account, management_status
        FROM position_records
        WHERE management_status = 'UNMANAGED_EXTERNAL_POSITION'
    """)

    rows = cursor.fetchall()
    print("\n=== 手动/未托管持仓记录的exchange_account字段 ===\n")
    for row in rows:
        print(f"symbol: {row[1]:15s} | side: {row[2]:6s} | exchange_account: {row[3]!r}")

    conn.close()


if __name__ == "__main__":
    main()
