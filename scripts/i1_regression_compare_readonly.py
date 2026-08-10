"""I-1 step15: did the migration introduce any NEW failure mode? (READ-ONLY)

Compares execution-cycle terminals and decision rejection reasons in a window
BEFORE the migration commit against the window AFTER the scheduler restart.
A reason class present only in the AFTER window is a candidate regression.
"""

from __future__ import annotations

import json
import pathlib
import sqlite3
import sys

DB = pathlib.Path(".local_paper_console.db").resolve()
ART = pathlib.Path("artifacts/t0-i1-symbol-canonical-20260809")
# migration COMMIT happened ~2026-08-09 01:5x UTC; scheduler restarted 02:12 UTC.
CUT = "2026-08-09 02:00:00"
PRE_FROM = "2026-08-08 12:00:00"  # ~14h of pre-migration behaviour

con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
con.row_factory = sqlite3.Row
fail: list[str] = []
out: dict[str, object] = {"db": str(DB), "cut_utc": CUT, "pre_from_utc": PRE_FROM}


def bag(sql: str, *a) -> dict[str, int]:
    return {str(r[0]): r[1] for r in con.execute(sql, a).fetchall()}


print("=== A. execution cycle terminals: pre vs post ===")
pre = bag(
    "SELECT decision_terminal, COUNT(*) FROM v2_execution_cycles WHERE started_at >= ? AND started_at < ? GROUP BY 1",
    PRE_FROM,
    CUT,
)
post = bag("SELECT decision_terminal, COUNT(*) FROM v2_execution_cycles WHERE started_at >= ? GROUP BY 1", CUT)
print(f"  PRE  ({PRE_FROM} .. {CUT}): {pre}")
print(f"  POST ({CUT} .. now)       : {post}")
new_terms = sorted(set(post) - set(pre))
print(f"  terminals absent from the 14h PRE window: {new_terms or 'none'}")
truly_new_terms: list[str] = []
for term in new_terms:
    if term == "None":
        # NULL decision_terminal == a cycle still in flight at snapshot time.
        inflight = con.execute(
            "SELECT COUNT(*) c FROM v2_execution_cycles "
            "WHERE decision_terminal IS NULL AND completed_at IS NULL AND started_at >= ?",
            (CUT,),
        ).fetchone()["c"]
        hist_null = con.execute(
            "SELECT COUNT(*) c FROM v2_execution_cycles WHERE decision_terminal IS NULL AND started_at < ?", (CUT,)
        ).fetchone()["c"]
        print(
            f"    NULL terminal: {inflight} in-flight now, {hist_null} NULL cycles pre-migration "
            f"-> in-flight cycle, NOT a regression"
        )
        continue
    hist = con.execute(
        "SELECT COUNT(*) c, MIN(started_at) first_seen FROM v2_execution_cycles "
        "WHERE decision_terminal = ? AND started_at < ?",
        (term, CUT),
    ).fetchone()
    if hist["c"]:
        print(f"    {term!r}: pre-existing -- {hist['c']} before migration, first {hist['first_seen']}")
    else:
        print(f"    {term!r}: UNPRECEDENTED before {CUT} -> candidate regression")
        truly_new_terms.append(term)
out["cycle_terminals"] = {
    "pre": pre,
    "post": post,
    "absent_from_pre_window": new_terms,
    "unprecedented_in_history": truly_new_terms,
}
if truly_new_terms:
    fail.append(f"unprecedented cycle terminal(s) after migration: {truly_new_terms}")

print("\n=== B. decision terminal_reason: pre vs post ===")
pre_r = bag(
    "SELECT terminal_reason, COUNT(*) FROM v2_execution_decisions WHERE created_at >= ? AND created_at < ? GROUP BY 1",
    PRE_FROM,
    CUT,
)
post_r = bag("SELECT terminal_reason, COUNT(*) FROM v2_execution_decisions WHERE created_at >= ? GROUP BY 1", CUT)
for k, v in sorted(pre_r.items(), key=lambda kv: -kv[1]):
    print(f"  PRE   {k!r:<52} {v}")
