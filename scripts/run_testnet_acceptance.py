"""Run the bounded fixed-Top20 Binance Futures Testnet acceptance flow."""

from __future__ import annotations

import json
import uuid

from services.execution.gateway import BinanceUsdtPerpetualGateway
from services.execution.testnet_acceptance import TestnetAcceptanceService
from services.execution.testnet_cleanup import testnet_account_cleanup
from shared.config import settings
from shared.models import TestnetAcceptanceRunRequest


def main() -> int:
    if not settings.binance_use_testnet or settings.live_trading_enabled:
        print(json.dumps({"status": "blocked_safety_boundary"}))
        return 2
    gateway = BinanceUsdtPerpetualGateway(use_testnet=True)
    cleanup_result = testnet_account_cleanup(gateway)
    if not cleanup_result["skipped"]:
        print(
            json.dumps(
                {
                    "pre_acceptance_cleanup": "performed",
                    "cancelled_orders": cleanup_result["cancelled_orders"],
                    "closed_positions": cleanup_result["closed_positions"],
                },
                ensure_ascii=False,
            )
        )
    service = TestnetAcceptanceService(gateway=gateway)
    result = service.run(
        TestnetAcceptanceRunRequest(
            idempotency_key=f"top20-{uuid.uuid4().hex[:12]}",
            max_notional_usdt=120,
        )
    )
    output = {
        "run_status": result.run_status,
        "requested_count": len(result.requested_symbols),
        "completed_count": len(result.completed_symbols),
        "filled_order_count": result.filled_order_count,
        "failed_symbol": result.failed_symbol,
        "compensation_attempted": result.compensation_attempted,
        "final_open_position_count": result.final_open_position_count,
        "final_open_order_count": result.final_open_order_count,
        "symbols": [
            {
                "symbol": item.symbol,
                "status": item.run_status,
                "stage": item.final_stage,
                "compensation_succeeded": item.compensation_succeeded,
                "failure_class": item.failure_class,
            }
            for item in result.symbol_results
        ],
        "error_summary": result.error_summary,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if result.run_status == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
