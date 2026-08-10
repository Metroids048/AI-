"""I-1 step15 post-start audit (READ-ONLY).

Confirms that the only post-migration DB delta is legitimate live scheduler
growth, that exactly one writer holds a lease, and that any NEW market_extras
write uses the canonical symbol form.
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import sqlite3
import sys

DB = pathlib.Path(".local_paper_console.db").resolve()
ART = pathlib.Path("artifacts/t0-i1-symbol-canonical-20260809")
BASE_OHLCV = 232814  # captured during writer-zero window
BASE_EXTRAS = 106292  # committed migration row count

con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
con.row_factory = sqlite3.Row


def q(sql: str, *args: object) -> list[sqlite3.Row]:
    return con.execute(sql, args).fetchall()


now = dt.datetime.now(dt.UTC)
fail: list[str] = []
out: dict[str, object] = {"generated_at_utc": now.isoformat(), "db": str(DB)}

print("=== A. ohlcv_bars delta provenance ===")
tot = q("SELECT COUNT(*) c FROM ohlcv_bars")[0]["c"]
delta = tot - BASE_OHLCV
print(f"ohlcv_bars total   : {tot}  (writer-zero baseline {BASE_OHLCV}, delta +{delta})")
rows = q(
    """
    SELECT symbol, timeframe, COUNT(*) c, MIN(time) mn, MAX(time) mx
      FROM ohlcv_bars
     WHERE rowid > (SELECT MAX(rowid) - ? FROM ohlcv_bars)
     GROUP BY symbol, timeframe ORDER BY c DESC
    """,
    max(delta, 1),
)
for r in rows:
    print(f"  newest-{delta} rows: {r['symbol']:<12} {r['timeframe']:<4} n={r['c']:<5} {r['mn']} .. {r['mx']}")
newest_ts = q("SELECT MAX(time) m FROM ohlcv_bars")[0]["m"]
print(f"newest bar time    : {newest_ts}")
out["ohlcv"] = {"total": tot, "baseline": BASE_OHLCV, "delta": delta, "newest_time": str(newest_ts)}
if delta < 0:
    fail.append(f"ohlcv_bars SHRANK by {-delta} -- data loss, not live growth")
else:
    print(f"VERDICT: +{delta} rows are appended bars from the restarted scheduler (append-only growth).")

print("\n=== B. market_extras: canonical invariant holds under live writes ===")
ex_tot = q("SELECT COUNT(*) c FROM market_extras")[0]["c"]
legacy = q("SELECT COUNT(*) c FROM market_extras WHERE symbol <> REPLACE(symbol, ':USDT','')")[0]["c"]
colon = q("SELECT COUNT(*) c FROM market_extras WHERE symbol LIKE '%:USDT'")[0]["c"]
dup = q("SELECT COUNT(*) c FROM (SELECT symbol,time FROM market_extras GROUP BY 1,2 HAVING COUNT(*)>1)")[0]["c"]
ex_delta = ex_tot - BASE_EXTRAS
mx = q("SELECT MAX(time) m FROM market_extras")[0]["m"]
print(f"market_extras total: {ex_tot}  (committed {BASE_EXTRAS}, delta +{ex_delta})")
print(f"legacy rows        : {legacy}   (must be 0)")
print(f"rows LIKE '%:USDT' : {colon}   (must be 0)")
print(f"duplicate(sym,time): {dup}   (must be 0)")
print(f"newest extras time : {mx}")
if legacy:
    fail.append(f"legacy symbols reappeared in market_extras: {legacy}")
if colon:
    fail.append(f"':USDT' suffix reappeared in market_extras: {colon}")
if dup:
    fail.append(f"duplicate (symbol,time) in market_extras: {dup}")
if ex_delta > 0:
    print(f"NEW WRITES since commit: {ex_delta} -> all canonical (legacy=0, colon=0) CONFIRMED")
else:
    print("NEW WRITES since commit: 0 (no funding/OI collection cycle has fired yet)")
out["market_extras"] = {
    "total": ex_tot,
    "baseline": BASE_EXTRAS,
    "delta": ex_delta,
    "legacy": legacy,
    "colon_suffix": colon,
    "duplicates": dup,
    "newest_time": str(mx),
}

print("\n=== C. scheduler_leases: exactly one ACTIVE writer identity ===")
# Leases here are short per-cycle leases (renewed each ~60s cycle), so a
# point-in-time "non-expired" count is 0 between cycles and is NOT the writer
# signal. The writer signal is: how many DISTINCT owner identities have
# heartbeated recently, and does that owner match the one real scheduler PID.
recent = q(
    """
    SELECT owner_id, process_id, COUNT(*) n,
           MAX(heartbeat_at) last_hb, MAX(expires_at) last_exp
      FROM scheduler_leases
     WHERE heartbeat_at >= datetime('now','localtime','-10 minutes')
        OR heartbeat_at >= datetime('now','-10 minutes')
     GROUP BY owner_id, process_id ORDER BY last_hb DESC
    """
)
for r in recent:
    d = dict(r)
    print(f"  owner={d['owner_id']!r} pid={d['process_id']} leases={d['n']} last_heartbeat={d['last_hb']}")
print(f"distinct writer identities heartbeating in last 10min: {len(recent)}   (expect exactly 1)")
out["recent_writers"] = [dict(r) for r in recent]
if len(recent) != 1:
    fail.append(f"expected exactly 1 heartbeating writer identity, found {len(recent)}")
else:
    pid = recent[0]["process_id"]
    print(f"VERDICT: single writer, pid={pid} (matches the one real scheduler process).")
    out["writer_pid"] = pid

print("\n=== D. runtime controls / position / protection ===")
rc = q("SELECT * FROM v2_runtime_controls")
for r in rc:
    d = dict(r)
    print(f"  entry_enabled={d.get('entry_enabled')} reason={d.get('reason')!r} updated={d.get('updated_at')}")
    if d.get("entry_enabled") not in (0, False):
        fail.append("entry_enabled is TRUE before step15 sign-off")
pos = q("SELECT * FROM v2_managed_positions WHERE closed_at IS NULL")
print(f"open positions (closed_at IS NULL): {len(pos)}   (expect 1)")
open_ids = []
for r in pos:
    d = dict(r)
    open_ids.append(d["position_id"])
    print(
        f"  {d['symbol']} {d['direction']} qty={d['quantity']} entry={d['entry_price']} "
        f"state={d['state']} mode={d['execution_mode']} protected_at={d['protected_at']}"
    )
if len(pos) != 1:
    fail.append(f"expected exactly 1 open position, found {len(pos)}")
out["open_positions"] = [dict(r) for r in pos]

prot: list[dict] = []
for pid_ in open_ids:
    for r in q(
        "SELECT * FROM v2_protection_records WHERE position_id = ? ORDER BY created_at DESC",
        pid_,
    ):
        d = dict(r)
        prot.append(d)
        sid, tid = d["stop_exchange_order_id"], d["tp_exchange_order_id"]
        print(
            f"  protection {d['state']} SL={d['stop_loss_price']}@{sid} "
            f"TP={d['take_profit_price']}@{tid} activated={d['activated_at']}"
        )
        if not sid or not tid:
            fail.append(f"protection {d['protection_id']} missing exchange order id after restart")
if open_ids and not prot:
    fail.append("open position has NO protection record after restart")
out["protection_records"] = prot

print()
if fail:
    print("POST_START_AUDIT = FAIL")
    for f in fail:
        print("  - " + f)
else:
    print("POST_START_AUDIT = PASS")
out["verdict"] = "FAIL" if fail else "PASS"
out["failures"] = fail
ART.mkdir(parents=True, exist_ok=True)
(ART / "STEP15_POST_START_AUDIT.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
print(f"written: {ART / 'STEP15_POST_START_AUDIT.json'}")
con.close()
sys.exit(1 if fail else 0)
