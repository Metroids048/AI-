"""Diagnostic: inspect ohlcv_bars freshness and data_stale event distribution."""

import sqlite3

conn = sqlite3.connect(".local_paper_console.db")
cur = conn.cursor()

print("--- most recent 1m bar per symbol (top 25 freshest) ---")
cur.execute(
    "SELECT symbol, MAX(time), COUNT(*) FROM ohlcv_bars WHERE timeframe='1m' "
    "GROUP BY symbol ORDER BY MAX(time) DESC LIMIT 25"
)
for row in cur.fetchall():
    print(row)

print("--- data_stale event time range ---")
cur.execute("SELECT MIN(created_at), MAX(created_at), COUNT(*) FROM risk_events WHERE event_type='data_stale'")
print(cur.fetchone())

print("--- data_stale events per day ---")
cur.execute(
    "SELECT substr(created_at,1,10) AS d, COUNT(*) FROM risk_events "
    "WHERE event_type='data_stale' GROUP BY d ORDER BY d"
)
for row in cur.fetchall():
    print(row)

print("--- data_stale events per symbol (affected_symbols) ---")
cur.execute(
    "SELECT affected_symbols, COUNT(*) FROM risk_events WHERE event_type='data_stale' "
    "GROUP BY affected_symbols ORDER BY COUNT(*) DESC LIMIT 25"
)
for row in cur.fetchall():
    print(row)

print("--- resolution_status breakdown for data_stale ---")
cur.execute("SELECT resolution_status, COUNT(*) FROM risk_events WHERE event_type='data_stale' GROUP BY resolution_status")
for row in cur.fetchall():
    print(row)

print("--- last 20 data_stale events (raw) ---")
cur.execute(
    "SELECT id, created_at, affected_symbols, resolution_status FROM risk_events "
    "WHERE event_type='data_stale' ORDER BY created_at DESC LIMIT 20"
)
for row in cur.fetchall():
    print(row)

conn.close()
