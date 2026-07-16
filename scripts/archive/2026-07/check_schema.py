import sqlite3

conn = sqlite3.connect('.local_paper_console.db')
cursor = conn.cursor()

print("=== 所有表 ===")
cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
tables = [row[0] for row in cursor.fetchall()]
print(f"表: {tables}\n")

print("=== strategies表结构 ===")
cursor.execute("PRAGMA table_info(strategies)")
columns = cursor.fetchall()
for col in columns:
    print(f"  {col[1]} ({col[2]})")

print("\n=== strategy_library表结构 ===")
cursor.execute("PRAGMA table_info(strategy_library)")
columns = cursor.fetchall()
for col in columns:
    print(f"  {col[1]} ({col[2]})")

print("\n=== 检查是否有策略数据 ===")
cursor.execute("SELECT COUNT(*) FROM strategies")
strat_count = cursor.fetchone()[0]
print(f"strategies表: {strat_count}条")

cursor.execute("SELECT COUNT(*) FROM strategy_library")
lib_count = cursor.fetchone()[0]
print(f"strategy_library表: {lib_count}条")

conn.close()
