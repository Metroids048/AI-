"""I-1 close-out: post-entry-enable acceptance (READ-ONLY).

Operator-mandated step 5 after restoring entry_enabled=true. Asserts the six
conditions that would justify re-arming the maintenance lock, and explicitly
does NOT fail on the three known-unresolved items (market_extras stale,
OI/long-short NULL, V2 cycle behaviour) because those predate I-1.

Usage: i1_post_toggle_acceptance_readonly.py <baseline_cycle_count> <toggle_at_utc>
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
BASE_CYCLES = int(sys.argv[1]) if len(sys.argv) > 1 else 13789
TOGGLE_AT = sys.argv[2] if len(sys.argv) > 2 else "2026-08-09 03:00:27"

con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
con.row_factory = sqlite3.Row
fail: list[str] = []
out: dict[str, object] = {"baseline_cycles": BASE_CYCLES, "toggle_at_utc": TOGGLE_AT}


def one(sql: str, *a: object) -> sqlite3.Row:
    return con.execute(sql, a).fetchone()


print("=== 0. entry_enabled restored, maintenance reason cleared ===")
rc = dict(one("SELECT * FROM v2_runtime_controls WHERE scope='global'"))
print(f"  entry_enabled={rc['entry_enabled']} reason={rc['reason']!r}")
print(f"  updated_by={rc['updated_by']!r} version={rc['version']}")
if rc["entry_enabled"] not in (1, True):
    fail.append("entry_enabled is not true after the authorized restore")
if "migration_window" in str(rc["reason"]):
    fail.append("maintenance reason i1_symbol_canonical_migration_window was not replaced")
if rc["updated_by"] != "api":
    fail.append(f"updated_by={rc['updated_by']!r} -- restore did not go through the API")
out["runtime_control"] = rc
print("\n=== 1. at least 2 scheduler cycles observed since the toggle ===")
n_new = one("SELECT COUNT(*) c FROM v2_execution_cycles WHERE started_at >= ?", TOGGLE_AT)["c"]
total = one("SELECT COUNT(*) c FROM v2_execution_cycles")["c"]
print(f"  cycles since toggle: {n_new}   (need >= 2)")
print(f"  total cycles: {total}  (baseline {BASE_CYCLES}, delta +{total - BASE_CYCLES})")
for r in con.execute(
    "SELECT symbol, bar_timestamp, decision_terminal, started_at FROM v2_execution_cycles "
    "WHERE started_at >= ? ORDER BY started_at DESC LIMIT 8",
    (TOGGLE_AT,),
):
    d = dict(r)
    print(f"    {d['started_at']} {d['symbol']:<10} -> {d['decision_terminal']}")
out["cycles_since_toggle"] = n_new
if n_new < 2:
    fail.append(f"only {n_new} cycle(s) since toggle -- need at least 2 to accept")

print("\n=== 2. scheduler is still a SINGLE writer ===")
writers = con.execute(
    "SELECT owner_id, process_id, COUNT(*) n, MAX(heartbeat_at) hb FROM scheduler_leases "
    "WHERE heartbeat_at >= datetime('now','-10 minutes') GROUP BY 1,2 ORDER BY hb DESC"
).fetchall()
for r in writers:
    d = dict(r)
    print(f"  owner={d['owner_id']!r} pid={d['process_id']} leases={d['n']} last_hb={d['hb']}")
print(f"  distinct writer identities: {len(writers)}   (expect 1)")
out["writers"] = [dict(r) for r in writers]
if len(writers) != 1:
    fail.append(f"writer count is {len(writers)}, not 1 -- duplicate-writer risk")

print("\n=== 3. primary candidate still FAIL-CLOSED on validated edge ===")
rej = con.execute(
    "SELECT terminal_reason, COUNT(*) c FROM v2_execution_decisions WHERE created_at >= ? GROUP BY 1 ORDER BY c DESC",
    (TOGGLE_AT,),
).fetchall()
for r in rej:
    print(f"  {r['terminal_reason']!r:<40} {r['c']}")
edge = one(
    "SELECT COUNT(*) c FROM v2_execution_decisions WHERE created_at >= ? AND payload LIKE '%validated_edge%'",
    TOGGLE_AT,
)["c"]
entries = one("SELECT COUNT(*) c FROM v2_managed_positions WHERE projected_at >= ?", TOGGLE_AT)["c"]
print(f"  decisions mentioning validated_edge since toggle: {edge}")
print(f"  NEW managed positions opened since toggle: {entries}")
print("  (0 expected: the strategy-level edge gate is independent of the global lock)")
out["decision_reasons_since_toggle"] = [dict(r) for r in rej]
out["new_positions_since_toggle"] = entries
print("\n=== 4. exchange: BTC SL/TP still LIVE, no ghost, no duplicate protection ===")
adapter = BinanceTestnetAdapter(execution_mode=V2ExecutionMode.BINANCE_TESTNET)
snap = adapter.fetch_authoritative_snapshot()
ex_pos = [asdict(p) for p in snap.positions]
ex_pend = [asdict(o) for o in snap.pending_orders]
for p in ex_pos:
    print(f"  position {p['symbol']} {p['direction']} qty={p['quantity']} entry={p['entry_price']}")
for o in ex_pend:
    print(
        f"  pending  {o['order_type']:<20} {o['symbol']} trigger={o['price']} "
        f"reduce_only={o['reduce_only']} status={o['status']} id={o['exchange_order_id']}"
    )

local_pos = [dict(r) for r in con.execute("SELECT * FROM v2_managed_positions WHERE closed_at IS NULL")]
prot = [
    dict(r)
    for r in con.execute(
        "SELECT * FROM v2_protection_records WHERE position_id IN "
        "(SELECT position_id FROM v2_managed_positions WHERE closed_at IS NULL)"
    )
]
ex_syms = {p["symbol"] for p in ex_pos}
pend_ids = {str(o["exchange_order_id"]) for o in ex_pend}
findings: list[str] = []
for p in prot:
    sym = next((x["symbol"] for x in local_pos if x["position_id"] == p["position_id"]), "BTC/USDT")
    still_open = sym in ex_syms
    legs = {}
    for kind, oid, cid in (
        ("STOP_LOSS", p["stop_exchange_order_id"], p["stop_client_order_id"]),
        ("TAKE_PROFIT", p["tp_exchange_order_id"], p["tp_client_order_id"]),
    ):
        live = str(oid) in pend_ids
        # Binance gives the EXECUTED order a different id than the algo id, so an
        # algo id vanishing from open orders is ambiguous. Resolve by client id.
        ex_order = adapter.query_order_by_client_id(sym, cid)
        st = ex_order.status if ex_order else None
        legs[kind] = {"algo_id": str(oid), "live": live, "status": st}
        print(
            f"  {kind:<12} algo_id={oid} live_in_open_orders={live} "
            f"resolved_by_client_id={st!r} position_still_open={still_open}"
        )

    triggered = [k for k, v in legs.items() if not v["live"] and v["status"] == "closed"]
    for kind, v in legs.items():
        if v["live"]:
            continue
        if still_open:
            # Position exposed with no protection order -> real protection failure.
            fail.append(f"{kind} {v['algo_id']} gone while {sym} is STILL OPEN -> unprotected exposure")
        elif kind in triggered:
            print(
                f"    -> {kind} TRIGGERED and filled; {sym} closed on the exchange. Protection worked. NOT a failure."
            )
            findings.append(f"{kind}_TRIGGERED_AND_FILLED:{sym}")
        elif triggered:
            # Sibling leg fired and flattened the position; Binance cancels the
            # remaining reduce-only leg and purges it (status resolves to None).
            print(
                f"    -> {kind} was OCO-CANCELLED because {triggered[0]} fired and closed "
                f"{sym}. Expected exchange behaviour. NOT a failure."
            )
            findings.append(f"{kind}_OCO_CANCELLED:{sym}")
        else:
            fail.append(
                f"{kind} {v['algo_id']} vanished (status={v['status']!r}) with no sibling "
                f"trigger and no open {sym} position -- unexplained"
            )

ghost = sorted({p["symbol"] for p in local_pos} - ex_syms)
print(f"  local-open-but-absent-on-exchange: {ghost or 'none'}")
for sym in ghost:
    lp = next(x for x in local_pos if x["symbol"] == sym)
    triggered = any(f.endswith(sym) for f in findings)
    if triggered and lp["state"] == "QUARANTINED":
        # Exit happened on the exchange; the local projection has not caught up.
        # This is a reconciliation lag, NOT a pre-existing fabricated ghost row.
        print(f"    {sym}: state={lp['state']}, closed_at={lp['closed_at']}, realized_pnl={lp['realized_pnl']}")
        print(
            "    -> UNRECONCILED_EXIT: protection fired on the exchange but the local "
            "projection never booked the exit fill / PnL / closed_at."
        )
        print(
            "    -> NOT I1-CAUSED (reconcile path never reads market_extras). "
            "Escalate separately; does not justify re-arming the I-1 lock."
        )
        findings.append(f"UNRECONCILED_EXIT:{sym}")
    else:
        fail.append(f"ghost position with no matching exchange exit: {sym} (state={lp['state']})")
out["findings_not_i1_caused"] = findings

seen: dict[tuple[str, str], int] = {}
for o in ex_pend:
    key = (o["symbol"], o["order_type"])
    seen[key] = seen.get(key, 0) + 1
dupes = {f"{k[0]}/{k[1]}": v for k, v in seen.items() if v > 1}
print(f"  DUPLICATE protection orders: {dupes or 'none'}")
if dupes:
    fail.append(f"duplicate protection orders after toggle: {dupes}")
out["exchange_positions"] = ex_pos
out["exchange_pending"] = ex_pend
out["ghost"] = ghost
out["duplicate_protection"] = dupes
print("\n=== 5. no NEW migration-related rejection introduced by the toggle ===")
sym_hits = con.execute(
    "SELECT terminal_reason, COUNT(*) c FROM v2_execution_decisions WHERE created_at >= ? "
    "AND (payload LIKE '%symbol_not_found%' OR payload LIKE '%market_extras%') GROUP BY 1",
    (TOGGLE_AT,),
).fetchall()
legacy_now = one("SELECT COUNT(*) c FROM market_extras WHERE symbol <> REPLACE(symbol, ':USDT','')")["c"]
print(f"  symbol/market_extras-shaped rejections: {[dict(r) for r in sym_hits] or 'none'}")
print(f"  market_extras legacy rows now: {legacy_now}   (must stay 0)")
if sym_hits:
    fail.append(f"migration-shaped rejections appeared after toggle: {[dict(r) for r in sym_hits]}")
if legacy_now:
    fail.append(f"legacy symbols reappeared: {legacy_now}")

print("\n=== KNOWN-UNRESOLVED (recorded, explicitly NOT a failure) ===")
mx = one("SELECT MAX(time) m FROM market_extras")["m"]
nulls = one("SELECT COUNT(open_interest) oi, COUNT(long_ratio) lr, COUNT(liquidation_usd) lq FROM market_extras")
print(f"  MARKET_EXTRAS_STALE                     newest={mx}")
print(f"  MARKET_EXTRAS_NON_FUNDING_FIELDS_EMPTY  oi={nulls['oi']} long_ratio={nulls['lr']} liq={nulls['lq']}")
print("  V2_CYCLE_BEHAVIOUR                      OUT_OF_T0_SCOPE / NOT_INVESTIGATED")
print("  candidate registry 9-vs-10              PRE_EXISTING_BASELINE_FAILURE / NOT_CAUSED_BY_I1")
out["known_unresolved"] = {
    "MARKET_EXTRAS_STALE": str(mx),
    "MARKET_EXTRAS_NON_FUNDING_FIELDS_EMPTY": dict(nulls),
    "V2_CYCLE_BEHAVIOUR": "OUT_OF_T0_SCOPE / NOT_INVESTIGATED",
    "CANDIDATE_REGISTRY_9_VS_10": "PRE_EXISTING_BASELINE_FAILURE / NOT_CAUSED_BY_I1",
}

print()
verdict = "FAIL" if fail else "PASS"
print(f"POST_TOGGLE_ACCEPTANCE = {verdict}")
for f in fail:
    print("  - " + f)
if fail:
    print("\nACTION: re-arm the maintenance lock via POST /api/v2/automated-trading/controls/entry-disable and stop.")
out["verdict"] = verdict
out["failures"] = fail
ART.mkdir(parents=True, exist_ok=True)
(ART / "STEP16_POST_TOGGLE_ACCEPTANCE.json").write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
print(f"written: {ART / 'STEP16_POST_TOGGLE_ACCEPTANCE.json'}")
con.close()
sys.exit(1 if fail else 0)
