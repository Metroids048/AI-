"""U1-B local truth snapshot (READ-ONLY).

Freezes what LOCAL V2 state currently believes about the BTC position whose
Binance-native stop loss filled at 2026-08-09 02:47:08.700 UTC, with full
identifier linkage: entry intent -> order record -> managed position ->
protection record -> exchange fills -> cycle/decision.

Writes nothing. Opens the DB in mode=ro.
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import sqlite3

DB = pathlib.Path(".local_paper_console.db").resolve()
ART = pathlib.Path("artifacts/t0-u1-unreconciled-exit-20260809")

EXIT_ORDER_ID = "28533281387"
ENTRY_ORDER_ID = "28531748285"
POSITION_ID = "10920a3c-6260-479c-8af6-6c410b303cfd"

con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
con.row_factory = sqlite3.Row


def q(sql: str, *args: object) -> list[dict[str, object]]:
    return [dict(r) for r in con.execute(sql, args).fetchall()]


def show(title: str, rows: list[dict[str, object]], keys: list[str] | None = None) -> None:
    print(f"\n--- {title}  (rows={len(rows)})")
    for r in rows:
        sel = {k: r.get(k) for k in keys} if keys else r
        print("   ", json.dumps(sel, default=str, ensure_ascii=False))


out: dict[str, object] = {
    "generated_at_utc": dt.datetime.now(dt.UTC).isoformat(),
    "db": str(DB),
    "exit_order_id": EXIT_ORDER_ID,
    "entry_order_id": ENTRY_ORDER_ID,
}

print("=" * 78)
print("U1-B LOCAL TRUTH SNAPSHOT (READ-ONLY)")
print("=" * 78)

# ---------------------------------------------------------------- 1. position
pos = q("SELECT * FROM v2_managed_positions WHERE position_id = ?", POSITION_ID)
out["managed_position"] = pos
show("v2_managed_positions (the BTC row)", pos)

open_btc = q(
    "SELECT position_id, symbol, direction, quantity, state, closed_at, realized_pnl "
    "FROM v2_managed_positions WHERE closed_at IS NULL"
)
out["all_open_positions"] = open_btc
show("ALL locally-open managed positions", open_btc)

# ------------------------------------------------------------ 2. order records
orders = q(
    "SELECT * FROM v2_exchange_orders WHERE exchange_order_id IN (?, ?) "
    "OR order_record_id = (SELECT order_record_id FROM v2_managed_positions "
    "WHERE position_id = ?) ORDER BY created_at",
    EXIT_ORDER_ID,
    ENTRY_ORDER_ID,
    POSITION_ID,
)
out["order_records"] = orders
show("v2_exchange_orders linked to this position / either order id", orders)

# -------------------------------------------------------------------- 3. fills
fills = q(
    "SELECT * FROM v2_exchange_fills WHERE exchange_order_id IN (?, ?) ORDER BY exchange_event_time",
    EXIT_ORDER_ID,
    ENTRY_ORDER_ID,
)
out["fills_for_both_orders"] = fills
show("v2_exchange_fills for entry+exit order ids", fills)

btc_fills = q(
    "SELECT fill_id, exchange_order_id, trade_id, symbol, side, reduce_only, "
    "filled_quantity, fill_price, commission, exchange_event_time "
    "FROM v2_exchange_fills "
    "WHERE REPLACE(symbol, ':USDT', '') = 'BTC/USDT' "
    "ORDER BY exchange_event_time DESC LIMIT 10"
)
out["recent_btc_fills"] = btc_fills
show("10 most recent BTC fills of ANY kind", btc_fills)

reduce_only = q(
    "SELECT COUNT(*) c FROM v2_exchange_fills WHERE REPLACE(symbol, ':USDT', '') = 'BTC/USDT' AND reduce_only = 1"
)
out["btc_reduce_only_fill_count"] = reduce_only[0]["c"]
print(f"\n    BTC reduce_only fill count in local DB = {reduce_only[0]['c']}")

# --------------------------------------------------------------- 4. protection
prot = q(
    "SELECT * FROM v2_protection_records WHERE position_id = ? ORDER BY created_at",
    POSITION_ID,
)
out["protection_records"] = prot
show("v2_protection_records for this position", prot)

live_prot = q(
    "SELECT protection_id, position_id, state, stop_client_order_id, tp_client_order_id, "
    "stop_exchange_order_id, tp_exchange_order_id "
    "FROM v2_protection_records WHERE state = 'PROTECTION_ACTIVE'"
)
out["all_active_protection"] = live_prot
show("ALL rows still claiming PROTECTION_ACTIVE", live_prot)

# ------------------------------------------------------------ 5. intent/cycle
intents = q(
    "SELECT * FROM v2_execution_intents WHERE intent_id = "
    "(SELECT intent_id FROM v2_managed_positions WHERE position_id = ?)",
    POSITION_ID,
)
out["execution_intents"] = intents
show("v2_execution_intents behind this position", intents)

# ---------------------------------------------- 6. what happened after 02:47
after = q(
    "SELECT cycle_id, symbol, execution_mode, decision_terminal, started_at, completed_at "
    "FROM v2_execution_cycles WHERE started_at >= '2026-08-09 02:47:00' "
    "ORDER BY started_at LIMIT 8"
)
out["cycles_right_after_exit"] = after
show("first 8 execution cycles that ran AFTER the stop filled", after)

recon = q(
    "SELECT snapshot_id, cycle_id, execution_mode, status, captured_at, discrepancies "
    "FROM v2_reconciliation_snapshots WHERE captured_at >= '2026-08-09 02:47:00' "
    "ORDER BY captured_at LIMIT 6"
)
out["recon_snapshots_after_exit"] = recon
show("first reconciliation snapshots after the exit", recon)

recon_states = q(
    "SELECT status, COUNT(*) c, MIN(captured_at) first, MAX(captured_at) last "
    "FROM v2_reconciliation_snapshots WHERE captured_at >= '2026-08-09 02:47:00' "
    "GROUP BY status ORDER BY c DESC"
)
out["recon_status_since_exit"] = recon_states
show("reconciliation status distribution since the exit", recon_states)

incidents = q(
    "SELECT incident_type, severity, COUNT(*) c, MIN(created_at) first, MAX(created_at) last "
    "FROM v2_execution_incidents WHERE created_at >= '2026-08-09 02:40:00' "
    "GROUP BY incident_type, severity ORDER BY c DESC LIMIT 12"
)
out["incidents_since_exit"] = incidents
show("incidents raised since the exit window", incidents)

decisions = q(
    "SELECT terminal_reason, COUNT(*) c, MIN(created_at) first, MAX(created_at) last "
    "FROM v2_execution_decisions WHERE created_at >= '2026-08-09 02:47:00' "
    "GROUP BY terminal_reason ORDER BY c DESC LIMIT 12"
)
out["decisions_since_exit"] = decisions
show("decision terminal reasons since the exit", decisions)

# ------------------------------------------------------------------ 7. verdict
print("\n" + "=" * 78)
print("U1-B FINDINGS")
print("=" * 78)
findings: list[str] = []

if pos:
    p = pos[0]
    if p.get("closed_at") is None:
        findings.append(f"LOCAL_STILL_OPEN: closed_at IS NULL, state={p.get('state')!r}")
    else:
        findings.append(f"LOCAL_CLOSED: closed_at={p.get('closed_at')}")
else:
    findings.append(f"POSITION_ROW_MISSING: {POSITION_ID}")

if out["btc_reduce_only_fill_count"] == 0:
    findings.append("NO_REDUCE_ONLY_BTC_FILL: the real exchange exit was never projected")

if any(r.get("state") == "PROTECTION_ACTIVE" for r in prot):
    findings.append("PROTECTION_STILL_ACTIVE: local believes a dead algo order still guards it")

for f in findings:
    print("   *", f)
out["findings"] = findings

ART.mkdir(parents=True, exist_ok=True)
dest = ART / "U1B_LOCAL_TRUTH_SNAPSHOT.json"
dest.write_text(json.dumps(out, indent=2, default=str, ensure_ascii=False), encoding="utf-8")
print(f"\nwrote {dest}")
con.close()
