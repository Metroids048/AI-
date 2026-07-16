"""Open a few Binance Demo positions so the operator can verify both platforms."""

from __future__ import annotations

import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    from services.execution.gateway import BinanceUsdtPerpetualGateway

    gateway = BinanceUsdtPerpetualGateway(use_testnet=True)
    before = gateway.reconcile(live_run_id="operator-visible-before")
    print("BEFORE", json.dumps(before.get("open_positions"), ensure_ascii=False))

    plan = [
        ("BTC/USDT", 150.0),
        ("ETH/USDT", 100.0),
        ("SOL/USDT", 80.0),
    ]
    opened = []
    stamp = int(time.time())
    for symbol, notional in plan:
        price = gateway.fetch_last_price(symbol)
        result = gateway.submit_acceptance_order(
            symbol=symbol,
            side="BUY",
            requested_notional=notional,
            reference_price=price,
            reduce_only=False,
            stoploss_price=price * 0.985,
            idempotency_key=f"visible-open-{symbol.replace('/', '')}-{stamp}",
        )
        row = {
            "symbol": symbol,
            "price": price,
            "notional": notional,
            "gateway_order_id": result.get("gateway_order_id"),
            "gateway_status": result.get("gateway_status"),
            "quantity": result.get("quantity"),
        }
        opened.append(row)
        print("OPENED", json.dumps(row, ensure_ascii=False))

    after = gateway.reconcile(live_run_id="operator-visible-after")
    print("AFTER_POSITIONS", json.dumps(after.get("open_positions"), ensure_ascii=False))
    print("AFTER_OPEN_ORDERS", after.get("open_order_count"))
    report = ROOT / "scripts" / "_visible_binance_opens.json"
    report.write_text(json.dumps({"opened": opened, "positions": after.get("open_positions")}, ensure_ascii=False, indent=2), encoding="utf-8")
    print("REPORT", report)


if __name__ == "__main__":
    main()
