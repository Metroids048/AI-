"""Gateway abstractions and first real Binance USDT perpetual connector."""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from shared.binance_network import binance_ccxt_config
from shared.config import settings
from shared.models import ExchangeAccountSnapshot, ExchangeGatewayCapability, ExecutionOrderRequest
from shared.models.execution_runtime import (
    BinanceTestnetAccountStatus,
    BinanceTestnetOrderView,
    BinanceTestnetPositionView,
)


class ExchangeGateway(Protocol):
    capability: ExchangeGatewayCapability

    def sync_account(self, *, live_run_id: str) -> ExchangeAccountSnapshot: ...

    def submit_order(self, *, live_run_id: str, order_request: ExecutionOrderRequest) -> dict[str, Any]: ...

    def cancel_order(self, *, gateway_order_id: str) -> dict[str, Any]: ...

    def reconcile(self, *, live_run_id: str) -> dict[str, Any]: ...

    def set_leverage(self, *, symbol: str, leverage: float) -> dict[str, Any]: ...


def _resolve_gateway_quantity(*, client: Any, symbol: str, order_request: ExecutionOrderRequest) -> float:
    context = order_request.entry_context
    min_notional = float(context.get("min_notional_usdt", 50.0))
    reference_price = float(context.get("reference_price") or context.get("limit_price") or 0)
    quantity = float(context.get("quantity") or 0.0)
    requested_notional = float(context.get("requested_notional") or 0.0)
    if quantity <= 0 and reference_price > 0:
        target_notional = max(requested_notional, min_notional)
        quantity = target_notional / reference_price
    if reference_price > 0 and quantity * reference_price < min_notional:
        quantity = min_notional / reference_price
    load_markets = getattr(client, "load_markets", None)
    if callable(load_markets) and not getattr(client, "markets", None):
        load_markets()
    amount_to_precision = getattr(client, "amount_to_precision", None)
    if callable(amount_to_precision):
        quantity = float(amount_to_precision(symbol, quantity))
    step = 0.001
    market_fn = getattr(client, "market", None)
    if callable(market_fn):
        try:
            market = market_fn(symbol)
            step = float(((market.get("limits") or {}).get("amount") or {}).get("min") or step)
        except Exception:
            pass
    if reference_price > 0:
        while quantity > 0 and quantity * reference_price < min_notional:
            quantity += step
            if callable(amount_to_precision):
                quantity = float(amount_to_precision(symbol, quantity))
    if quantity <= 0:
        raise ValueError("live gateway order requires positive entry_context.quantity")
    return quantity


def _gateway_order_side(*, direction: str, close_only: bool) -> str:
    if direction == "long":
        return "sell" if close_only else "buy"
    return "buy" if close_only else "sell"


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
        self.api_backend = "disabled"
        self.client = client or self._build_default_client()
        if self.use_testnet:
            self.api_backend = configure_binance_paper_client(self.client)

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
        symbol = _normalize_binance_symbol(order_request.symbol)
        if not gateway_symbol_available(gateway=self, symbol=order_request.symbol):
            raise ValueError(f"symbol_not_found: {order_request.symbol}")
        requested_leverage = float(order_request.entry_context.get("requested_leverage") or 0)
        if requested_leverage >= 1:
            with contextlib.suppress(Exception):
                self.set_leverage(symbol=order_request.symbol, leverage=requested_leverage)
        quantity = _resolve_gateway_quantity(client=self.client, symbol=symbol, order_request=order_request)
        close_only = bool(
            order_request.entry_context.get("close_only_mode") or order_request.entry_context.get("reduce_only")
        )
        direction = str(order_request.direction).lower()
        side = _gateway_order_side(direction=direction, close_only=close_only)
        order_type = str(order_request.entry_context.get("order_type", "market"))
        params: dict[str, Any] = {"positionSide": "BOTH"}
        if close_only:
            params["reduceOnly"] = True
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
        protection_refs = self._submit_protection_algo_orders(
            order_request=order_request,
            side=side,
            quantity=quantity,
        )
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
        refs: list[dict[str, Any] | None] = []
        symbol = _binance_market_id(_normalize_binance_symbol(order_request.symbol))
        if "price" in order_request.stoploss_plan:
            refs.append(
                self._submit_algo_order(
                    method,
                    {
                        "algoType": "CONDITIONAL",
                        "symbol": symbol,
                        "side": close_side,
                        "type": "STOP_MARKET",
                        "quantity": quantity,
                        "triggerPrice": order_request.stoploss_plan["price"],
                        "reduceOnly": "true",
                    },
                )
            )
        if "price" in order_request.takeprofit_plan:
            refs.append(
                self._submit_algo_order(
                    method,
                    {
                        "algoType": "CONDITIONAL",
                        "symbol": symbol,
                        "side": close_side,
                        "type": "TAKE_PROFIT_MARKET",
                        "quantity": quantity,
                        "triggerPrice": order_request.takeprofit_plan["price"],
                        "reduceOnly": "true",
                    },
                )
            )
        return [ref for ref in refs if ref is not None]

    @staticmethod
    def _submit_algo_order(method: Any, payload: dict[str, Any]) -> dict[str, Any] | None:
        try:
            return method(payload)
        except Exception:
            # ponytail: optional protection mirror; primary entry order already submitted.
            return None

    @staticmethod
    def _build_default_client() -> Any:
        if not settings.binance_api_key or not settings.binance_api_secret:
            raise ValueError("binance_api_key/binance_api_secret are required for live gateway")
        import ccxt  # imported lazily so tests do not require runtime credentials

        client = ccxt.binanceusdm(
            binance_ccxt_config(
                {
                    "apiKey": settings.binance_api_key,
                    "secret": settings.binance_api_secret,
                    "enableRateLimit": True,
                    "options": {
                        "adjustForTimeDifference": True,
                        "defaultType": "future",
                        "fetchCurrencies": False,
                    },
                }
            )
        )
        return client

