#!/usr/bin/env python3
"""Read-only Binance USDT-M Testnet position evidence.

This script deliberately reuses the production gateway so it cannot silently
fall back to Binance Spot or mainnet. Failure is reported as UNAVAILABLE and
uses a non-zero exit code.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime

from services.execution.gateway import BinanceUsdtPerpetualGateway
from shared.config import settings


def collect() -> dict:
    observed_at = datetime.now(UTC).isoformat()
    if not settings.binance_use_testnet or settings.live_trading_enabled:
        raise RuntimeError("safety boundary violation: Testnet must be enabled and live trading disabled")
    gateway = BinanceUsdtPerpetualGateway()
    snapshot = gateway.reconcile(live_run_id=f"diagnostic:{observed_at}")
    if "open_positions" not in snapshot:
        raise RuntimeError("gateway response omitted open_positions")
    return {
        "status": "healthy",
        "source": "BINANCE_USDT_M_TESTNET",
        "observed_at": observed_at,
        "open_position_count": len(snapshot["open_positions"]),
        "open_order_count": int(snapshot.get("open_order_count") or 0),
        "positions": snapshot["open_positions"],
        "open_orders": snapshot.get("open_orders") or [],
        "reconciliation_status": snapshot.get("reconciliation_status"),
        "notes": snapshot.get("notes") or [],
    }


def main() -> int:
    try:
        payload = collect()
    except Exception as exc:  # noqa: BLE001 - diagnostic boundary
        payload = {
            "status": "unavailable",
            "source": "BINANCE_USDT_M_TESTNET",
            "observed_at": datetime.now(UTC).isoformat(),
            "error": str(exc),
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 2
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
