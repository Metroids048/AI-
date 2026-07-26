#!/usr/bin/env python3
"""直接用ccxt底层fetch_open_orders + fapiPrivateGetOpenAlgoOrders，拿完整raw info，
不通过我们自己封装的reconcile()（可能过滤/丢字段），核实clientOrderId的真实前缀"""

import sys

sys.path.insert(0, ".")

from services.execution.gateway import configured_gateways

gateways = configured_gateways()
gateway = gateways[0] if gateways else None

if gateway is None:
    print("没有配置的gateway")
    sys.exit(1)

client = gateway.client

print("=== 1. 普通挂单 (fetch_open_orders) ===\n")
orders = client.fetch_open_orders()
print(f"共 {len(orders)} 条\n")
for o in orders:
    info = o.get("info", {})
    print(
        f"symbol={o.get('symbol')} side={o.get('side')} type={o.get('type')} "
        f"amount={o.get('amount')} price={o.get('price')} reduceOnly={info.get('reduceOnly')}"
    )
    print(f"  raw clientOrderId={info.get('clientOrderId')!r}")
    print(f"  raw orderId={info.get('orderId')!r} time={info.get('time')!r}")
    print()

print("\n=== 2. 条件单/算法单 (fapiPrivateGetOpenAlgoOrders) ===\n")
try:
    algo_method = getattr(client, "fapiPrivateGetOpenAlgoOrders", None)
    if callable(algo_method):
        result = algo_method()
        algo_orders = result.get("orders", result) if isinstance(result, dict) else result
        print(f"共 {len(algo_orders)} 条\n")
        for o in algo_orders:
            print(
                f"symbol={o.get('symbol')} side={o.get('side')} strategyType={o.get('strategyType')} "
                f"triggerPrice={o.get('triggerPrice')} quantity={o.get('quantity')} "
                f"reduceOnly={o.get('reduceOnly')}"
            )
            print(f"  raw clientOrderId / clientStrategyId={o.get('clientAlgoId') or o.get('clientStrategyId')!r}")
            print(f"  raw strategyId={o.get('strategyId')!r} bookTime={o.get('bookTime')!r}")
            print()
    else:
        print("fapiPrivateGetOpenAlgoOrders 方法不存在")
except Exception as e:
    print(f"查询失败: {type(e).__name__}: {e}")
