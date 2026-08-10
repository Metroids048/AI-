"""I-1 step 6: P0-1 market_extras collision audit, re-run at migration time.

READ-ONLY (mode=ro). Exit 0 only when the verdict is SAFE_PLAIN_MIGRATION.

Mirrors the frozen P0-1 definition (plan §3) and the M-02 collision rules (§6):
  legacy vs canonical same (symbol, time), payload fully equivalent -> keep canonical, drop legacy dup
  any payload business field differs        -> SYMBOL_MIGRATION_COLLISION -> STOP, no auto-merge

The canonical rule must match the write path exactly:
``services/data/universe.py::canonical_market_symbol`` = ``symbol.replace(":USDT", "")``,
so SQL uses REPLACE(symbol, ':USDT', ''), not a suffix strip.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

DB = ".local_paper_console.db"
OUT_DIR = Path("artifacts/t0-i1-symbol-canonical-20260809")
CANON = "REPLACE(symbol, ':USDT', '')"


def main() -> int:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    report: dict[str, object] = {
        "captured_at": datetime.now(UTC).isoformat(),
        "purpose": "I-1 step 6: collision audit re-run at migration time (writer-zero window)",
        "production_write": False,
        "canonical_rule": "symbol.replace(':USDT','')",
    }
    try:
        cols = [c[1] for c in con.execute("PRAGMA table_info(market_extras)")]
        payload = [c for c in cols if c not in ("symbol", "time")]
        print(f"market_extras columns : {cols}")
        print(f"payload fields        : {payload}\n")
        report["columns"] = cols
        report["payload_fields"] = payload

        ddl = con.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='market_extras'").fetchone()[0]
        idx = list(con.execute("SELECT name, sql FROM sqlite_master WHERE type='index' AND tbl_name='market_extras'"))
        print("--- schema ---")
        print(f"  {ddl}")
        for name, sql in idx:
            print(f"  index {name}: {sql}")
        report["ddl"] = ddl
        report["indexes"] = [{"name": n, "sql": s} for n, s in idx]

        total = con.execute("SELECT COUNT(*) FROM market_extras").fetchone()[0]
        like_rows = con.execute("SELECT COUNT(*) FROM market_extras WHERE symbol LIKE '%:USDT'").fetchone()[0]
        need_rows = con.execute(f"SELECT COUNT(*) FROM market_extras WHERE symbol <> {CANON}").fetchone()[0]
        canon_rows = con.execute(f"SELECT COUNT(*) FROM market_extras WHERE symbol = {CANON}").fetchone()[0]
        like_syms = con.execute(
            "SELECT COUNT(DISTINCT symbol) FROM market_extras WHERE symbol LIKE '%:USDT'"
        ).fetchone()[0]
        need_syms = con.execute(f"SELECT COUNT(DISTINCT symbol) FROM market_extras WHERE symbol <> {CANON}").fetchone()[
            0
        ]
        canon_syms = con.execute(f"SELECT COUNT(DISTINCT symbol) FROM market_extras WHERE symbol = {CANON}").fetchone()[
            0
        ]

        print("\n--- symbol inventory ---")
        print(f"  total rows                     : {total}")
        print(f"  legacy  LIKE '%:USDT'          : {like_syms} symbols, {like_rows} rows")
        print(f"  needs-migration (symbol<>canon): {need_syms} symbols, {need_rows} rows")
        print(f"  canonical (symbol=canon)       : {canon_syms} symbols, {canon_rows} rows")
        report["inventory"] = {
            "total_rows": total,
            "legacy_like_symbols": like_syms,
            "legacy_like_rows": like_rows,
            "needs_migration_symbols": need_syms,
            "needs_migration_rows": need_rows,
            "canonical_symbols": canon_syms,
            "canonical_rows": canon_rows,
        }
        return _finish(con, report, payload, total, need_rows, canon_rows, like_rows)
    finally:
        con.close()


def _finish(
    con: sqlite3.Connection,
    report: dict[str, object],
    payload: list[str],
    total: int,
    need_rows: int,
    canon_rows: int,
    like_rows: int,
) -> int:
    per_symbol = [
        {"symbol": r[0], "canonical": r[1], "rows": r[2], "min_time": str(r[3]), "max_time": str(r[4])}
        for r in con.execute(
            f"SELECT symbol, {CANON}, COUNT(*), MIN(time), MAX(time) FROM market_extras GROUP BY symbol ORDER BY symbol"
        )
    ]
    print("\n--- per-symbol detail ---")
    for s in per_symbol:
        flag = "" if s["symbol"] == s["canonical"] else "  -> " + str(s["canonical"])
        print(f"  {s['symbol']:<20} rows={s['rows']:<8} {s['min_time']} .. {s['max_time']}{flag}")
    report["per_symbol"] = per_symbol

    # Collision = a row needing migration whose target (canonical, time) is already
    # occupied by an existing canonical row. That is what would break uniqueness.
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

    diff_expr = " OR ".join(
        f"IFNULL(CAST(legacy.{c} AS TEXT),'~') <> IFNULL(CAST(canon.{c} AS TEXT),'~')" for c in payload
    )
    conflicting = 0
    if overlap:
        conflicting = con.execute(
            f"""
            SELECT COUNT(*) FROM market_extras AS legacy
            JOIN market_extras AS canon
              ON canon.symbol = REPLACE(legacy.symbol, ':USDT', '')
             AND canon.time = legacy.time
             AND canon.symbol = REPLACE(canon.symbol, ':USDT', '')
            WHERE legacy.symbol <> REPLACE(legacy.symbol, ':USDT', '')
              AND ({diff_expr})
            """
        ).fetchone()[0]
    equivalent = overlap - conflicting

    # Two distinct legacy symbols collapsing onto one canonical identity at the same
    # timestamp would also break uniqueness, without any pre-existing canonical row.
    internal = con.execute(
        f"""
        SELECT COUNT(*) FROM (
          SELECT {CANON} AS c, time, COUNT(*) AS n, COUNT(DISTINCT symbol) AS d
          FROM market_extras GROUP BY c, time HAVING d > 1
        )
        """
    ).fetchone()[0]

    print("\n--- collision audit ---")
    print(f"  overlapping (canonical,time) pairs : {overlap}")
    print(f"    payload equivalent               : {equivalent}")
    print(f"    payload CONFLICTING              : {conflicting}")
    print(f"  internal many-legacy->one-canonical: {internal}")

    stops: list[str] = []
    if canon_rows > 0:
        stops.append(f"canonical_rows > 0 ({canon_rows}) — operator stop condition")
    if overlap > 0:
        stops.append(f"overlap > 0 ({overlap}) — operator stop condition")
    if conflicting > 0:
        stops.append(f"conflicting > 0 ({conflicting}) — SYMBOL_MIGRATION_COLLISION, no auto-merge")
    if internal > 0:
        stops.append(f"internal collapse collisions ({internal}) — uniqueness would break")
    if need_rows == 0:
        stops.append("nothing to migrate (needs_migration_rows = 0)")

    verdict = "SAFE_PLAIN_MIGRATION" if not stops else "STOP"
    report["collision"] = {
        "overlap": overlap,
        "payload_equivalent": equivalent,
        "payload_conflicting": conflicting,
        "internal_collapse": internal,
    }
    report["verdict"] = verdict
    report["stop_reasons"] = stops

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / "STEP06_COLLISION_AUDIT_AT_MIGRATION_TIME.json"
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"\nP0-1 VERDICT: {verdict}")
    for s in stops:
        print(f"  - {s}")
    print(f"\nwritten -> {out}")
    return 0 if verdict == "SAFE_PLAIN_MIGRATION" else 1


if __name__ == "__main__":
    sys.exit(main())
