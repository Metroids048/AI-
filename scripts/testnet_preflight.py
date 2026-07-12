"""Read-only Binance Testnet safety and connectivity preflight."""

from __future__ import annotations

import json
import socket
from typing import Any
from urllib.parse import urlparse

from services.execution.gateway import probe_testnet_account
from shared.config import settings


def proxy_connectivity(proxy_url: str | None, *, timeout_seconds: float = 2.0) -> dict[str, Any]:
    if not proxy_url:
        return {"configured": False, "reachable": None, "endpoint": None}
    parsed = urlparse(proxy_url)
    host = parsed.hostname
    port = parsed.port
    endpoint = f"{host}:{port}" if host and port else "invalid"
    if not host or not port:
        return {"configured": True, "reachable": False, "endpoint": endpoint, "error": "invalid_proxy_url"}
    try:
        with socket.create_connection((host, port), timeout=timeout_seconds):
            return {"configured": True, "reachable": True, "endpoint": endpoint}
    except OSError as exc:
        return {
            "configured": True,
            "reachable": False,
            "endpoint": endpoint,
            "error": type(exc).__name__,
        }


def build_preflight_report(*, probe_account: bool = True) -> dict[str, Any]:
    futures_credentials = bool(settings.binance_api_key and settings.binance_api_secret)
    spot_credentials = bool(settings.spot_testnet_api_key and settings.spot_testnet_api_secret)
    safety_boundary = settings.binance_use_testnet and not settings.live_trading_enabled
    proxy = proxy_connectivity(settings.binance_https_proxy or settings.binance_http_proxy)
    report: dict[str, Any] = {
        "safety_boundary": {
            "binance_use_testnet": settings.binance_use_testnet,
            "live_trading_enabled": settings.live_trading_enabled,
            "mainnet_disabled": safety_boundary,
        },
        "credentials": {
            "futures_configured": futures_credentials,
            "spot_configured": spot_credentials,
        },
        "proxy": proxy,
        "futures_account": {"probed": False, "connected": False},
    }
    if probe_account and safety_boundary and futures_credentials and proxy.get("reachable") is not False:
        status = probe_testnet_account(order_limit=1)
        report["futures_account"] = {
            "probed": True,
            "connected": status.connected,
            "trading_mode": status.trading_mode,
            "api_backend": status.api_backend,
            "open_position_count": status.open_position_count,
            "warning": status.warning,
            "error": status.error,
        }
    report["ready_for_futures_acceptance"] = bool(
        safety_boundary
        and futures_credentials
        and proxy.get("reachable") is not False
        and report["futures_account"].get("connected")
    )
    report["ready_for_spot_carry"] = bool(report["ready_for_futures_acceptance"] and spot_credentials)
    return report


def main() -> int:
    report = build_preflight_report()
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0 if report["ready_for_futures_acceptance"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
