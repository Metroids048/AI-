"""I-1 acceptance: does the REAL repository read path now return funding/OI?

This is the defect I-1 exists to fix. Pre-migration, rows were stored as
'BTC/USDT:USDT' while repository.load_market_extras() queried
canonical_market_symbol(symbol) == 'BTC/USDT', so the query matched nothing and
research code silently saw zero funding / OI.

Read-only: opens its own read-only connection, calls the production canonical
helper, and does NOT write.
"""

from __future__ import annotations

import json
import pathlib
import sqlite3
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from services.data.universe import canonical_market_symbol  # noqa: E402

DB = pathlib.Path(".local_paper_console.db").resolve()
ART = pathlib.Path("artifacts/t0-i1-symbol-canonical-20260809")
PROBES = ["BTC/USDT:USDT", "BTC/USDT", "ETH/USDT:USDT", "ETH/USDT", "SOL/USDT:USDT"]

con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
con.row_factory = sqlite3.Row
fail: list[str] = []
out: dict[str, object] = {"db": str(DB), "probes": {}}

print("=== canonical helper vs migration SQL rule ===")
for s in PROBES:
    print(f"  canonical_market_symbol({s!r}) = {canonical_market_symbol(s)!r}")
mism = con.execute("SELECT COUNT(*) c FROM market_extras WHERE symbol <> REPLACE(symbol, ':USDT','')").fetchone()["c"]
print(f"  rows where SQL rule would still change symbol: {mism}  (must be 0)")
if mism:
    fail.append("SQL canonical rule still has work to do -> migration incomplete")

print("\n=== read path: query by the form callers actually pass ===")
for probe in PROBES:
    canon = canonical_market_symbol(probe)
    row = con.execute(
        """
        SELECT COUNT(*) n,
               COUNT(funding_rate) fr, COUNT(open_interest) oi,
               MIN(time) mn, MAX(time) mx
          FROM market_extras WHERE symbol = ?
        """,
        (canon,),
    ).fetchone()
    n, fr, oi = row["n"], row["fr"], row["oi"]
    print(
        f"  probe={probe:<15} -> WHERE symbol={canon!r}: rows={n:<7} funding={fr:<7} oi={oi:<7} {row['mn']} .. {row['mx']}"
    )
    out["probes"][probe] = {
        "canonical": canon,
        "rows": n,
        "funding_rate_nonnull": fr,
        "open_interest_nonnull": oi,
        "min_time": str(row["mn"]),
        "max_time": str(row["mx"]),
    }
    if probe.startswith(("BTC", "ETH")) and n == 0:
        fail.append(f"read path STILL returns 0 rows for {probe} -> I-1 did not fix the defect")

print("\n=== sample real payload (BTC/USDT, newest 3) ===")
for r in con.execute(
    """
    SELECT symbol, time, funding_rate, open_interest, long_ratio, short_ratio, liquidation_usd
      FROM market_extras WHERE symbol = 'BTC/USDT' ORDER BY time DESC LIMIT 3
    """
):
    d = dict(r)
    print(
        f"  {d['time']} funding={d['funding_rate']} oi={d['open_interest']} "
        f"long={d['long_ratio']} short={d['short_ratio']} liq={d['liquidation_usd']}"
    )
    if d["funding_rate"] is None and d["open_interest"] is None:
        fail.append("newest BTC/USDT row has NULL funding AND NULL open_interest")

print("\n=== freshness (honest reporting, not a pass/fail gate) ===")
for sym in ("BTC/USDT", "ETH/USDT"):
    r = con.execute("SELECT MAX(time) mx, COUNT(*) n FROM market_extras WHERE symbol = ?", (sym,)).fetchone()
    print(f"  {sym}: rows={r['n']} newest={r['mx']}")
out["note"] = (
    "market_extras is stale (newest 2026-07-26) because live WebSocket collectors "
    "are disabled in this environment ('no Binance proxy configured'). I-1 fixes the "
    "symbol form so the read path resolves; it does not resurrect the collector."
)
print(f"  NOTE: {out['note']}")

print()
verdict = "FAIL" if fail else "PASS"
print(f"READPATH_ACCEPTANCE = {verdict}")
for f in fail:
    print("  - " + f)
out["verdict"] = verdict
out["failures"] = fail
ART.mkdir(parents=True, exist_ok=True)
(ART / "STEP15_READPATH_ACCEPTANCE.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
print(f"written: {ART / 'STEP15_READPATH_ACCEPTANCE.json'}")
con.close()
sys.exit(1 if fail else 0)
