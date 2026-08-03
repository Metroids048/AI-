#!/usr/bin/env python3
"""直接查Binance testnet的挂单列表，核实那笔8.363 ETH限价单的真实client_order_id"""

import sys

sys.path.insert(0, ".")

from services.execution.gateway import configured_gateways

gateways = configured_gateways()
gateway = gateways[0] if gateways else None

if gateway is None:
    print("没有配置的gateway")
    sys.exit(1)

snapshot = gateway.reconcile(live_run_id="paper-testnet:manual-inspect")
open_orders = snapshot.get("open_orders", [])

print(f"共 {len(open_orders)} 个挂单\n")
for o in open_orders:
    info = o.get("info", {}) if isinstance(o, dict) else {}
    symbol = o.get("symbol")
    side = o.get("side")
    amount = o.get("amount")
    price = o.get("price")
    stop_price = o.get("stopPrice") or info.get("stopPrice")
    client_order_id = o.get("clientOrderId") or info.get("clientOrderId")
    order_type = o.get("type")
    reduce_only = info.get("reduceOnly")

    print(
        f"symbol={symbol} side={side} type={order_type} amount={amount} price={price} "
        f"stopPrice={stop_price} reduceOnly={reduce_only}"
    )
    print(f"  clientOrderId={client_order_id!r}")
    print()
