"""I-1 steps 10-13: M-02 canonical migration of market_extras.symbol.

THE ONLY WRITING SCRIPT IN I-1. Everything else in this window is mode=ro.

Order is fixed and not negotiable:
  BEGIN IMMEDIATE -> UPDATE -> in-transaction verification -> COMMIT (or ROLLBACK)

The acceptance idea: build a fingerprint of every row keyed by
``(canonical_symbol, time, ...payload)``. A pure symbol rename must leave that
fingerprint bit-identical, which simultaneously proves no row was lost, duplicated,
or had a payload altered. ``ohlcv_bars`` gets the same treatment to prove it was
never touched (plan §9 acceptance for I-1).

Canonical rule mirrors the write path exactly:
``services/data/universe.py::canonical_market_symbol`` = ``symbol.replace(":USDT", "")``.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

DB = ".local_paper_console.db"
OUT_DIR = Path("artifacts/t0-i1-symbol-canonical-20260809")
EXPECT_TOTAL = 106292
EXPECT_LEGACY_SYMBOLS = 10


def fingerprint(con: sqlite3.Connection, sql: str) -> tuple[str, int]:
    """Streaming sha256 over an ordered result set, plus the row count."""
    h = hashlib.sha256()
    n = 0
    for row in con.execute(sql):
        h.update(repr(row).encode("utf-8"))
        h.update(b"\x1e")
        n += 1
    return h.hexdigest(), n


MARKET_FP_SQL = """
SELECT REPLACE(symbol, ':USDT', ''), time, funding_rate, open_interest,
       long_ratio, short_ratio, liquidation_usd
