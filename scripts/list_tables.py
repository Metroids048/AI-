#!/usr/bin/env python3
"""列出数据库中所有的表"""

import sqlite3


def main():
    conn = sqlite3.connect(".local_paper_console.db")
    cursor = conn.cursor()

    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    tables = [row[0] for row in cursor.fetchall()]

    print("\n=== 数据库中的所有表 ===\n")
    for t in tables:
        print(f"  {t}")

    conn.close()


if __name__ == "__main__":
    main()
