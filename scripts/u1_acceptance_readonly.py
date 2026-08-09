"""U1 acceptance verification (READ-ONLY).

Checks the 15 hard acceptance criteria A1-A15 for the UNRECONCILED_EXIT repair
of Binance Testnet reduce-only order 28533281387.

Read-only: opens the runtime SQLite file with ``mode=ro`` and never writes.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / ".local_paper_console.db"
ARTIFACT_DIR = ROOT / "artifacts" / "t0-u1-unreconciled-exit-20260809"

POSITION_ID = "10920a3c-6260-479c-8af6-6c410b303cfd"
EXIT_ORDER_ID = "28533281387"
EXIT_TRADE_ID = "525914324"
ENTRY_ORDER_ID = "28531748285"
PROTECTION_ID = "db4864db-bca1-43bd-944f-eef0a2bd29e9"

EXPECTED_QTY = Decimal("0.0388")
EXPECTED_PRICE = Decimal("64768.6")
EXPECTED_FEE = Decimal("1.00520867")
EXPECTED_ENTRY_FEE = Decimal("1.00874102")
EXPECTED_CLOSED_AT = "2026-08-09 02:47:08.700000"
GROSS_PNL = Decimal("-8.83088000")
NET_PNL = Decimal("-10.84482969")

EPS = Decimal("0.0001")
EPS_TIGHT = Decimal("0.00000001")


def _rows(conn: sqlite3.Connection, sql: str, params: tuple[object, ...] = ()) -> list[dict[str, object]]:
    cur = conn.execute(sql, params)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]


def _close(actual: Decimal, expected: Decimal, eps: Decimal) -> bool:
    return abs(actual - expected) <= eps


def main() -> int:
    conn = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
    results: list[tuple[str, bool, str]] = []

    def check(tag: str, ok: bool, detail: str) -> None:
        results.append((tag, bool(ok), detail))

    position = _rows(
        conn,
        "SELECT state, closed_at, realized_pnl, quantity, entry_price, entry_fee, version "
        "FROM v2_managed_positions WHERE position_id = ?",
        (POSITION_ID,),
    )
    fills = _rows(
        conn,
        "SELECT fill_id, exchange_order_id, trade_id, side, reduce_only, filled_quantity, "
        "fill_price, commission, exchange_event_time, raw_hash "
        "FROM v2_exchange_fills WHERE exchange_order_id = ?",
        (EXIT_ORDER_ID,),
    )
    protection = _rows(
        conn,
        "SELECT state, version FROM v2_protection_records WHERE protection_id = ?",
        (PROTECTION_ID,),
    )
    exit_orders = _rows(
        conn,
        "SELECT order_record_id, exchange_order_id, filled_quantity, average_fill_price, total_fee "
        "FROM v2_exchange_orders WHERE exchange_order_id = ?",
        (EXIT_ORDER_ID,),
    )

    # --- A1: exactly one real reduce-only fill for the exit order
    check("A1 single reduce-only fill", len(fills) == 1, f"fill rows={len(fills)}")

    fill = fills[0] if fills else {}

    # --- A2: fill is not synthetic (real trade id + raw hash keyed on exchange ids)
    check(
        "A2 not synthetic",
        str(fill.get("trade_id")) == EXIT_TRADE_ID and str(fill.get("raw_hash")) == f"{EXIT_ORDER_ID}:{EXIT_TRADE_ID}",
        f"trade_id={fill.get('trade_id')} raw_hash={fill.get('raw_hash')}",
    )

    # --- A3: quantity
    q = Decimal(str(fill.get("filled_quantity", "0")))
    check("A3 qty 0.0388", _close(q, EXPECTED_QTY, EPS_TIGHT), f"filled_quantity={q}")

    # --- A4: price
    p = Decimal(str(fill.get("fill_price", "0")))
    check("A4 price 64768.6", _close(p, EXPECTED_PRICE, EPS), f"fill_price={p}")

    # --- A5: fee
    f = Decimal(str(fill.get("commission", "0")))
    check("A5 fee 1.00520867", _close(f, EXPECTED_FEE, EPS_TIGHT), f"commission={f}")

    # --- A6: trade id
    check("A6 trade 525914324", str(fill.get("trade_id")) == EXIT_TRADE_ID, f"trade_id={fill.get('trade_id')}")

    # --- A6b: reduce-only flag really set
    check("A6b reduce_only", bool(fill.get("reduce_only")) is True, f"reduce_only={fill.get('reduce_only')}")

    pos = position[0] if position else {}

    # --- A7: position CLOSED
    check("A7 position CLOSED", str(pos.get("state")) == "CLOSED", f"state={pos.get('state')}")

    # --- A8: closed_at is the exchange fill timestamp
    check(
        "A8 closed_at exchange time",
        str(pos.get("closed_at")) == EXPECTED_CLOSED_AT,
        f"closed_at={pos.get('closed_at')}",
    )

    # --- A9: realized_pnl follows the single existing project contract (NET)
    realized = Decimal(str(pos.get("realized_pnl", "0")))
    check("A9 realized_pnl net -10.84482969", _close(realized, NET_PNL, EPS), f"realized_pnl={realized}")

    # --- A10: gross is derivable, fees never double-charged
    entry_fee = Decimal(str(pos.get("entry_fee", "0")))
    derived_gross = realized + entry_fee + f
    check(
        "A10 gross derivable -8.83088",
        _close(derived_gross, GROSS_PNL, EPS) and _close(entry_fee, EXPECTED_ENTRY_FEE, EPS_TIGHT),
        f"derived_gross={derived_gross} entry_fee={entry_fee}",
    )

    prot = protection[0] if protection else {}

    # --- A11: protection retired, not PROTECTION_ACTIVE
    check(
        "A11 protection retired",
        str(prot.get("state")) not in ("PROTECTION_ACTIVE", "None") and prot.get("state") is not None,
        f"state={prot.get('state')}",
    )

    # --- A12: zero surviving BTC protection rows
    surviving = _rows(
        conn,
        "SELECT p.protection_id FROM v2_protection_records p "
        "JOIN v2_managed_positions m ON m.position_id = p.position_id "
        "WHERE m.symbol = 'BTC/USDT' AND p.state = 'PROTECTION_ACTIVE'",
    )
    check("A12 zero active BTC protection", len(surviving) == 0, f"rows={len(surviving)}")

    # --- A13: no open BTC managed position remains locally
    open_btc = _rows(
        conn,
        "SELECT position_id, state FROM v2_managed_positions "
        "WHERE symbol = 'BTC/USDT' AND state NOT IN ('CLOSED', 'QUARANTINED')",
    )
    check("A13 no open local BTC position", len(open_btc) == 0, f"rows={len(open_btc)} {open_btc}")

    # --- A14: idempotency - no duplicate fills / orders / closes anywhere
    all_exit_fills = _rows(
        conn,
        "SELECT COUNT(*) AS c FROM v2_exchange_fills WHERE trade_id = ?",
        (EXIT_TRADE_ID,),
    )[0]["c"]
    reduce_only_btc = _rows(
        conn,
        "SELECT COUNT(*) AS c FROM v2_exchange_fills WHERE symbol = 'BTC/USDT' AND reduce_only = 1",
    )[0]["c"]
    exit_order_rows = len(exit_orders)
    closed_btc = _rows(
        conn,
        "SELECT COUNT(*) AS c FROM v2_managed_positions WHERE symbol = 'BTC/USDT' AND state = 'CLOSED'",
    )[0]["c"]
    ghost_incidents = _rows(
        conn,
        "SELECT COUNT(*) AS c FROM v2_execution_incidents "
        "WHERE incident_type = 'LOCAL_GHOST_QUARANTINED' AND created_at > '2026-08-09 06:00:00'",
    )[0]["c"]
    check(
        "A14 idempotent counters",
        all_exit_fills == 1
        and reduce_only_btc == 1
        and exit_order_rows == 1
        and closed_btc == 1
        and ghost_incidents == 0,
        f"trade_fills={all_exit_fills} btc_reduce_only={reduce_only_btc} "
        f"exit_order_rows={exit_order_rows} closed_btc={closed_btc} "
        f"post_restart_ghost_incidents={ghost_incidents}",
    )

    # --- A15: entry fill untouched (no rewrite of the original entry)
    entry_fills = _rows(
        conn,
        "SELECT COUNT(*) AS c FROM v2_exchange_fills WHERE exchange_order_id = ?",
        (ENTRY_ORDER_ID,),
    )[0]["c"]
    check("A15 entry fill untouched", entry_fills == 1, f"entry_fill_rows={entry_fills}")

    conn.close()

    print("=" * 78)
    print("U1 ACCEPTANCE A1-A15  (local projection side)")
    print("=" * 78)
    for tag, ok, detail in results:
        print(f"  [{'PASS' if ok else 'FAIL'}] {tag:34s} {detail}")

    failed = [tag for tag, ok, _ in results if not ok]
    verdict = "PASS" if not failed else "FAIL"
    print()
    print(f"LOCAL_ACCEPTANCE = {verdict}" + (f"  failed={failed}" if failed else ""))

    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    out = ARTIFACT_DIR / "U1_ACCEPTANCE.json"
    out.write_text(
        json.dumps(
            {
                "verdict": verdict,
                "checks": [{"tag": t, "ok": o, "detail": d} for t, o, d in results],
                "position": position,
                "exit_fills": fills,
                "protection": protection,
                "exit_order_records": exit_orders,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(f"wrote {out.relative_to(ROOT)}")
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
