"""I-1 post-commit verification. READ-ONLY (mode=ro). Exit 0 = PASS.

Runs in a fresh process against the committed file, so it cannot be fooled by the
migrating process's own transaction state. Also re-compares the payload fingerprint
recorded during the transaction, proving the commit persisted a pure rename.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from pathlib import Path

DB = ".local_paper_console.db"
REPORT = Path("artifacts/t0-i1-symbol-canonical-20260809/STEP10_13_MIGRATION_RESULT.json")
EXPECT_TOTAL = 106292
FP_SQL = """
SELECT REPLACE(symbol, ':USDT', ''), time, funding_rate, open_interest,
       long_ratio, short_ratio, liquidation_usd
FROM market_extras
ORDER BY 1, 2
"""


def main() -> int:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    fails: list[str] = []
    try:
        total = con.execute("SELECT COUNT(*) FROM market_extras").fetchone()[0]
        legacy = con.execute(
            "SELECT COUNT(*) FROM market_extras WHERE symbol <> REPLACE(symbol, ':USDT', '')"
        ).fetchone()[0]
        like = con.execute("SELECT COUNT(*) FROM market_extras WHERE symbol LIKE '%:USDT'").fetchone()[0]
        dupes = con.execute(
            "SELECT COUNT(*) FROM (SELECT symbol, time FROM market_extras GROUP BY symbol, time HAVING COUNT(*) > 1)"
        ).fetchone()[0]
        symbols = [r[0] for r in con.execute("SELECT DISTINCT symbol FROM market_extras ORDER BY 1")]
        qc = [r[0] for r in con.execute("PRAGMA quick_check")]

        h = hashlib.sha256()
        for row in con.execute(FP_SQL):
            h.update(repr(row).encode("utf-8"))
            h.update(b"\x1e")
        fp = h.hexdigest()

        ctrl = con.execute("SELECT entry_enabled, reason FROM v2_runtime_controls WHERE scope='global'").fetchone()
        pos = list(
            con.execute(
                "SELECT symbol, direction, quantity, entry_price, state FROM v2_managed_positions "
                "WHERE state NOT IN ('CLOSED','QUARANTINED')"
            )
        )
        prot = list(
            con.execute(
                "SELECT stop_loss_price, stop_exchange_order_id, take_profit_price, "
                "tp_exchange_order_id FROM v2_protection_records WHERE state='PROTECTION_ACTIVE'"
            )
        )
        ohlcv = con.execute("SELECT COUNT(*) FROM ohlcv_bars").fetchone()[0]
    finally:
        con.close()

    print(f"market_extras total        : {total}   (expect {EXPECT_TOTAL})")
    print(f"legacy rows (symbol<>canon): {legacy}       (expect 0)")
    print(f"rows LIKE '%:USDT'         : {like}       (expect 0)")
    print(f"duplicate (symbol,time)    : {dupes}       (expect 0)")
    print(f"distinct symbols           : {symbols}")
    print(f"quick_check                : {qc}")
    print(f"ohlcv_bars rows            : {ohlcv}   (expect 232814)")
    print(f"payload fingerprint        : {fp}")
    print(f"entry_enabled              : {ctrl[0]}  reason={ctrl[1]}")
    for p in pos:
        print(f"position                   : {p[0]} {p[1]} qty={p[2]} entry={p[3]} {p[4]}")
    for r in prot:
        print(f"protection                 : SL={r[0]}@{r[1]} TP={r[2]}@{r[3]}")

    if total != EXPECT_TOTAL:
        fails.append(f"total rows {total} != {EXPECT_TOTAL}")
    if legacy or like:
        fails.append(f"legacy-shaped rows remain: {legacy}/{like}")
    if dupes:
        fails.append(f"duplicates: {dupes}")
    if any(s.endswith(":USDT") for s in symbols):
        fails.append(f"legacy symbol identity still present: {symbols}")
    if qc != ["ok"]:
        fails.append(f"quick_check not ok: {qc[:5]}")
    if ohlcv != 232814:
        fails.append(f"ohlcv_bars row count changed: {ohlcv}")
    if not ctrl or ctrl[0] != 0:
        fails.append("entry_enabled must remain 0 until post-start verification completes")
    if not pos:
        fails.append("open position disappeared")
    if not prot or not prot[0][1] or not prot[0][3]:
        fails.append("exchange-native SL/TP missing")

    if REPORT.exists():
        recorded = json.loads(REPORT.read_text(encoding="utf-8"))["post"]["market_extras_fingerprint"]
        print(f"fingerprint recorded in txn: {recorded}")
        print(f"  matches committed file   : {recorded == fp}")
        if recorded != fp:
            fails.append("committed fingerprint differs from the in-transaction value")

    print()
    if fails:
        print("POST_MIGRATION = FAIL")
        for f in fails:
            print(f"  - {f}")
        return 1
    print("POST_MIGRATION = PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
