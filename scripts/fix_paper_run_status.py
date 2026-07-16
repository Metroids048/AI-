import sqlite3

conn = sqlite3.connect('.local_paper_console.db')
cursor = conn.cursor()

cursor.execute('''
SELECT p.paper_run_id, s.strategy_key, p.paper_status
FROM paper_runs p
JOIN strategies s ON p.strategy_id = s.id
WHERE s.strategy_key = 'auto_paper_mature_templates'
''')

result = cursor.fetchone()
if result:
    run_id, key, status = result
    print(f'{key}: {status}')
    if status != 'running':
        print(f'设置为running...')
        cursor.execute('UPDATE paper_runs SET paper_status = ?WHERE paper_run_id = ?', ('running', run_id))
        conn.commit()
        print('✅ 已设置为running')
    else:
        print('✅ 状态已经是running')
else:
    print('❌ 未找到auto_paper_mature_templates')

conn.close()
