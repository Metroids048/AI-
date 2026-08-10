"""I-1 step 5: DB health after writer-zero. READ-ONLY (mode=ro). Exit 0 = PASS.

Proves the DB is actually fit for migration rather than relying on "SQLite is
crash-safe" as an acceptance result. Refuses to fall back to a writable handle.
"""

from __future__ import annotations

import os
import sqlite3
import sys

DB = ".local_paper_console.db"


def main() -> int:
    print("--- file family ---")
    for suffix in ("", "-wal", "-shm", "-journal"):
        p = DB + suffix
        if os.path.exists(p):
            print(f"  {p}  {os.path.getsize(p)} bytes")
        else:
            print(f"  {p}  (absent)")

    try:
        con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        print(f"\nFATAL: cannot open read-only: {exc}")
        print("DB_HEALTH = FAIL (did not retry writable on purpose)")
        return 1

    fails: list[str] = []
    try:
        print("\n--- pragmas ---")
        jm = con.execute("PRAGMA journal_mode").fetchone()[0]
        pc = con.execute("PRAGMA page_count").fetchone()[0]
        ps = con.execute("PRAGMA page_size").fetchone()[0]
        fk = con.execute("PRAGMA foreign_keys").fetchone()[0]
        print(f"  journal_mode = {jm}")
        print(f"  page_count = {pc}  page_size = {ps}  -> {pc * ps} bytes")
        print(f"  foreign_keys = {fk}")

        print("\n--- quick_check (may take a while) ---")
        rows = [r[0] for r in con.execute("PRAGMA quick_check")]
        for r in rows[:20]:
            print(f"  {r}")
        if rows != ["ok"]:
            fails.append(f"quick_check not ok: {rows[:5]}")

        print("\n--- key table counts ---")
        for t in (
            "market_extras",
            "ohlcv_bars",
            "v2_execution_cycles",
            "scheduler_cycles",
            "v2_managed_positions",
            "v2_protection_records",
            "scheduler_leases",
        ):
            n = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            print(f"  {t} = {n}")
    finally:
        con.close()

    print()
    if fails:
        print("DB_HEALTH = FAIL")
        for f in fails:
            print(f"  - {f}")
        return 1
    print("DB_HEALTH = PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