FROM market_extras
ORDER BY 1, 2
"""

OHLCV_FP_SQL = """
SELECT symbol, exchange, timeframe, time, open, high, low, close, volume
FROM ohlcv_bars
ORDER BY symbol, exchange, timeframe, time
"""


def writers_present() -> tuple[int, int]:
    now = datetime.now(UTC).replace(tzinfo=None).isoformat(sep=" ")
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        leases = con.execute("SELECT COUNT(*) FROM scheduler_leases WHERE expires_at > ?", (now,)).fetchone()[0]
    finally:
        con.close()
    ps = (
        "@(Get-CimInstance Win32_Process | Where-Object { $_.Name -match 'python' -and "
        "$_.CommandLine -match 'run-local-paper-scheduler|apps\\.api\\.local_server' }).Count"
    )
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, check=False
    ).stdout.strip()
    return leases, int(out or 0)


def main() -> int:
    report: dict[str, object] = {
        "started_at": datetime.now(UTC).isoformat(),
        "purpose": "I-1 steps 10-13: M-02 canonical migration of market_extras.symbol",
        "production_write": True,
        "canonical_rule": "symbol.replace(':USDT','')",
    }

    leases, procs = writers_present()
    print(f"pre-flight: non-expired leases = {leases}   live scheduler/API processes = {procs}")
    if leases or procs:
        print("ABORT: writers present. Migration requires a zero-writer window.")
        return 1

    backups = sorted(Path(".").glob(f"{DB}.backup_i1_*"))
    if not backups:
        print("ABORT: no I-1 backup found. Backup is the only rollback path (D-A).")
        return 1
    print(f"pre-flight: backup present -> {backups[-1].name}")
    report["backup_used_as_rollback"] = backups[-1].name

    con = sqlite3.connect(DB, isolation_level=None)
    try:
        con.execute("BEGIN IMMEDIATE")
        print("\nBEGIN IMMEDIATE acquired")

        # Re-assert the stop conditions with the write lock held: nothing may have
        # slipped in between the audit and this transaction.
        canon_rows = con.execute(
            "SELECT COUNT(*) FROM market_extras WHERE symbol = REPLACE(symbol, ':USDT', '')"
        ).fetchone()[0]
        overlap = con.execute(
            """
            SELECT COUNT(*) FROM market_extras AS legacy
            JOIN market_extras AS canon
              ON canon.symbol = REPLACE(legacy.symbol, ':USDT', '')
             AND canon.time = legacy.time
             AND canon.symbol = REPLACE(canon.symbol, ':USDT', '')
            WHERE legacy.symbol <> REPLACE(legacy.symbol, ':USDT', '')
            """
        ).fetchone()[0]
        legacy_rows_pre = con.execute(
            "SELECT COUNT(*) FROM market_extras WHERE symbol <> REPLACE(symbol, ':USDT', '')"
        ).fetchone()[0]
        legacy_syms_pre = con.execute(
            "SELECT COUNT(DISTINCT symbol) FROM market_extras WHERE symbol <> REPLACE(symbol, ':USDT', '')"
        ).fetchone()[0]
        total_pre = con.execute("SELECT COUNT(*) FROM market_extras").fetchone()[0]

        print(f"  in-txn canonical_rows      = {canon_rows}   (must be 0)")
        print(f"  in-txn overlap             = {overlap}   (must be 0)")
        print(f"  in-txn legacy rows/symbols = {legacy_rows_pre} / {legacy_syms_pre}")
        print(f"  in-txn total rows          = {total_pre}")

        if canon_rows or overlap:
            con.execute("ROLLBACK")
            print("ROLLBACK: stop condition appeared inside the transaction.")
            return 1
        if total_pre != EXPECT_TOTAL or legacy_rows_pre != EXPECT_TOTAL:
            con.execute("ROLLBACK")
            print(f"ROLLBACK: unexpected shape (expected {EXPECT_TOTAL} rows, all legacy).")
            return 1
        if legacy_syms_pre != EXPECT_LEGACY_SYMBOLS:
            con.execute("ROLLBACK")
            print(f"ROLLBACK: expected {EXPECT_LEGACY_SYMBOLS} legacy symbols.")
            return 1

        print("\ncomputing pre-migration fingerprints ...")
        me_fp_pre, me_n_pre = fingerprint(con, MARKET_FP_SQL)
        oh_fp_pre, oh_n_pre = fingerprint(con, OHLCV_FP_SQL)
        print(f"  market_extras fp = {me_fp_pre}  rows={me_n_pre}")
        print(f"  ohlcv_bars    fp = {oh_fp_pre}  rows={oh_n_pre}")

        per_symbol_pre = {
            r[0]: r[1]
            for r in con.execute(
                "SELECT REPLACE(symbol, ':USDT', ''), COUNT(*) FROM market_extras GROUP BY 1 ORDER BY 1"
            )
        }
        report["pre"] = {
            "total_rows": total_pre,
            "legacy_rows": legacy_rows_pre,
            "legacy_symbols": legacy_syms_pre,
            "market_extras_fingerprint": me_fp_pre,
            "ohlcv_bars_fingerprint": oh_fp_pre,
            "ohlcv_bars_rows": oh_n_pre,
            "per_canonical_symbol_rows": per_symbol_pre,
        }

        return _apply(con, report, me_fp_pre, me_n_pre, oh_fp_pre, oh_n_pre, per_symbol_pre, total_pre)
    finally:
        con.close()


def _apply(
    con: sqlite3.Connection,
    report: dict[str, object],
    me_fp_pre: str,
    me_n_pre: int,
    oh_fp_pre: str,
    oh_n_pre: int,
    per_symbol_pre: dict[str, int],
    total_pre: int,
) -> int:
    print("\nexecuting canonical UPDATE ...")
    cur = con.execute(
        "UPDATE market_extras SET symbol = REPLACE(symbol, ':USDT', '') WHERE symbol <> REPLACE(symbol, ':USDT', '')"
    )
    updated = cur.rowcount
    print(f"  rows updated = {updated}")
    report["rows_updated"] = updated

    print("\n--- in-transaction verification ---")
    fails: list[str] = []

    total_post = con.execute("SELECT COUNT(*) FROM market_extras").fetchone()[0]
    legacy_post = con.execute(
        "SELECT COUNT(*) FROM market_extras WHERE symbol <> REPLACE(symbol, ':USDT', '')"
    ).fetchone()[0]
    like_post = con.execute("SELECT COUNT(*) FROM market_extras WHERE symbol LIKE '%:USDT'").fetchone()[0]
    dupes = con.execute(
        "SELECT COUNT(*) FROM (SELECT symbol, time FROM market_extras GROUP BY symbol, time HAVING COUNT(*) > 1)"
    ).fetchone()[0]
    canon_post = con.execute("SELECT COUNT(DISTINCT symbol) FROM market_extras").fetchone()[0]

    me_fp_post, me_n_post = fingerprint(con, MARKET_FP_SQL)
    oh_fp_post, oh_n_post = fingerprint(con, OHLCV_FP_SQL)
    per_symbol_post = {
        r[0]: r[1] for r in con.execute("SELECT symbol, COUNT(*) FROM market_extras GROUP BY 1 ORDER BY 1")
    }

    print(f"  rows updated               : {updated}          (expect {total_pre})")
    print(f"  total rows                 : {total_post}          (expect {total_pre})")
    print(f"  legacy rows (symbol<>canon): {legacy_post}               (expect 0)")
    print(f"  rows LIKE '%:USDT'         : {like_post}               (expect 0)")
    print(f"  duplicate (symbol,time)    : {dupes}               (expect 0)")
    print(f"  distinct symbols           : {canon_post}              (expect {len(per_symbol_pre)})")
    print(f"  market_extras fp           : {me_fp_post}")
    print(f"    identical to pre         : {me_fp_post == me_fp_pre}")
    print(f"  ohlcv_bars fp              : {oh_fp_post}")
    print(f"    identical to pre         : {oh_fp_post == oh_fp_pre}  rows {oh_n_post} (pre {oh_n_pre})")

    if updated != total_pre:
        fails.append(f"rows updated {updated} != expected {total_pre}")
    if total_post != total_pre:
        fails.append(f"row count changed: {total_pre} -> {total_post}")
    if legacy_post:
        fails.append(f"legacy-shaped rows remain: {legacy_post}")
    if like_post:
        fails.append(f"rows still LIKE '%:USDT': {like_post}")
    if dupes:
        fails.append(f"duplicate (symbol,time) pairs: {dupes}")
    if me_fp_post != me_fp_pre:
        fails.append("market_extras payload fingerprint changed — not a pure rename")
    if oh_fp_post != oh_fp_pre or oh_n_post != oh_n_pre:
        fails.append("ohlcv_bars changed — migration must not touch it")
    if per_symbol_post != per_symbol_pre:
        fails.append(f"per-symbol counts changed: {per_symbol_pre} -> {per_symbol_post}")

    report["post"] = {
        "total_rows": total_post,
        "legacy_rows": legacy_post,
        "rows_like_legacy": like_post,
        "duplicate_symbol_time": dupes,
        "distinct_symbols": canon_post,
        "market_extras_fingerprint": me_fp_post,
        "ohlcv_bars_fingerprint": oh_fp_post,
        "ohlcv_bars_rows": oh_n_post,
        "per_canonical_symbol_rows": per_symbol_post,
        "fingerprint_identical": me_fp_post == me_fp_pre,
        "ohlcv_untouched": oh_fp_post == oh_fp_pre and oh_n_post == oh_n_pre,
    }

    if fails:
        con.execute("ROLLBACK")
        report["result"] = "ROLLBACK"
        report["failures"] = fails
        print("\nROLLBACK — verification failed:")
        for f in fails:
            print(f"  - {f}")
    else:
        con.execute("COMMIT")
        report["result"] = "COMMIT"
        print("\nCOMMIT — all in-transaction verification passed")

    report["finished_at"] = datetime.now(UTC).isoformat()
    report["db_size_after"] = os.path.getsize(DB)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / "STEP10_13_MIGRATION_RESULT.json"
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nwritten -> {out}")
    print(f"\nMIGRATION = {report['result']}")
    return 0 if report["result"] == "COMMIT" else 1


if __name__ == "__main__":
    sys.exit(main())
