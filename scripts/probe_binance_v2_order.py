#!/usr/bin/env python3
"""Read-only Binance Testnet lookup for a V2 client order id."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict

from services.automated_trading.domain.enums import V2ExecutionMode
from services.automated_trading.infrastructure.binance_adapter import BinanceTestnetAdapter


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--client-order-id", required=True)
    parser.add_argument("--lookup-order-id")
    args = parser.parse_args()

    adapter = BinanceTestnetAdapter(execution_mode=V2ExecutionMode.BINANCE_TESTNET)
    order = adapter.query_order_by_client_id(args.symbol, args.client_order_id)
    fills = adapter.fetch_fills(args.symbol, order.exchange_order_id) if order else ()
    snapshot = adapter.fetch_authoritative_snapshot()
    client = adapter._ensure_gateway()  # noqa: SLF001 - read-only diagnostic script
    recent_trades = client.fetch_my_trades(args.symbol, limit=50)
    get_all_algo_orders = getattr(client, "fapiPrivateGetAllAlgoOrders", None)
    algo_orders = get_all_algo_orders({"symbol": args.symbol.replace("/", "")}) if callable(get_all_algo_orders) else []
    lookup_order = client.fetch_order(args.lookup_order_id, args.symbol) if args.lookup_order_id else None
    payload = {
        "order": asdict(order) if order else None,
        "fills": [asdict(fill) for fill in fills],
        "positions": [asdict(position) for position in snapshot.positions],
        "pending_orders": [asdict(item) for item in snapshot.pending_orders],
        "recent_trades": [
            {
                "trade_id": str(item.get("id") or ""),
                "order_id": str(item.get("order") or (item.get("info") or {}).get("orderId") or ""),
                "side": item.get("side"),
                "amount": item.get("amount"),
                "price": item.get("price"),
                "fee": item.get("fee"),
                "timestamp": item.get("datetime") or item.get("timestamp"),
            }
            for item in recent_trades
        ],
        "algo_orders": [
            {
                "algo_id": str(item.get("algoId") or ""),
                "client_algo_id": item.get("clientAlgoId"),
                "actual_order_id": str(item.get("actualOrderId") or item.get("orderId") or ""),
                "status": item.get("algoStatus") or item.get("status"),
                "order_type": item.get("orderType") or item.get("type"),
                "trigger_price": item.get("triggerPrice") or item.get("stopPrice"),
                "quantity": item.get("quantity"),
            }
            for item in algo_orders or []
        ],
        "lookup_order": (
            {
                "order_id": str(lookup_order.get("id") or ""),
                "client_order_id": lookup_order.get("clientOrderId")
                or (lookup_order.get("info") or {}).get("clientOrderId"),
                "status": lookup_order.get("status"),
                "side": lookup_order.get("side"),
                "type": lookup_order.get("type"),
                "amount": lookup_order.get("amount"),
                "filled": lookup_order.get("filled"),
                "average": lookup_order.get("average"),
                "reduce_only": (lookup_order.get("info") or {}).get("reduceOnly"),
            }
            if lookup_order
            else None
        ),
        "observed_at": snapshot.snapshot_timestamp,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
