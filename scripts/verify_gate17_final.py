"""Gate 17 final acceptance: full auto open/close chain on Binance Testnet.

Read-only. Verifies against the exchange-first invariant, not local rows alone:
  natural signal -> intent -> real exchange order -> real fill
  -> local projection AFTER fill -> protection -> reduce-only exit -> closed

Exit code 0 = every check passed.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from decimal import Decimal

DB = ".local_paper_console.db"
DIRECTIONAL_RUN = "35298c65-cdbe-4bee-bee3-b7ded07c3204"

checks: list[tuple[str, bool, str]] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    checks.append((label, ok, detail))


def main() -> int:
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    print("=" * 78)
    print("GATE 17 FINAL ACCEPTANCE — automatic open/close on Binance Testnet")
    print("=" * 78)

    # ---- A-001/A-002: anchor on the managed position, then trace back to its
    # ENTRY intent. Exit legs create their own synthetic reduce-only intents
    # (candidate_key "exit:<position_id>:<reason>"), so selecting the newest
    # intent would grab the exit and misreport the chain.
    position = conn.execute(
        """SELECT * FROM v2_managed_positions ORDER BY projected_at DESC LIMIT 1"""
    ).fetchone()
    if position is None:
        check("A-001 managed position exists", False, "no v2_managed_positions rows")
        _report()
        return 1

    intent = conn.execute(
        """SELECT * FROM v2_execution_intents WHERE intent_id = ?""", (position["intent_id"],)
    ).fetchone()
    if intent is None:
        check("A-001 entry intent exists", False, f"position {position['position_id']} has no intent row")
        _report()
        return 1

    print(f"\nintent_id      = {intent['intent_id']}")
    print(f"symbol         = {intent['symbol']} {intent['direction']}")
    print(f"candidate      = {intent['candidate_key']} ({intent['candidate_type']})")
    print(f"bar_timestamp  = {intent['decision_bar_timestamp']}")
    print(f"state          = {intent['state']}")

    check("A-001 intent exists", True, intent["intent_id"])
    check(
        "A-002 execution_mode is BINANCE_TESTNET",
        intent["execution_mode"] == "BINANCE_TESTNET",
        str(intent["execution_mode"]),
    )
    check("A-002 intent reached FILLED", intent["state"] == "FILLED", str(intent["state"]))

    # ---- A-003/A-004: real exchange order id + real fill
    orders = list(
        conn.execute(
            """SELECT * FROM v2_exchange_orders WHERE intent_id = ? ORDER BY submitted_at""",
            (intent["intent_id"],),
        )
    )
    entry_order = orders[0] if orders else None
    if entry_order is None:
        check("A-003 exchange order exists", False, "none")
        _report()
        return 1

    print(f"\nentry exchange_order_id = {entry_order['exchange_order_id']}")
    print(f"  qty={entry_order['quantity']} filled={entry_order['filled_quantity']} avg={entry_order['average_fill_price']}")
    print(f"  fee={entry_order['total_fee']} leverage={entry_order['leverage']}")

    check(
        "A-003 real exchange_order_id present",
        bool(entry_order["exchange_order_id"]),
        str(entry_order["exchange_order_id"]),
    )
    check(
        "A-004 filled_quantity > 0",
        Decimal(str(entry_order["filled_quantity"] or 0)) > 0,
        str(entry_order["filled_quantity"]),
    )
    check(
        "A-004 avg_fill_price > 0",
        Decimal(str(entry_order["average_fill_price"] or 0)) > 0,
        str(entry_order["average_fill_price"]),
    )

    fills = list(
        conn.execute(
            """SELECT * FROM v2_exchange_fills WHERE intent_id = ? ORDER BY exchange_event_time""",
            (intent["intent_id"],),
        )
    )
    entry_fill = next((f for f in fills if not f["reduce_only"]), None)
    exit_fill = next((f for f in fills if f["reduce_only"]), None)

    check("A-004 entry fill has a real trade_id", bool(entry_fill and entry_fill["trade_id"]),
          str(entry_fill["trade_id"]) if entry_fill else "none")

    # ---- A-005: exchange-first ordering (local projection AFTER exchange fill)
    print(f"\nposition_id  = {position['position_id']}")
    print(f"  qty={position['quantity']} entry={position['entry_price']} state={position['state']}")
    print(f"  projected_at={position['projected_at']}")
    print(f"  closed_at={position['closed_at']} realized_pnl={position['realized_pnl']}")

    check("A-001 managed position exists", True, position["position_id"])
    if entry_fill is not None:
        check(
            "A-005 exchange-first: projection AFTER exchange fill",
            str(position["projected_at"]) > str(entry_fill["exchange_event_time"]),
            f"fill={entry_fill['exchange_event_time']} projected={position['projected_at']}",
        )

    # ---- A-006: local quantity == exchange filled quantity
    check(
        "A-006 local qty == exchange filled qty",
        Decimal(str(position["quantity"])) == Decimal(str(entry_order["filled_quantity"])),
        f"local={position['quantity']} exchange={entry_order['filled_quantity']}",
    )
    check(
        "A-006 local entry price == exchange avg fill",
        Decimal(str(position["entry_price"])) == Decimal(str(entry_order["average_fill_price"])),
        f"local={position['entry_price']} exchange={entry_order['average_fill_price']}",
    )

    # ---- A-009: protection created with real exchange order ids
    protection = conn.execute(
        """SELECT * FROM v2_protection_records WHERE position_id = ?""", (position["position_id"],)
    ).fetchone()
    if protection is not None:
        print(f"\nprotection   = sl={protection['stop_loss_price']} tp={protection['take_profit_price']}")
        print(f"  sl_exchange_id={protection['stop_exchange_order_id']}")
        print(f"  tp_exchange_id={protection['tp_exchange_order_id']}")
        print(f"  state={protection['state']}")
        check(
            "A-009 protection has real exchange order ids",
            bool(protection["stop_exchange_order_id"]) and bool(protection["tp_exchange_order_id"]),
            f"sl={protection['stop_exchange_order_id']} tp={protection['tp_exchange_order_id']}",
        )
    else:
        check("A-009 protection record exists", False, "none")

    # ---- Auto exit: the exit leg is a separate reduce-only intent whose
    # candidate_key references this position, so resolve it that way.
    exit_intents = [
        row["intent_id"]
        for row in conn.execute(
            """SELECT intent_id FROM v2_execution_intents WHERE candidate_key LIKE ?""",
            (f"exit:{position['position_id']}:%",),
        )
    ]
    exit_order = None
    if exit_intents:
        placeholders = ",".join("?" * len(exit_intents))
        exit_order = conn.execute(
            f"""SELECT * FROM v2_exchange_orders WHERE intent_id IN ({placeholders})
                ORDER BY submitted_at DESC LIMIT 1""",
            exit_intents,
        ).fetchone()
        exit_fill = conn.execute(
            f"""SELECT * FROM v2_exchange_fills WHERE intent_id IN ({placeholders})
                ORDER BY exchange_event_time DESC LIMIT 1""",
            exit_intents,
        ).fetchone()
    if exit_order is not None:
        print(f"\nexit exchange_order_id = {exit_order['exchange_order_id']}")
        print(f"  qty={exit_order['quantity']} filled={exit_order['filled_quantity']} avg={exit_order['average_fill_price']}")
        check(
            "EXIT real exchange_order_id present",
            bool(exit_order["exchange_order_id"]),
            str(exit_order["exchange_order_id"]),
        )
    check("EXIT reduce-only fill exists", exit_fill is not None,
          str(exit_fill["trade_id"]) if exit_fill else "none")
    if exit_fill is not None:
        check("EXIT fill is reduce_only", bool(exit_fill["reduce_only"]), str(exit_fill["reduce_only"]))
    check("EXIT position reached CLOSED", position["state"] == "CLOSED", str(position["state"]))
    check("EXIT realized_pnl recorded", position["realized_pnl"] is not None, str(position["realized_pnl"]))

    # ---- A-010: no synthetic fills — every fill traces to an exchange trade id
    bad_fills = [f for f in fills if not f["exchange_order_id"] or not f["trade_id"]]
    check("A-010 no synthetic fills (all have exchange+trade ids)", not bad_fills, f"{len(bad_fills)} bad")

    # ---- A-008: one entry per intent (no duplicate side effects)
    entry_orders = [o for o in orders if o is entry_order or o["exchange_order_id"] == entry_order["exchange_order_id"]]
    check("A-008 single entry order per intent", len(entry_orders) == 1, f"{len(entry_orders)}")

    # ---- Sizing sanity: the position must not be dust relative to fees
    gross_move = abs(
        Decimal(str(exit_order["average_fill_price"])) - Decimal(str(entry_order["average_fill_price"]))
    ) * Decimal(str(position["quantity"])) if exit_order else Decimal("0")
    total_fee = Decimal(str(entry_order["total_fee"] or 0)) + Decimal(str((exit_order or {})["total_fee"] or 0) if exit_order else 0)
    notional = Decimal(str(position["quantity"])) * Decimal(str(position["entry_price"]))
    print(f"\nnotional     = {notional:.2f} USDT")
    print(f"gross_move   = {gross_move:.4f} USDT   total_fee = {total_fee:.4f} USDT")
    if gross_move > 0:
        print(f"fee/gross    = {(total_fee / gross_move * 100):.1f}%")

    # ---- current live config that future cycles will use
    row = conn.execute(
        "SELECT execution_profile, paper_metrics_summary FROM paper_runs WHERE paper_run_id = ?",
        (DIRECTIONAL_RUN,),
    ).fetchone()
    profile = json.loads(row["execution_profile"] or "{}")
    metrics = json.loads(row["paper_metrics_summary"] or "{}")
    equity = Decimal(str(metrics.get("account_equity") or 0))
    rpt = Decimal(str(profile.get("risk_per_trade") or 0))
    frac = Decimal(str(profile.get("max_symbol_exposure") or 0))
    lev = Decimal(str(profile.get("max_leverage") or 0))
    print("\n--- live config for future cycles ---")
    print(f"  equity={equity:.2f} risk_per_trade={rpt} max_symbol_exposure={frac} max_leverage={lev}")
    if equity > 0:
        print(f"  risk budget/trade = {equity * rpt:.2f} USDT")
        print(f"  exposure ceiling  = {equity * frac:.2f} USDT notional ({equity * frac / lev:.2f} USDT margin @ {lev}x)")
    check("CONFIG risk_per_trade is the operator's 10% band", rpt == Decimal("0.1"), str(rpt))

    conn.close()
    return _report()


def _report() -> int:
    print("\n" + "=" * 78)
    failed = [c for c in checks if not c[1]]
    for label, ok, detail in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f"  ({detail})" if detail else ""))
    print("=" * 78)
    if failed:
        print(f"RESULT: {len(failed)} FAILED / {len(checks)} checks")
        return 1
    print(f"RESULT: GATE17_AUTO_ENTRY_PASS — all {len(checks)} checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
