"""Binance Spot Demo gateway for delta-neutral carry validation."""

from __future__ import annotations

import hashlib
from typing import Any

from shared.binance_network import binance_ccxt_config
from shared.config import settings


class BinanceSpotTestnetGateway:
    def __init__(self, *, client: Any | None = None) -> None:
        self.client = client or self._build_default_client()
        self._baseline_balances: dict[str, float] = {}
        self._assert_withdrawal_disabled()

    def _assert_withdrawal_disabled(self) -> None:
        balance = self.client.fetch_balance()
        info = balance.get("info", {}) if isinstance(balance, dict) else {}
        can_withdraw = info.get("canWithdraw") if isinstance(info, dict) else None
        if can_withdraw is True or str(can_withdraw).lower() in {"true", "1", "yes"}:
            raise ValueError("Binance Spot Testnet API key withdrawal permission must be disabled")

    def preflight(self) -> dict[str, list[Any]]:
        return {"open_orders": list(self.client.fetch_open_orders()), "open_positions": []}

    def submit_market_order(
        self,
        *,
        symbol: str,
        side: str,
        notional_usdt: float,
        quantity: float | None,
        idempotency_key: str,
    ) -> dict[str, Any]:
        normalized = symbol.replace(":USDT", "")
        base_asset = normalized.split("/", 1)[0]
        if normalized not in self._baseline_balances:
            self._baseline_balances[normalized] = self._asset_balance(base_asset)
        if quantity is None:
            ticker = self.client.fetch_ticker(normalized)
            price = float(ticker.get("last") or ticker.get("close") or 0)
            if price <= 0:
                raise ValueError(f"unable to resolve positive Spot Testnet price for {normalized}")
            quantity = notional_usdt / price
        precision = getattr(self.client, "amount_to_precision", None)
        if callable(precision):
            quantity = float(precision(normalized, quantity))
        if quantity <= 0:
            raise ValueError("Spot Testnet order quantity must be positive")
        created = self.client.create_order(
            normalized,
            "market",
            side,
            quantity,
            None,
            {"newClientOrderId": _spot_client_order_id(idempotency_key)},
        )
        raw_status = str(created.get("status", "unknown")).lower()
        return {
            "gateway_order_id": str(created.get("id") or created.get("orderId") or ""),
            "gateway_status": "filled" if raw_status in {"closed", "filled"} else raw_status,
            "symbol": normalized,
            "side": side,
            "quantity": quantity,
            "notional_usdt": notional_usdt,
        }

    def final_state(self) -> dict[str, list[Any]]:
        residuals: list[str] = []
        for symbol, baseline in self._baseline_balances.items():
            current = self._asset_balance(symbol.split("/", 1)[0])
            if abs(current - baseline) > 1e-8:
                residuals.append(symbol)
        return {"open_orders": list(self.client.fetch_open_orders()), "open_positions": residuals}

    def _asset_balance(self, asset: str) -> float:
        balance = self.client.fetch_balance()
        total = balance.get("total", {}) if isinstance(balance, dict) else {}
        return float(total.get(asset, 0.0) or 0.0)

    @staticmethod
    def _build_default_client() -> Any:
        api_key, api_secret = _spot_demo_credentials()
        if not api_key or not api_secret:
            raise ValueError(
                "Binance Spot Demo requires BINANCE_API_KEY/BINANCE_API_SECRET or an explicit "
                "SPOT_TESTNET_API_KEY/SPOT_TESTNET_API_SECRET override"
            )
        import ccxt

        client = ccxt.binance(
            binance_ccxt_config(
                {
                    "apiKey": api_key,
                    "secret": api_secret,
                    "enableRateLimit": True,
                    "options": {"defaultType": "spot", "adjustForTimeDifference": True},
                }
            )
        )
        enable_demo = getattr(client, "enable_demo_trading", None)
        if callable(enable_demo):
            enable_demo(True)
        else:  # pragma: no cover - retained for older CCXT versions only
            client.set_sandbox_mode(True)
        load_time_difference = getattr(client, "load_time_difference", None)
        if callable(load_time_difference):
            load_time_difference()
        return client


def spot_demo_credentials_configured() -> bool:
    api_key, api_secret = _spot_demo_credentials()
    return bool(api_key and api_secret)


def _spot_demo_credentials() -> tuple[str, str]:
    """Prefer an explicit Spot pair, otherwise reuse the Binance Demo pair."""
    if settings.spot_testnet_api_key or settings.spot_testnet_api_secret:
        return settings.spot_testnet_api_key, settings.spot_testnet_api_secret
    return settings.binance_api_key, settings.binance_api_secret


def _spot_client_order_id(idempotency_key: str) -> str:
    digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:20]
    return f"aqs-{digest}"
