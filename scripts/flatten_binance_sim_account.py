"""Flatten the configured Binance simulation account without touching mainnet."""

from __future__ import annotations

import argparse
import json
import time

from services.execution.gateway import BinanceUsdtPerpetualGateway, probe_testnet_account
from shared.config import settings
from shared.models import ExecutionOrderRequest, TradeSide


def _cancel_order(gateway: BinanceUsdtPerpetualGateway, *, symbol: str, order_id: str) -> None:
    try:
        gateway.cancel_protection_order(symbol=symbol, gateway_order_id=order_id)
        return
    except Exception:  # noqa: BLE001 - legacy conditional orders use a different endpoint
        gateway.client.cancel_order(order_id, symbol)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--confirm", action="store_true")
    args = parser.parse_args()

    if settings.live_trading_enabled or not settings.binance_use_testnet:
        raise SystemExit("refusing to run: Binance mainnet must remain disabled")

    before = probe_testnet_account(order_limit=50)
    summary = {
        "connected": before.connected,
        "trading_mode": before.trading_mode,
        "api_backend": before.api_backend,
        "positions": [position.model_dump(mode="json") for position in before.positions],
        "open_orders": [order.model_dump(mode="json") for order in before.open_orders],
    }
    print(json.dumps({"before": summary}, ensure_ascii=False, indent=2))
    if not before.connected:
        return 1
    if not args.confirm:
        print("dry-run only; pass --confirm to flatten the Binance simulation account")
        return 0

    gateway = BinanceUsdtPerpetualGateway(use_testnet=True)
    cancelled: list[str] = []
    for order in before.open_orders:
        _cancel_order(gateway, symbol=order.symbol, order_id=order.order_id)
        cancelled.append(order.order_id)

    closed: list[dict[str, object]] = []
    stamp = int(time.time())
    for position in before.positions:
        reference_price = gateway.fetch_last_price(position.symbol)
        result = gateway.submit_order(
            live_run_id="binance-simulation-flatten",
            order_request=ExecutionOrderRequest(
                strategy_id="binance_simulation_account_maintenance",
                symbol=position.symbol,
                direction=TradeSide(position.side.lower()),
                entry_context={
                    "order_type": "market",
                    "quantity": abs(position.quantity),
                    "reference_price": reference_price,
                    "close_only_mode": True,
                    "reduce_only": True,
                },
                stoploss_plan={},
                takeprofit_plan={},
                idempotency_key=f"flatten-{position.symbol}-{stamp}",
            ),
        )
        closed.append(
            {
                "symbol": position.symbol,
                "quantity": position.quantity,
                "gateway_order_id": result.get("gateway_order_id"),
                "gateway_status": result.get("gateway_status"),
            }
        )

    time.sleep(2)
    after = probe_testnet_account(order_limit=50)
    report = {
        "cancelled_order_ids": cancelled,
        "closed_positions": closed,
        "after": {
            "connected": after.connected,
            "api_backend": after.api_backend,
            "open_position_count": after.open_position_count,
            "open_order_count": len(after.open_orders),
            "positions": [position.model_dump(mode="json") for position in after.positions],
            "open_orders": [order.model_dump(mode="json") for order in after.open_orders],
        },
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if after.connected and not after.positions and not after.open_orders else 1


if __name__ == "__main__":
    raise SystemExit(main())