MOCK_TRADING_WEB_URL = "https://demo.binance.com/en/futures/BTCUSDT"


def _apply_legacy_testnet_api(client: Any) -> None:
    urls = getattr(client, "urls", None)
    test_urls = urls.get("test") if isinstance(urls, dict) else None
    clone = getattr(client, "clone", None)
    if test_urls and callable(clone):
        client.urls["api"] = clone(test_urls)


def _sync_binance_time_difference(client: Any) -> None:
    load_time_difference = getattr(client, "load_time_difference", None)
    if callable(load_time_difference):
        with contextlib.suppress(Exception):
            load_time_difference()


def configure_binance_paper_client(client: Any) -> str:
    """Configure CCXT for Binance Mock Trading; fall back to legacy testnet fapi if demo API blocked."""
    options = getattr(client, "options", None)
    if isinstance(options, dict):
        options["fetchCurrencies"] = False
        options.setdefault("defaultType", "future")

    mode = (settings.binance_trading_mode or "demo").lower()
    if mode == "testnet":
        _apply_legacy_testnet_api(client)
        _sync_binance_time_difference(client)
        return "testnet"

    enable_demo = getattr(client, "enable_demo_trading", None)
    if callable(enable_demo):
        enable_demo(True)
    try:
        client.fetch_time({"type": "future"})
        _sync_binance_time_difference(client)
        return "demo"
    except Exception:
        # ponytail: demo-fapi often returns 451 in CN; legacy testnet fapi still serves same paper keys.
        urls = getattr(client, "urls", {})
        if isinstance(urls, dict) and "apiBackupDemoTrading" in urls:
            client.urls["api"] = urls["apiBackupDemoTrading"]
            omit = getattr(client, "omit", None)
            if callable(omit):
                client.urls = omit(client.urls, "apiBackupDemoTrading")
        if isinstance(options, dict):
            options["enableDemoTrading"] = False
        _apply_legacy_testnet_api(client)
        _sync_binance_time_difference(client)
        return "testnet-fallback"


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


def gateway_symbol_available(*, gateway: ExchangeGateway, symbol: str) -> bool:
    """Return False when the configured Binance gateway does not list the market."""
    client = getattr(gateway, "client", None)
    if client is None:
        return True
    normalized = _normalize_binance_symbol(symbol)
    load_markets = getattr(client, "load_markets", None)
    if callable(load_markets):
        try:
            if not getattr(client, "markets", None):
                load_markets()
        except Exception:
            return True
    market_fn = getattr(client, "market", None)
    if not callable(market_fn):
        return True
    try:
        market_fn(normalized)
    except Exception:
        return False
    return True


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


def _binance_mode_urls() -> tuple[str, str, str]:
    return (
        "demo",
        settings.binance_usdm_rest_base or "https://demo-fapi.binance.com",
        MOCK_TRADING_WEB_URL,
    )


