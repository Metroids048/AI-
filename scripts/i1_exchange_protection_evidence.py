"""I-1 step15: prove SL/TP survived the migration + restart ON THE EXCHANGE.

READ-ONLY against Binance Testnet: queries by client order id and reads the
authoritative snapshot. Creates no orders and cancels nothing.

Exchange is the source of truth; the local DB row is only a projection. This
script asserts the exchange agrees with the local protection record.
"""

from __future__ import annotations

import json
import pathlib
import sqlite3
import sys
from dataclasses import asdict

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from services.automated_trading.domain.enums import V2ExecutionMode  # noqa: E402
from services.automated_trading.infrastructure.binance_adapter import (  # noqa: E402
    BinanceTestnetAdapter,
)

DB = pathlib.Path(".local_paper_console.db").resolve()
ART = pathlib.Path("artifacts/t0-i1-symbol-canonical-20260809")
fail: list[str] = []

con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
con.row_factory = sqlite3.Row
local_pos = [dict(r) for r in con.execute("SELECT * FROM v2_managed_positions WHERE closed_at IS NULL")]
local_prot = [
    dict(r)
    for r in con.execute(
        "SELECT * FROM v2_protection_records WHERE position_id IN "
        "(SELECT position_id FROM v2_managed_positions WHERE closed_at IS NULL)"
    )
]
con.close()

print("=== local projection ===")
for p in local_pos:
    print(f"  position {p['symbol']} {p['direction']} qty={p['quantity']} entry={p['entry_price']} state={p['state']}")
for p in local_prot:
    print(
        f"  protection {p['state']} SL={p['stop_loss_price']}@{p['stop_exchange_order_id']} "
        f"TP={p['take_profit_price']}@{p['tp_exchange_order_id']}"
    )

adapter = BinanceTestnetAdapter(execution_mode=V2ExecutionMode.BINANCE_TESTNET)
snapshot = adapter.fetch_authoritative_snapshot()
ex_pos = [asdict(p) for p in snapshot.positions]
ex_pending = [asdict(o) for o in snapshot.pending_orders]

print("\n=== exchange truth (Binance Testnet) ===")
for p in ex_pos:
    print(
        f"  position {p['symbol']} {p['direction']} qty={p['quantity']} entry={p['entry_price']} "
        f"mark={p['mark_price']} upnl={p['unrealized_pnl']}"
    )
for o in ex_pending:
    print(
        f"  pending  {o['order_type']:<20} {o['symbol']} {o['side']} qty={o['quantity']} "
        f"trigger={o['price']} reduce_only={o['reduce_only']} status={o['status']} id={o['exchange_order_id']}"
    )

print("\n=== assertion: every local protection order is LIVE on the exchange ===")
pending_ids = {str(o["exchange_order_id"]) for o in ex_pending}
resolved = []
for p in local_prot:
    for kind, oid, cid in (
        ("STOP_LOSS", p["stop_exchange_order_id"], p["stop_client_order_id"]),
        ("TAKE_PROFIT", p["tp_exchange_order_id"], p["tp_client_order_id"]),
    ):
        in_pending = str(oid) in pending_ids
        order = adapter.query_order_by_client_id(
            next(x["symbol"] for x in local_pos if x["position_id"] == p["position_id"]), cid
        )
        st = order.status if order else None
        ok = in_pending and st == "new"
        print(
            f"  {kind:<12} id={oid} client={cid} in_open_orders={in_pending} status={st!r} -> "
            f"{'LIVE' if ok else 'NOT LIVE'}"
        )
        resolved.append(
            {
                "kind": kind,
                "exchange_order_id": str(oid),
                "client_order_id": cid,
                "in_open_orders": in_pending,
                "status": st,
                "live": ok,
            }
        )
        if not ok:
            fail.append(f"{kind} {oid} is NOT live on the exchange after migration+restart")

print("\n=== position reconciliation (local managed vs exchange) ===")
print(f"  local managed open positions : {len(local_pos)}")
print(f"  exchange open positions      : {len(ex_pos)}")
local_syms = {p["symbol"] for p in local_pos}
ex_syms = {p["symbol"] for p in ex_pos}
unmanaged = sorted(ex_syms - local_syms)
print(f"  on exchange but NOT locally managed: {unmanaged or 'none'}")
if unmanaged:
    print("  -> these are UNMANAGED_EXTERNAL_POSITION (manual). Runtime must quarantine, never adopt or close them.")
ghost = sorted(local_syms - ex_syms)
print(f"  locally managed but NOT on exchange (GHOST): {ghost or 'none'}")
if ghost:
    fail.append(f"GHOST positions -- local rows with no exchange position: {ghost}")

out = {
    "observed_at": str(snapshot.snapshot_timestamp),
    "local_positions": local_pos,
    "local_protections": local_prot,
    "exchange_positions": ex_pos,
    "exchange_pending_orders": ex_pending,
    "protection_orders_resolved": resolved,
    "unmanaged_external_symbols": unmanaged,
    "ghost_symbols": ghost,
}
print()
verdict = "FAIL" if fail else "PASS"
print(f"EXCHANGE_PROTECTION_EVIDENCE = {verdict}")
for f in fail:
    print("  - " + f)
out["verdict"] = verdict
out["failures"] = fail
ART.mkdir(parents=True, exist_ok=True)
(ART / "STEP15_EXCHANGE_PROTECTION_EVIDENCE.json").write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
print(f"written: {ART / 'STEP15_EXCHANGE_PROTECTION_EVIDENCE.json'}")
sys.exit(1 if fail else 0)
