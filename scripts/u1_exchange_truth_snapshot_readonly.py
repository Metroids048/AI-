"""U1-A exchange truth snapshot (READ-ONLY).

Freezes Binance Testnet truth for the BTC native stop-loss exit, resolved via
clientOrderId and the algo->actualOrderId link. Never uses fetch_order(algo_id)
to judge protection health.

Writes nothing to the DB and submits no orders.
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib

from services.automated_trading.domain.enums import V2ExecutionMode
from services.automated_trading.infrastructure.binance_adapter import BinanceTestnetAdapter

ART = pathlib.Path("artifacts/t0-u1-unreconciled-exit-20260809")

SYMBOL = "BTC/USDT:USDT"
STOP_CLIENT_ID = "A2S-9dc0d177295fb3ebf8"
TP_CLIENT_ID = "A2T-8e98c64793d42fb9ac"
STOP_ALGO_ID = "1000000160188648"
TP_ALGO_ID = "1000000160188659"
EXPECTED_EXIT_ORDER_ID = "28533281387"
ENTRY_ORDER_ID = "28531748285"

out: dict[str, object] = {
    "generated_at_utc": dt.datetime.now(dt.UTC).isoformat(),
    "symbol": SYMBOL,
    "note": "read-only; algo ids resolved via fetch_fills/actualOrderId, never fetch_order(algo_id)",
}

print("=" * 78)
print("U1-A EXCHANGE TRUTH SNAPSHOT (READ-ONLY, BINANCE TESTNET)")
print("=" * 78)

adapter = BinanceTestnetAdapter(execution_mode=V2ExecutionMode.BINANCE_TESTNET)

# --------------------------------------------------------- 1. account snapshot
snap = adapter.fetch_authoritative_snapshot()
positions = [
    {
        "symbol": p.symbol,
        "direction": p.direction,
        "quantity": str(p.quantity),
        "entry_price": str(p.entry_price),
        "mark_price": str(p.mark_price),
        "unrealized_pnl": str(p.unrealized_pnl),
    }
    for p in snap.positions
]
orders = [
    {
        "symbol": o.symbol,
        "exchange_order_id": o.exchange_order_id,
        "client_order_id": o.client_order_id,
    }
    for o in snap.pending_orders
]
out["exchange_positions"] = positions
out["exchange_open_orders"] = orders
print("\n--- exchange positions")
for p in positions:
    print("   ", json.dumps(p, ensure_ascii=False))
print("--- exchange open orders")
for o in orders:
    print("   ", json.dumps(o, ensure_ascii=False))

btc_open = [p for p in positions if p["symbol"].startswith("BTC/USDT")]
out["btc_exchange_flat"] = not btc_open
print(f"\n    BTC exchange position rows = {len(btc_open)}  -> flat = {not btc_open}")

btc_orders = [o for o in orders if o["symbol"].startswith("BTC/USDT")]
out["btc_leftover_protection_orders"] = btc_orders
print(f"    BTC leftover open/protection orders = {len(btc_orders)}")

eth = [p for p in positions if p["symbol"].startswith("ETH/USDT")]
out["eth_external_position"] = eth
print(f"    ETH external position preserved = {bool(eth)} -> {eth}")


# -------------------------------------------- 2. resolve each protection leg
def fills_for(label: str, order_ref: str) -> list[dict[str, object]]:
    print(f"\n--- fetch_fills({SYMBOL}, {order_ref})   [{label}]")
    try:
        receipts = adapter.fetch_fills(SYMBOL, order_ref)
    except Exception as exc:  # noqa: BLE001
        print(f"    ERROR: {exc}")
        return []
    rows = [
        {
            "exchange_order_id": r.exchange_order_id,
            "trade_id": r.trade_id,
            "filled_quantity": str(r.filled_quantity),
            "fill_price": str(r.fill_price),
            "fee": str(r.fee),
            "fill_timestamp": r.fill_timestamp.isoformat() if r.fill_timestamp else None,
        }
        for r in receipts
    ]
    if not rows:
        print("    (no fills)")
    for r in rows:
        print("   ", json.dumps(r, ensure_ascii=False))
    return rows


stop_fills = fills_for("STOP leg via algo id", STOP_ALGO_ID)
tp_fills = fills_for("TP leg via algo id", TP_ALGO_ID)
out["stop_leg_fills"] = stop_fills
out["tp_leg_fills"] = tp_fills


# ------------------------------------------- 3. clientOrderId resolution proof
def by_client(label: str, client_id: str) -> dict[str, object] | None:
    print(f"\n--- query_order_by_client_id({client_id})   [{label}]")
    try:
        rec = adapter.query_order_by_client_id(SYMBOL, client_id)
    except Exception as exc:  # noqa: BLE001
        print(f"    ERROR: {exc}")
        return None
    if rec is None:
        print("    -> None (purged from queryable history)")
        return None
    d = {
        "exchange_order_id": getattr(rec, "exchange_order_id", None),
        "client_order_id": getattr(rec, "client_order_id", None),
        "status": getattr(rec, "status", None),
    }
    print("   ", json.dumps(d, default=str, ensure_ascii=False))
    return d


out["stop_by_client_id"] = by_client("STOP", STOP_CLIENT_ID)
out["tp_by_client_id"] = by_client("TP", TP_CLIENT_ID)

# ------------------------------------------------- 4. raw trade history window
print("\n--- raw fetch_my_trades window around the exit")
raw: list[dict[str, object]] = []
try:
    client = adapter._ensure_gateway()  # noqa: SLF001  read-only diagnostic
    since = int(dt.datetime(2026, 8, 8, 10, 0, tzinfo=dt.UTC).timestamp() * 1000)
    trades = client.fetch_my_trades(SYMBOL, since=since, limit=50)
    for t in trades:
        raw.append(
            {
                "order": str(t.get("order")),
                "id": str(t.get("id")),
                "side": t.get("side"),
                "amount": str(t.get("amount")),
                "price": str(t.get("price")),
                "fee": str((t.get("fee") or {}).get("cost")),
                "datetime": t.get("datetime"),
                "reduceOnly": (t.get("info") or {}).get("reduceOnly"),
                "realizedPnl": (t.get("info") or {}).get("realizedPnl"),
            }
        )
except Exception as exc:  # noqa: BLE001
    print(f"    ERROR: {exc}")
out["raw_trades"] = raw
for r in raw:
    print("   ", json.dumps(r, ensure_ascii=False))

# --------------------------------------------------------------- 5. assertions
print("\n" + "=" * 78)
print("U1-A ASSERTIONS")
print("=" * 78)
checks: list[str] = []
exit_rows = [r for r in raw if r["order"] == EXPECTED_EXIT_ORDER_ID]
entry_rows = [r for r in raw if r["order"] == ENTRY_ORDER_ID]
out["exit_trades_for_expected_order"] = exit_rows
out["entry_trades_for_expected_order"] = entry_rows

checks.append(f"exit order {EXPECTED_EXIT_ORDER_ID} trade rows = {len(exit_rows)}")
checks.append(f"entry order {ENTRY_ORDER_ID} trade rows = {len(entry_rows)}")
checks.append(f"BTC exchange flat = {not btc_open}")
checks.append(f"BTC leftover protection orders = {len(btc_orders)} (expect 0)")
checks.append(f"ETH external short preserved = {bool(eth)}")
checks.append(f"stop leg resolved fills via algo id = {len(stop_fills)}")
for c in checks:
    print("   *", c)
out["checks"] = checks

if exit_rows and entry_rows:
    e = entry_rows[0]
    x = exit_rows[0]
    qty = float(x["amount"])
    gross = (float(x["price"]) - float(e["price"])) * qty
    fees = float(e["fee"]) + float(x["fee"])
    out["pnl_recomputed"] = {
        "entry_price": e["price"],
        "exit_price": x["price"],
        "quantity": str(qty),
        "gross_pnl": round(gross, 8),
        "entry_fee": e["fee"],
        "exit_fee": x["fee"],
        "total_fees": round(fees, 8),
        "net_pnl": round(gross - fees, 8),
        "exchange_realized_pnl_field": x["realizedPnl"],
    }
    print("\n--- PnL recomputed from real exchange fills (long)")
    print("   ", json.dumps(out["pnl_recomputed"], ensure_ascii=False))

ART.mkdir(parents=True, exist_ok=True)
dest = ART / "U1A_EXCHANGE_TRUTH_SNAPSHOT.json"
dest.write_text(json.dumps(out, indent=2, default=str, ensure_ascii=False), encoding="utf-8")
print(f"\nwrote {dest}")
