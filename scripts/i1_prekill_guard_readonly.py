"""I-1 pre-kill guard. READ-ONLY (mode=ro). Exit 0 = safe to stop writers.

Operator-frozen condition: if entry is enabled, or the BTC position is missing, or
either exchange-native SL/TP is absent, DO NOT stop the scheduler. Exit code is the
gate so the result cannot be misread from prose.
"""

from __future__ import annotations

import sqlite3
import sys

DB = ".local_paper_console.db"
EXPECT_REASON = "i1_symbol_canonical_migration_window"


def main() -> int:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        ctrl = con.execute(
            "SELECT entry_enabled, reason, version FROM v2_runtime_controls WHERE scope='global'"
        ).fetchone()
        pos = list(
            con.execute(
                "SELECT position_id, symbol, direction, quantity, entry_price, state "
                "FROM v2_managed_positions WHERE state NOT IN ('CLOSED','QUARANTINED')"
            )
        )
        prot = list(
            con.execute(
                "SELECT position_id, stop_loss_price, stop_exchange_order_id, "
                "take_profit_price, tp_exchange_order_id, state "
                "FROM v2_protection_records WHERE state='PROTECTION_ACTIVE'"
            )
        )
    finally:
        con.close()

    fails: list[str] = []

    if not ctrl:
        fails.append("v2_runtime_controls global row missing")
    else:
        print(f"entry_enabled = {ctrl[0]}  reason = {ctrl[1]}  version = {ctrl[2]}")
        if ctrl[0] != 0:
            fails.append(f"entry_enabled must be 0, got {ctrl[0]}")
        if ctrl[1] != EXPECT_REASON:
            fails.append(f"reason must be {EXPECT_REASON}, got {ctrl[1]}")

    print(f"open positions = {len(pos)}")
    for p in pos:
        print(f"  {p[1]} {p[2]} qty={p[3]} entry={p[4]} state={p[5]} id={p[0][:8]}")
    btc = [p for p in pos if p[1] == "BTC/USDT" and p[5] == "PROTECTED"]
    if not btc:
        fails.append("no PROTECTED BTC/USDT position found")

    print(f"active protections = {len(prot)}")
    for r in prot:
        print(f"  pos={r[0][:8]} SL={r[1]}@{r[2]} TP={r[3]}@{r[4]} {r[5]}")

    for p in btc:
        rec = [r for r in prot if r[0] == p[0]]
        if not rec:
            fails.append(f"position {p[0][:8]} has no PROTECTION_ACTIVE record")
            continue
        r = rec[0]
        if r[1] is None or not r[2]:
            fails.append(f"position {p[0][:8]} missing exchange-native STOP LOSS")
        if r[3] is None or not r[4]:
            fails.append(f"position {p[0][:8]} missing exchange-native TAKE PROFIT")

    print()
    if fails:
        print("PREKILL_GUARD = FAIL  -> DO NOT STOP SCHEDULER; abort I-1 window")
        for f in fails:
            print(f"  - {f}")
        return 1
    print("PREKILL_GUARD = PASS  -> safe to stop writers")
    return 0


if __name__ == "__main__":
    sys.exit(main())
