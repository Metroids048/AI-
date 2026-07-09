"""Real Binance Mock/Testnet open + close smoke — orders must appear on exchange."""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

ENV_PATH = ROOT / ".env"
REPORT_PATH = ROOT / "scripts" / "_testnet_open_close_report.json"


def _load_dotenv() -> None:
    if not ENV_PATH.exists():
        return
    for raw in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def main() -> int:
    _load_dotenv()
    os.environ.setdefault("BINANCE_USE_TESTNET", "true")
    os.environ.setdefault("LIVE_TRADING_ENABLED", "false")

    from services.execution.gateway import BinanceUsdtPerpetualGateway, probe_testnet_account
    from shared.models import ExecutionOrderRequest

    run_id = f"testnet-open-close-{int(time.time())}"
    symbol = "BTC/USDT"
    quantity = 0.001

    report: dict = {
        "run_id": run_id,
        "started_at": datetime.now(UTC).isoformat(),
        "safety": {
            "live_trading_enabled": os.environ.get("LIVE_TRADING_ENABLED", "false"),
            "binance_use_testnet": os.environ.get("BINANCE_USE_TESTNET", "true"),
            "quantity": quantity,
            "symbol": symbol,
        },
    }

    before = probe_testnet_account(order_limit=8)
    report["before"] = before.model_dump(mode="json")
    if not before.connected:
        print(f"ERROR: Binance not connected: {before.error}")
        REPORT_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        return 1

    gateway = BinanceUsdtPerpetualGateway(use_testnet=True)
    client = gateway.client
    ticker = client.fetch_ticker("BTC/USDT:USDT")
    ref_price = float(ticker.get("last") or ticker.get("close") or 0)
    if ref_price <= 0:
        print("ERROR: could not fetch BTC reference price")
        return 1

    print(f"Opening BTC long {quantity} @ ~{ref_price:.2f} USDT ...")
    open_req = ExecutionOrderRequest(
        strategy_id="testnet-smoke",
        symbol=symbol,
        direction="long",
        entry_context={
            "order_type": "market",
            "quantity": quantity,
            "reference_price": ref_price,
            "requested_notional": quantity * ref_price,
            "requested_leverage": 2,
        },
        stoploss_plan={},
        takeprofit_plan={},
    )
    open_result = gateway.submit_order(live_run_id=run_id, order_request=open_req)
    report["open_order"] = open_result
    print(f"OPEN filled: gateway_order_id={open_result.get('gateway_order_id')}")

    time.sleep(2)
    positions = [p for p in client.fetch_positions() if abs(float(p.get("contracts") or 0)) > 0]
    btc_pos = next((p for p in positions if "BTC" in str(p.get("symbol", ""))), None)
    if btc_pos is None:
        print("WARN: no open BTC position visible after open order")

    print(f"Closing BTC position {quantity} reduce-only ...")
    close_req = ExecutionOrderRequest(
        strategy_id="testnet-smoke",
        symbol=symbol,
        direction="long",
        entry_context={
            "order_type": "market",
            "quantity": quantity,
            "reference_price": ref_price,
            "close_only_mode": True,
            "reduce_only": True,
        },
        stoploss_plan={},
        takeprofit_plan={},
    )
    close_result = gateway.submit_order(live_run_id=run_id, order_request=close_req)
    report["close_order"] = close_result
    print(f"CLOSE filled: gateway_order_id={close_result.get('gateway_order_id')}")

    time.sleep(2)
    after = probe_testnet_account(order_limit=8)
    report["after"] = after.model_dump(mode="json")
    report["finished_at"] = datetime.now(UTC).isoformat()

    open_id = str(open_result.get("gateway_order_id", ""))
    close_id = str(close_result.get("gateway_order_id", ""))
    recent_ids = {o.order_id for o in after.recent_orders}
    verified = open_id in recent_ids and close_id in recent_ids
    report["verified_on_exchange"] = verified

    REPORT_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nReport: {REPORT_PATH}")
    print(f"Verified on Binance recent orders: {verified}")
    if after.recent_orders:
        print("Latest exchange orders:")
        for order in after.recent_orders[:4]:
            print(f"  {order.order_id} {order.side} {order.quantity} {order.status} @ {order.avg_price}")

    if not verified:
        print("ERROR: open/close order ids not found in recent exchange orders")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