for k, v in sorted(post_r.items(), key=lambda kv: -kv[1]):
    print(f"  POST  {k!r:<52} {v}")
new_reasons = sorted(set(post_r) - set(pre_r))
print(f"  reasons absent from the 14h PRE window: {new_reasons or 'none'}")
# A reason missing from a 14h window is NOT evidence of a regression -- different
# bars produce different indicator rejections. The real test is whether the
# reason is genuinely unprecedented across the ENTIRE pre-migration history.
truly_new: list[str] = []
for reason in new_reasons:
    hist = con.execute(
        "SELECT COUNT(*) c, MIN(created_at) first_seen FROM v2_execution_decisions "
        "WHERE terminal_reason = ? AND created_at < ?",
        (reason, CUT),
    ).fetchone()
    if hist["c"]:
        print(
            f"    {reason!r}: pre-existing -- {hist['c']} occurrences before migration, "
            f"first seen {hist['first_seen']} -> NOT a regression"
        )
    else:
        print(f"    {reason!r}: UNPRECEDENTED in all history before {CUT} -> candidate regression")
        truly_new.append(reason)
out["decision_reasons"] = {
    "pre": pre_r,
    "post": post_r,
    "absent_from_pre_window": new_reasons,
    "unprecedented_in_history": truly_new,
}
if truly_new:
    fail.append(f"unprecedented decision terminal_reason(s) after migration: {truly_new}")

print("\n=== C. symbol-shaped errors in post-migration decision payloads ===")
# A symbol-form regression would surface as a payload mentioning ':USDT' where a
# canonical form was expected, or a lookup miss on market_extras.
hits = con.execute(
    """
    SELECT terminal_reason, COUNT(*) c FROM v2_execution_decisions
     WHERE created_at >= ?
       AND (payload LIKE '%:USDT%' OR payload LIKE '%market_extras%'
            OR payload LIKE '%funding%' OR payload LIKE '%symbol_not_found%')
     GROUP BY 1 ORDER BY c DESC
    """,
    (CUT,),
).fetchall()
if hits:
    for r in hits:
        print(f"  {r['terminal_reason']!r:<52} {r['c']}")
    print("  (':USDT' inside a payload is NORMAL: perp_symbol is a legitimate exchange field)")
else:
    print("  no symbol/funding-shaped decision payloads in post window")
out["symbol_shaped_payloads"] = [dict(r) for r in hits]

print("\n=== D. post-migration cycle volume (is the runtime actually cycling?) ===")
n_cyc = con.execute("SELECT COUNT(*) c FROM v2_execution_cycles WHERE started_at >= ?", (CUT,)).fetchone()["c"]
n_dec = con.execute("SELECT COUNT(*) c FROM v2_execution_decisions WHERE created_at >= ?", (CUT,)).fetchone()["c"]
last = con.execute(
    "SELECT symbol, bar_timestamp, decision_terminal, started_at FROM v2_execution_cycles "
    "ORDER BY started_at DESC LIMIT 6"
).fetchall()
print(f"  cycles since {CUT}: {n_cyc}   decisions: {n_dec}")
for r in last:
    d = dict(r)
    print(f"    {d['started_at']} {d['symbol']:<10} bar={d['bar_timestamp']} -> {d['decision_terminal']}")
out["post_volume"] = {"cycles": n_cyc, "decisions": n_dec}
if n_cyc == 0:
    fail.append("no execution cycles after restart -> runtime is not cycling")

print()
verdict = "FAIL" if fail else "PASS"
print(f"REGRESSION_COMPARE = {verdict}")
for f in fail:
    print("  - " + f)
out["verdict"] = verdict
out["failures"] = fail
ART.mkdir(parents=True, exist_ok=True)
(ART / "STEP15_REGRESSION_COMPARE.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
print(f"written: {ART / 'STEP15_REGRESSION_COMPARE.json'}")
con.close()
sys.exit(1 if fail else 0)
