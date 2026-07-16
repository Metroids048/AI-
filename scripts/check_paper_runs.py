import sqlite3

conn = sqlite3.connect('.local_paper_console.db')
cursor = conn.cursor()

print("=== Paper Runs详细信息 ===")
cursor.execute("PRAGMA table_info(paper_runs)")
columns = [row[1] for row in cursor.fetchall()]
print(f"Paper_runs列: {columns}\n")

cursor.execute("""
    SELECT paper_run_id, paper_status, strategy_id
    FROM paper_runs
    ORDER BY created_at DESC
    LIMIT 5
""")

for run_id, status, strat_id in cursor.fetchall():
    print(f"{run_id[:8]}... : {status}")

    # 查找对应的strategy
    cursor.execute("SELECT strategy_key FROM strategies WHERE id = ?", (strat_id,))
    result = cursor.fetchone()
    if result:
        print(f"  策略: {result[0]}")

print("\n=== 问题诊断 ===")
print("所有paper_runs都是locked/paused状态，没有running状态")
print("这就是为什么scheduler不生成订单")
print("\n需要将paper_runs状态改为running")

conn.close()
