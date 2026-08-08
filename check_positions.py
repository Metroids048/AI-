import sqlite3

conn = sqlite3.connect(".local_paper_console.db")
cursor = conn.cursor()

print("=== paper_runs 表结构 ===")
cursor.execute("PRAGMA table_info(paper_runs)")
columns = [row[1] for row in cursor.fetchall()]
print(f"字段: {', '.join(columns)}")
print()

print("=== 当前运行配置 ===")
cursor.execute('SELECT * FROM paper_runs WHERE paper_status = "running" LIMIT 1')
row = cursor.fetchone()
if row:
    for i, col in enumerate(columns):
        print(f"{col}: {row[i]}")
print()

print("=== 未关闭的持仓 (非 CLOSED) ===")
cursor.execute("""
    SELECT symbol, position_side, quantity, exchange_account,
           management_status, execution_mode, opened_at
    FROM position_records
    WHERE management_status != 'CLOSED'
    ORDER BY opened_at DESC
""")
positions = cursor.fetchall()
print(f"总数: {len(positions)}")
for row in positions:
    print(f"  {row[0]} {row[1]} qty={row[2]:.4f} account={row[3]} status={row[4]} mode={row[5]} time={row[6]}")

conn.close()
