"""Read-only smoke for Binance simulation mirror path gates.

Does not place mainnet orders, does not mutate strategy enable flags.
Exit non-zero only when live trading is accidentally enabled.
"""

from __future__ import annotations

import json
import sys

from shared.config import settings


def main() -> int:
    report = {
        "binance_auto_execute": bool(settings.binance_auto_execute),
        "binance_use_testnet": bool(settings.binance_use_testnet),
        "live_trading_enabled": bool(settings.live_trading_enabled),
        "credentials_configured": bool(settings.binance_api_key and settings.binance_api_secret),
        "mirror_path_ready": False,
        "probe": None,
        "notes": [
            "Top20 layered/OOS gates remain failed; strategy auto-enable must stay unchanged.",
            "This smoke only validates configuration + optional account probe.",
        ],
    }
    if settings.live_trading_enabled:
        report["error"] = "live_trading_enabled must be false for simulation smoke"
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 2

    report["mirror_path_ready"] = (
        bool(settings.binance_auto_execute)
        and bool(settings.binance_use_testnet)
        and not bool(settings.live_trading_enabled)
        and report["credentials_configured"]
    )

    if report["credentials_configured"] and settings.binance_use_testnet:
        try:
            from services.execution.gateway import probe_testnet_account

            status = probe_testnet_account(order_limit=5)
            report["probe"] = {
                "connected": bool(status.connected),
                "trading_mode": status.trading_mode,
                "error": status.error,
                "position_count": len(status.positions or []),
                "order_count": len(status.recent_orders or []),
                "api_backend": status.api_backend,
            }
        except Exception as exc:  # noqa: BLE001
            report["probe"] = {"connected": False, "error": str(exc)}

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
