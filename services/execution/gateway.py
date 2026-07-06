"""Gateway abstractions and first real Binance USDT perpetual connector."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from apps.api.config import settings
from shared.models import ExchangeAccountSnapshot, ExchangeGatewayCapability, ExecutionOrderRequest


class ExchangeGateway(Protocol):
    capability: ExchangeGatewayCapability

    def sync_account(self, *, live_run_id: str) -> ExchangeAccountSnapshot: ...

    def submit_order(self, *, live_run_id: str, order_request: ExecutionOrderRequest) -> dict[str, Any]: ...

    def cancel_order(self, *, gateway_order_id: str) -> dict[str, Any]: ...

    def reconcile(self, *, live_run_id: str) -> dict[str, Any]: ...

    def set_leverage(self, *, symbol: str, leverage: float) -> dict[str, Any]: ...


@dataclass
class NullExchangeGateway:
    capability: ExchangeGatewayCapability = field(
        default_factory=lambda: ExchangeGatewayCapability(
            gateway_name="null_gateway",
            exchange="binance",
            market_type="usdt_perpetual",
            supports_account_sync=False,
            supports_positions_sync=False,
            supports_order_submit=False,
            supports_order_cancel=False,
            supports_reconciliation=False,
        )
    )

    def sync_account(self, *, live_run_id: str) -> ExchangeAccountSnapshot:
        raise NotImplementedError("live gateway is not configured")

    def submit_order(self, *, live_run_id: str, order_request: ExecutionOrderRequest) -> dict[str, Any]:
        raise NotImplementedError("live gateway is not configured")

    def cancel_order(self, *, gateway_order_id: str) -> dict[str, Any]:
        raise NotImplementedError("live gateway is not configured")

    def reconcile(self, *, live_run_id: str) -> dict[str, Any]:
        raise NotImplementedError("live gateway is not configured")

    def set_leverage(self, *, symbol: str, leverage: float) -> dict[str, Any]:
        raise NotImplementedError("live gateway is not configured")


class BinanceUsdtPerpetualGateway:
    capability = ExchangeGatewayCapability(
        gateway_name="binance_usdt_perpetual",
        exchange="binance",
        market_type="usdt_perpetual",
        supports_account_sync=True,
        supports_positions_sync=True,
        supports_order_submit=True,
        supports_order_cancel=True,
        supports_reconciliation=True,
    )

    def __init__(self, *, client: Any | None = None, use_testnet: bool | None = None) -> None:
        self.use_testnet = settings.binance_use_testnet if use_testnet is None else use_testnet
        self.client = client or self._build_default_client()
        self._configure_client(self.client, use_testnet=self.use_testnet)

    def sync_account(self, *, live_run_id: str) -> ExchangeAccountSnapshot:
        balance = self.client.fetch_balance(params={"type": "future"})
        positions = list(self.client.fetch_positions())
        info = balance.get("info", {})
        return ExchangeAccountSnapshot(
            live_run_id=live_run_id,
            exchange="binance",
            wallet_balance=float(balance.get("total", {}).get("USDT", 0.0)),
            available_balance=float(balance.get("free", {}).get("USDT", 0.0)),
            margin_balance=float(info.get("totalMarginBalance", balance.get("total", {}).get("USDT", 0.0))),
            unrealized_pnl=float(info.get("totalUnrealizedProfit", 0.0)),
            open_position_count=len([position for position in positions if _position_open(position)]),
            source_ref=self.capability.gateway_name,
        )

    def submit_order(self, *, live_run_id: str, order_request: ExecutionOrderRequest) -> dict[str, Any]:
        quantity = float(order_request.entry_context.get("quantity", 0.0))
        if quantity <= 0:
            raise ValueError("live gateway order requires positive entry_context.quantity")
        side = "buy" if str(order_request.direction).lower() == "long" else "sell"
        order_type = str(order_request.entry_context.get("order_type", "market"))
        params: dict[str, Any] = {"positionSide": "BOTH"}
        if "price" in order_request.stoploss_plan:
            params["stopLoss"] = {"triggerPrice": order_request.stoploss_plan["price"]}
        if "price" in order_request.takeprofit_plan:
            params["takeProfit"] = {"triggerPrice": order_request.takeprofit_plan["price"]}
        created = self.client.create_order(
            _normalize_binance_symbol(order_request.symbol),
            order_type,
            side,
            quantity,
            order_request.entry_context.get("limit_price"),
            params,
        )
        protection_refs = self._submit_protection_algo_orders(order_request=order_request, side=side, quantity=quantity)
        return {
            "live_run_id": live_run_id,
            "gateway_order_id": str(created.get("id")),
            "gateway_status": _normalize_order_status(created.get("status")),
            "symbol": order_request.symbol,
            "protection_order_refs": protection_refs,
        }

    def cancel_order(self, *, gateway_order_id: str) -> dict[str, Any]:
        cancelled = self.client.cancel_order(gateway_order_id)
        return {
            "gateway_order_id": str(cancelled.get("id", gateway_order_id)),
            "gateway_status": _normalize_order_status(cancelled.get("status", "canceled")),
        }

    def reconcile(self, *, live_run_id: str) -> dict[str, Any]:
        open_orders = list(self.client.fetch_open_orders())
        positions = list(self.client.fetch_positions())
        mismatches = [position for position in positions if not _position_open(position)]
        return {
            "live_run_id": live_run_id,
            "reconciliation_status": "warning" if mismatches else "ok",
            "open_order_count": len(open_orders),
            "position_mismatches": mismatches,
            "notes": ["binance gateway reconciliation snapshot"],
        }

    def set_leverage(self, *, symbol: str, leverage: float) -> dict[str, Any]:
        normalized_symbol = _normalize_binance_symbol(symbol)
        method = getattr(self.client, "set_leverage", None)
        if callable(method):
            result = method(int(leverage), normalized_symbol)
        else:
            raw_method = getattr(self.client, "fapiPrivatePostLeverage", None)
            if not callable(raw_method):
                raise ValueError("binance gateway client does not support leverage adjustment")
            result = raw_method({"symbol": _binance_market_id(normalized_symbol), "leverage": int(leverage)})
        return {
            "symbol": symbol,
            "leverage": leverage,
            "gateway_status": "acknowledged",
            "raw": result,
        }

    def _submit_protection_algo_orders(
        self,
        *,
        order_request: ExecutionOrderRequest,
        side: str,
        quantity: float,
    ) -> list[dict[str, Any]]:
        method = getattr(self.client, "fapiPrivatePostAlgoOrder", None)
        if not callable(method):
            return []
        close_side = "SELL" if side == "buy" else "BUY"
        refs: list[dict[str, Any]] = []
        symbol = _binance_market_id(_normalize_binance_symbol(order_request.symbol))
        if "price" in order_request.stoploss_plan:
            refs.append(
                method(
                    {
                        "symbol": symbol,
                        "side": close_side,
                        "type": "STOP_MARKET",
                        "quantity": quantity,
                        "triggerPrice": order_request.stoploss_plan["price"],
                        "reduceOnly": "true",
                    }
                )
            )
        if "price" in order_request.takeprofit_plan:
            refs.append(
                method(
                    {
                        "symbol": symbol,
                        "side": close_side,
                        "type": "TAKE_PROFIT_MARKET",
                        "quantity": quantity,
                        "triggerPrice": order_request.takeprofit_plan["price"],
                        "reduceOnly": "true",
                    }
                )
            )
        return refs

    @staticmethod
    def _build_default_client() -> Any:
        if not settings.binance_api_key or not settings.binance_api_secret:
            raise ValueError("binance_api_key/binance_api_secret are required for live gateway")
        import ccxt  # imported lazily so tests do not require runtime credentials

        client = ccxt.binanceusdm(
            {
                "apiKey": settings.binance_api_key,
                "secret": settings.binance_api_secret,
                "enableRateLimit": True,
                "options": {"defaultType": "future"},
            }
        )
        return client

    @staticmethod
    def _configure_client(client: Any, *, use_testnet: bool) -> None:
        sandbox_mode = getattr(client, "set_sandbox_mode", None)
        if callable(sandbox_mode):
            sandbox_mode(bool(use_testnet))


def configured_gateways() -> list[ExchangeGateway]:
    gateways: list[ExchangeGateway] = [NullExchangeGateway()]
    if settings.binance_api_key and settings.binance_api_secret:
        try:
            gateways.insert(0, BinanceUsdtPerpetualGateway(use_testnet=settings.binance_use_testnet))
        except Exception:
            # Keep capability discovery available even if runtime client bootstrap fails.
            gateways.insert(
                0,
                BinanceUsdtPerpetualGateway(
                    client=_UnavailableBinanceClient(),
                    use_testnet=settings.binance_use_testnet,
                ),
            )
    else:
        gateways.insert(
            0,
            BinanceUsdtPerpetualGateway(
                client=_UnavailableBinanceClient(),
                use_testnet=settings.binance_use_testnet,
            ),
        )
    return gateways


class _UnavailableBinanceClient:
    def fetch_balance(self, params=None):  # noqa: ANN001
        raise ValueError("binance live credentials are not configured")

    def fetch_positions(self):
        raise ValueError("binance live credentials are not configured")

    def create_order(self, *args, **kwargs):  # noqa: ANN002, ANN003
        raise ValueError("binance live credentials are not configured")

    def cancel_order(self, *args, **kwargs):  # noqa: ANN002, ANN003
        raise ValueError("binance live credentials are not configured")

    def fetch_open_orders(self):
        raise ValueError("binance live credentials are not configured")

    def set_leverage(self, *args, **kwargs):  # noqa: ANN002, ANN003
        raise ValueError("binance live credentials are not configured")


def _normalize_binance_symbol(symbol: str) -> str:
    if symbol.endswith(":USDT"):
        return symbol
    if symbol.endswith("/USDT"):
        return f"{symbol}:USDT"
    return symbol


def _binance_market_id(symbol: str) -> str:
    return symbol.replace(":USDT", "").replace("/", "")


def _normalize_order_status(status: Any) -> str:
    mapping = {
        "open": "acknowledged",
        "closed": "filled",
        "canceled": "cancelled",
        "canceled_by_user": "cancelled",
        "rejected": "rejected",
    }
    normalized = str(status).lower()
    return mapping.get(normalized, normalized)


def _position_open(position: dict[str, Any]) -> bool:
    contracts = position.get("contracts")
    if contracts is None:
        return True
    try:
        return abs(float(contracts)) > 0
    except (TypeError, ValueError):
        return True