def probe_testnet_account(*, order_limit: int = 10) -> BinanceTestnetAccountStatus:
    """Fetch live Binance Mock Trading balances, positions, and recent orders via API."""
    trading_mode, api_base, web_ui_url = _binance_mode_urls()
    warning = (
        "币安网页 Login 报 restricted countries 时，API 仍可正常交易。"
        "Mock 入口 demo.binance.com；本面板显示的就是你的模拟盘真实资金与订单。"
        "若仅 Testnet API 连不上，可在 .env 设置 BINANCE_HTTPS_PROXY（仅本程序走代理，无需全局 VPN）。"
    )
    if not settings.binance_api_key or not settings.binance_api_secret:
        return BinanceTestnetAccountStatus(
            connected=False,
            trading_mode=trading_mode,
            api_base=api_base,
            web_ui_url=web_ui_url,
            warning=warning,
            error="binance credentials not configured",
        )
    if not settings.binance_use_testnet:
        return BinanceTestnetAccountStatus(
            connected=False,
            trading_mode=trading_mode,
            api_base=api_base,
            web_ui_url=web_ui_url,
            warning=warning,
            error="binance_use_testnet is false",
        )
    try:
        gateway = BinanceUsdtPerpetualGateway(use_testnet=True)
        snapshot = gateway.sync_account(live_run_id="console_probe")
        client = gateway.client
        api_backend = getattr(gateway, "api_backend", "demo")
        if api_backend == "testnet-fallback":
            warning = (
                f"{warning} demo-fapi 在本机被墙(451)，API 已自动切到 testnet-fapi 网关，"
                "与 Mock 网页仍是同一套模拟账户。"
            )
        positions: list[BinanceTestnetPositionView] = []
        for raw in client.fetch_positions():
            contracts = float(raw.get("contracts") or 0)
            if abs(contracts) <= 0:
                continue
            mark_price = float(raw.get("markPrice") or raw.get("mark_price") or 0)
            entry_price = float(raw.get("entryPrice") or raw.get("entry_price") or 0)
            notional = float(raw.get("notional") or 0)
            if not notional and mark_price > 0:
                notional = abs(contracts) * mark_price
            positions.append(
                BinanceTestnetPositionView(
                    symbol=str(raw.get("symbol", "")),
                    side=str(raw.get("side", "long")),
                    quantity=abs(contracts),
                    entry_price=entry_price,
                    mark_price=mark_price,
                    notional_usdt=abs(notional),
                    margin_usdt=float(raw.get("initialMargin") or raw.get("collateral") or 0),
                    leverage=float(raw.get("leverage") or 0),
                    unrealized_pnl=float(raw.get("unrealizedPnl") or raw.get("unrealized_pnl") or 0),
                    liquidation_price=(
                        float(raw["liquidationPrice"])
                        if raw.get("liquidationPrice") not in (None, "", 0, "0")
                        else None
                    ),
                )
            )
        recent_orders: list[BinanceTestnetOrderView] = []
        symbols = {p.symbol.replace(":USDT", "").replace("/", "") for p in positions} or {"BTCUSDT"}
        for market_id in sorted(symbols):
            payload = client.fapiPrivateGetAllOrders({"symbol": market_id, "limit": order_limit})
            for raw in payload[-order_limit:]:
                recent_orders.append(
                    BinanceTestnetOrderView(
                        order_id=str(raw.get("orderId", "")),
                        symbol=str(raw.get("symbol", market_id)),
                        side=str(raw.get("side", "")),
                        order_type=str(raw.get("type", "")),
                        status=str(raw.get("status", "")),
                        quantity=float(raw.get("origQty") or 0),
                        avg_price=float(raw["avgPrice"]) if raw.get("avgPrice") else None,
                        update_time=int(raw["updateTime"]) if raw.get("updateTime") else None,
                    )
                )
        recent_orders.sort(key=lambda item: item.update_time or 0, reverse=True)
        status_api_base = (
            client.urls["api"].get("fapiPrivate", api_base) if isinstance(client.urls.get("api"), dict) else api_base
        )
        return BinanceTestnetAccountStatus(
            connected=True,
            trading_mode=trading_mode,
            api_base=status_api_base,
            wallet_balance=snapshot.wallet_balance,
            available_balance=snapshot.available_balance,
            unrealized_pnl=snapshot.unrealized_pnl,
            open_position_count=snapshot.open_position_count,
            positions=positions,
            recent_orders=recent_orders[:order_limit],
            web_ui_url=web_ui_url,
            api_backend=api_backend,
            synced_at=datetime.now(UTC),
            warning=warning,
        )
    except Exception as exc:  # noqa: BLE001 - surface probe errors to UI
        return BinanceTestnetAccountStatus(
            connected=False,
            trading_mode=trading_mode,
            api_base=api_base,
            web_ui_url=web_ui_url,
            warning=warning,
            error=str(exc),
        )
