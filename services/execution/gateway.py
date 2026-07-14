"""Gateway abstractions and first real Binance USDT perpetual connector."""

from __future__ import annotations

import contextlib
import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from services.data.universe import FIXED_TOP20_SYMBOLS, platform_to_exchange_symbol
from shared.binance_network import binance_ccxt_config
from shared.config import settings
from shared.models import ExchangeAccountSnapshot, ExchangeGatewayCapability, ExecutionOrderRequest, TradeSide
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
    close_only = bool(context.get("close_only_mode") or context.get("reduce_only"))
    if quantity <= 0 and reference_price > 0:
        quantity = requested_notional / reference_price
    if not close_only and reference_price > 0 and quantity * reference_price < min_notional:
        raise ValueError(f"below_min_notional: requested order is below {min_notional} USDT")
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
    if not close_only and reference_price > 0 and quantity * reference_price < min_notional:
        raise ValueError(f"below_min_notional: rounded order is below {min_notional} USDT")
    if quantity <= 0:
        raise ValueError("live gateway order requires positive entry_context.quantity")
    return quantity


def _binance_client_order_id(*, live_run_id: str, idempotency_key: str) -> str:
    digest = hashlib.sha256(f"{live_run_id}:{idempotency_key}".encode()).hexdigest()
    return f"aq-{digest[:33]}"


def _order_id(payload: dict[str, Any]) -> str:
    return str(payload.get("id") or payload.get("orderId") or "")


def _is_withdrawal_enabled(value: Any) -> bool:
    return value is True or str(value).strip().lower() in {"1", "true", "yes"}


def _gateway_order_side(*, direction: str, close_only: bool) -> str:
    if direction == "long":
        return "sell" if close_only else "buy"
    return "buy" if close_only else "sell"


def _validate_protection_prices(order_request: ExecutionOrderRequest) -> None:
    """Reject stale or nonsensical exchange protection triggers before entry submit."""

    if bool(order_request.entry_context.get("close_only_mode") or order_request.entry_context.get("reduce_only")):
        return
    reference_price = float(
        order_request.entry_context.get("gateway_reference_price")
        or order_request.entry_context.get("reference_price")
        or order_request.entry_context.get("limit_price")
        or 0
    )
    if reference_price <= 0:
        return

    max_distance = max(float(settings.gateway_protection_max_distance_bps), 0.0) / 10000.0
    direction = str(order_request.direction).lower()
    checks = (
        ("stoploss", order_request.stoploss_plan.get("price")),
        ("takeprofit", order_request.takeprofit_plan.get("price")),
    )
    for label, raw_price in checks:
        if raw_price is None:
            continue
        price = float(raw_price)
        if price <= 0:
            raise ValueError(f"invalid_{label}_price: {price}")
        if direction == "long" and label == "stoploss" and price >= reference_price:
            raise ValueError(f"invalid_stoploss_price: long stoploss {price} must be below {reference_price}")
        if direction == "long" and label == "takeprofit" and price <= reference_price:
            raise ValueError(f"invalid_takeprofit_price: long takeprofit {price} must be above {reference_price}")
        if direction == "short" and label == "stoploss" and price <= reference_price:
            raise ValueError(f"invalid_stoploss_price: short stoploss {price} must be above {reference_price}")
        if direction == "short" and label == "takeprofit" and price >= reference_price:
            raise ValueError(f"invalid_takeprofit_price: short takeprofit {price} must be below {reference_price}")
        if max_distance > 0 and abs(price - reference_price) / reference_price > max_distance:
            raise ValueError(
                f"protection_price_too_far: {label} {price} differs from reference "
                f"{reference_price} by more than {settings.gateway_protection_max_distance_bps} bps"
            )


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
        self._assert_withdrawal_disabled()

    def _assert_withdrawal_disabled(self) -> None:
        if isinstance(self.client, _UnavailableBinanceClient):
            return
        fetch_balance = getattr(self.client, "fetch_balance", None)
        if not callable(fetch_balance):
            return
        try:
            balance = fetch_balance(params={"type": "future"})
        except Exception as exc:
            raise ValueError("unable to verify Binance API key permissions") from exc
        info = balance.get("info", {}) if isinstance(balance, dict) else {}
        if isinstance(info, dict) and _is_withdrawal_enabled(info.get("canWithdraw")):
            raise ValueError("Binance API key withdrawal permission must be disabled")

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

    def preflight(self) -> dict[str, list[Any]]:
        # CCXT rejects the high-rate-limit all-symbol endpoint. The acceptance
        # workflow only trades this fixed universe, so inspect each contract.
        open_orders = [
            order
            for symbol in FIXED_TOP20_SYMBOLS
            for order in self.client.fetch_open_orders(_normalize_binance_symbol(symbol))
        ]
        return {
            "open_orders": open_orders,
            "open_positions": [position for position in self.client.fetch_positions() if _position_open(position)],
        }

    def final_state(self) -> dict[str, list[Any]]:
        return self.preflight()

    def account_equity(self) -> float:
        snapshot = self.sync_account(live_run_id="testnet-acceptance-preflight")
        return float(snapshot.margin_balance or snapshot.wallet_balance)

    def fetch_last_price(self, symbol: str) -> float:
        ticker = self.client.fetch_ticker(_normalize_binance_symbol(symbol))
        price = ticker.get("last") or ticker.get("close") or ticker.get("mark")
        if price is None or float(price) <= 0:
            raise ValueError(f"unable to resolve positive last price for {symbol}")
        return float(price)

    def submit_acceptance_order(
        self,
        *,
        symbol: str,
        side: str,
        requested_notional: float,
        reference_price: float,
        reduce_only: bool,
        stoploss_price: float | None,
        idempotency_key: str,
    ) -> dict[str, Any]:
        quantity = requested_notional / reference_price
        result = self.submit_order(
            live_run_id="testnet-acceptance",
            order_request=ExecutionOrderRequest(
                strategy_id="testnet_acceptance_only",
                symbol=symbol,
                direction=TradeSide.LONG,
                entry_context={
                    "order_type": "market",
                    "quantity": quantity,
                    "reference_price": reference_price,
                    "requested_notional": requested_notional,
                    "close_only_mode": reduce_only,
                    "reduce_only": reduce_only,
                },
                stoploss_plan={"price": stoploss_price} if stoploss_price is not None else {},
                idempotency_key=idempotency_key,
            ),
        )
        return {
            **result,
            "gateway_status": "filled"
            if result.get("gateway_status") in {"filled", "closed"}
            else result.get("gateway_status"),
            "side": side,
            "quantity": quantity,
            "requested_notional": requested_notional,
            "reduce_only": reduce_only,
        }

    def submit_carry_order(
        self,
        *,
        symbol: str,
        side: str,
        notional_usdt: float,
        quantity: float,
        reduce_only: bool,
        idempotency_key: str,
    ) -> dict[str, Any]:
        result = self.submit_order(
            live_run_id="funding-carry-testnet",
            order_request=ExecutionOrderRequest(
                strategy_id="funding_carry_dual_leg",
                symbol=symbol,
                direction=TradeSide.SHORT,
                entry_context={
                    "order_type": "market",
                    "quantity": quantity,
                    "requested_notional": notional_usdt,
                    "close_only_mode": reduce_only,
                    "reduce_only": reduce_only,
                },
                idempotency_key=idempotency_key,
            ),
        )
        return {
            **result,
            "gateway_status": "filled"
            if result.get("gateway_status") in {"filled", "closed"}
            else result.get("gateway_status"),
            "side": side,
            "quantity": quantity,
            "notional_usdt": notional_usdt,
            "reduce_only": reduce_only,
        }

    def cancel_protection_order(self, *, symbol: str, gateway_order_id: str) -> None:
        cancel_algo = getattr(self.client, "fapiPrivateDeleteAlgoOrder", None)
        if callable(cancel_algo):
            cancel_algo(
                {
                    "symbol": _binance_market_id(_normalize_binance_symbol(symbol)),
                    "algoId": gateway_order_id,
                }
            )
            return
        self.client.cancel_order(gateway_order_id, _normalize_binance_symbol(symbol))

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
        order_type = str(order_request.entry_context.get("order_type") or settings.execution_default_order_type)
        params: dict[str, Any] = {"positionSide": "BOTH"}
        if order_request.idempotency_key:
            params["newClientOrderId"] = _binance_client_order_id(
                live_run_id=live_run_id,
                idempotency_key=order_request.idempotency_key,
            )
        if close_only:
            params["reduceOnly"] = True
        _validate_protection_prices(order_request)
        if "price" in order_request.stoploss_plan:
            params["stopLoss"] = {"triggerPrice": order_request.stoploss_plan["price"]}
        if "price" in order_request.takeprofit_plan:
            params["takeProfit"] = {"triggerPrice": order_request.takeprofit_plan["price"]}
        try:
            created = self.client.create_order(
                _normalize_binance_symbol(order_request.symbol),
                order_type,
                side,
                quantity,
                order_request.entry_context.get("limit_price"),
                params,
            )
        except TimeoutError:
            client_order_id = params.get("newClientOrderId")
            lookup = getattr(self.client, "fapiPrivateGetOrder", None)
            if not client_order_id or not callable(lookup):
                raise
            created = lookup(
                {
                    "symbol": _binance_market_id(symbol),
                    "origClientOrderId": client_order_id,
                }
            )
        protection_refs: list[dict[str, Any]] = []
        if not close_only:
            protection_refs = self._submit_protection_algo_orders(
                order_request=order_request,
                side=side,
                quantity=quantity,
            )
        return {
            "live_run_id": live_run_id,
            "gateway_order_id": _order_id(created),
            "gateway_status": _normalize_order_status(created.get("status")),
            "symbol": order_request.symbol,
            "quantity": quantity,
            "requested_notional": float(order_request.entry_context.get("requested_notional") or 0.0),
            "reduce_only": close_only,
            "protection_order_refs": protection_refs,
        }

    def cancel_order(self, *, gateway_order_id: str) -> dict[str, Any]:
        cancelled = self.client.cancel_order(gateway_order_id)
        return {
            "gateway_order_id": str(cancelled.get("id", gateway_order_id)),
            "gateway_status": _normalize_order_status(cancelled.get("status", "canceled")),
        }

    def reconcile(self, *, live_run_id: str) -> dict[str, Any]:
        options = getattr(self.client, "options", None)
        if isinstance(options, dict):
            options["warnOnFetchOpenOrdersWithoutSymbol"] = False
        open_orders: list[Any] = []
        open_orders_error: str | None = None
        try:
            open_orders = list(self.client.fetch_open_orders())
        except Exception as exc:  # noqa: BLE001
            # Position flatness is what clears local ghosts; do not fail closed on
            # open-order scan warnings/rate-limit advisories from CCXT.
            open_orders_error = str(exc)
        positions = list(self.client.fetch_positions())
        open_positions = [position for position in positions if _position_open(position)]
        mismatches = [position for position in positions if not _position_open(position)]
        notes = ["binance gateway reconciliation snapshot"]
        if open_orders_error:
            notes.append(f"open_orders_scan_failed:{open_orders_error}")
        return {
            "live_run_id": live_run_id,
            "reconciliation_status": "warning" if mismatches or open_orders_error else "ok",
            "open_order_count": len(open_orders),
            "position_mismatches": mismatches,
            "open_positions": [
                {
                    "symbol": str(position.get("symbol") or ""),
                    "contracts": float(position.get("contracts") or 0.0),
                    "side": str(position.get("side") or ""),
                }
                for position in open_positions
            ],
            "notes": notes,
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

    def refresh_protection_orders(
        self,
        *,
        order_request: ExecutionOrderRequest,
        quantity: float,
        previous_refs: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """Cancel prior conditional protection and re-arm STOP for remaining size.

        Fail-closed: returns empty list when refresh cannot be confirmed.
        """
        symbol = _normalize_binance_symbol(order_request.symbol)
        for ref in previous_refs or []:
            algo_id = ref.get("algoId") or ref.get("orderId") or ref.get("id")
            if algo_id is None:
                continue
            cancel = getattr(self.client, "fapiPrivateDeleteAlgoOrder", None)
            if callable(cancel):
                with contextlib.suppress(Exception):
                    cancel({"algoId": algo_id, "symbol": _binance_market_id(symbol)})
            else:
                with contextlib.suppress(Exception):
                    self.cancel_order(gateway_order_id=str(algo_id))
        open_side = str(order_request.entry_context.get("open_side") or order_request.direction).lower()
        entry_side = "buy" if open_side in {"long", "buy"} else "sell"
        refs = self._submit_protection_algo_orders(
            order_request=order_request,
            side=entry_side,
            quantity=quantity,
        )
        return refs

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
            # Optional protection mirror; primary entry order already submitted.
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
                        # Account-level reconcile must scan all symbols; CCXT otherwise raises.
                        "warnOnFetchOpenOrdersWithoutSymbol": False,
                    },
                }
            )
        )
        return client


MOCK_TRADING_WEB_URL = "https://demo.binance.com/en/futures/BTCUSDT"
TESTNET_TRADING_WEB_URL = "https://testnet.binancefuture.com/en/futures/BTCUSDT"
MAINNET_NOT_SYNCED_HINT = (
    "主网 futures.binance.com 的仓位/订单永远不会同步到本系统"
    "（LIVE_TRADING_ENABLED=false）。请只与下方模拟盘网页对账。"
)


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
    platform_symbol = symbol.replace(":USDT", "")
    exchange_symbol = platform_to_exchange_symbol(platform_symbol)
    if exchange_symbol.endswith("USDT"):
        return f"{exchange_symbol.removesuffix('USDT')}/USDT:USDT"
    if platform_symbol.endswith("/USDT"):
        return f"{platform_symbol}:USDT"
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


def _binance_mode_urls(*, api_backend: str | None = None) -> tuple[str, str, str]:
    """Return (trading_mode label, api_base, web_ui_url) for the active paper backend."""
    backend = (api_backend or settings.binance_trading_mode or "demo").lower()
    if backend in {"testnet", "testnet-fallback"}:
        return (
            "testnet",
            "https://testnet.binancefuture.com/fapi/v1",
            TESTNET_TRADING_WEB_URL,
        )
    return (
        "demo",
        settings.binance_usdm_rest_base or "https://demo-fapi.binance.com",
        MOCK_TRADING_WEB_URL,
    )


def _binance_account_warning(*, api_backend: str) -> str:
    base = (
        f"{MAINNET_NOT_SYNCED_HINT}"
        " 币安网页 Login 报 restricted countries 时，API 仍可正常交易；"
        "本面板以 API 真源为准。"
    )
    if api_backend in {"testnet", "testnet-fallback"}:
        reason = (
            "demo-fapi 不可达，已使用 legacy Testnet API。"
            if api_backend == "testnet-fallback"
            else "当前配置为 Testnet。"
        )
        return (
            f"{base} {reason}"
            "对账入口：testnet.binancefuture.com（须同一套 Testnet API Key 账号）。"
            "不要打开 futures.binance.com 主网对比。"
        )
    return (
        f"{base} 对账入口：demo.binance.com。"
        "不要打开 futures.binance.com 主网对比。"
    )


def probe_testnet_account(
    *,
    order_limit: int = 10,
    order_symbols: list[str] | tuple[str, ...] | None = None,
) -> BinanceTestnetAccountStatus:
    """Fetch live Binance Mock Trading balances, positions, and recent orders via API."""
    # First call after process start can race with demo/testnet URL selection and
    # time-sync; retry once so the desk does not briefly show flat while Binance
    # still has live Demo positions.
    first = _probe_testnet_account_once(order_limit=order_limit, order_symbols=order_symbols)
    if first.connected and (first.positions or first.open_position_count > 0 or first.error):
        return first
    if not first.connected and first.error and "credentials" in str(first.error).lower():
        return first
    return _probe_testnet_account_once(order_limit=order_limit, order_symbols=order_symbols)


def _probe_testnet_account_once(
    *,
    order_limit: int = 10,
    order_symbols: list[str] | tuple[str, ...] | None = None,
) -> BinanceTestnetAccountStatus:
    """Single-shot Binance Demo/Testnet account probe."""
    trading_mode, api_base, web_ui_url = _binance_mode_urls()
    warning = _binance_account_warning(api_backend=trading_mode)
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
        trading_mode, api_base, web_ui_url = _binance_mode_urls(api_backend=api_backend)
        warning = _binance_account_warning(api_backend=api_backend)
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
        # Prefer the concrete position list over a stale sync counter.
        if positions and snapshot.open_position_count != len(positions):
            snapshot = snapshot.model_copy(update={"open_position_count": len(positions)})
        # Point the operator at the open symbol on the correct paper web UI.
        if positions:
            lead = positions[0].symbol.replace(":USDT", "").replace("/", "")
            if "testnet.binancefuture.com" in web_ui_url:
                web_ui_url = f"https://testnet.binancefuture.com/en/futures/{lead}"
            else:
                web_ui_url = f"https://demo.binance.com/en/futures/{lead}"
        recent_orders: list[BinanceTestnetOrderView] = []
        requested_symbols = {platform_to_exchange_symbol(symbol) for symbol in (order_symbols or ())}
        position_symbols = {p.symbol.replace(":USDT", "").replace("/", "") for p in positions}
        # Always include liquid majors so the desk order tab is not empty when
        # the only open position is on a quieter symbol the operator is not viewing.
        core_symbols = {"BTCUSDT", "ETHUSDT", "SOLUSDT", "LINKUSDT"}
        symbols = requested_symbols | position_symbols | core_symbols
        if not symbols:
            symbols = {"BTCUSDT"}
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
                        reduce_only=bool(raw.get("reduceOnly", False)),
                        update_time=int(raw["updateTime"]) if raw.get("updateTime") else None,
                    )
                )
        recent_orders.sort(key=lambda item: item.update_time or 0, reverse=True)
        open_orders: list[BinanceTestnetOrderView] = []
        options = getattr(client, "options", None)
        if isinstance(options, dict):
            options["warnOnFetchOpenOrdersWithoutSymbol"] = False
        try:
            for raw in client.fetch_open_orders() or []:
                open_orders.append(
                    BinanceTestnetOrderView(
                        order_id=str(raw.get("id") or raw.get("orderId") or ""),
                        symbol=str(raw.get("symbol") or ""),
                        side=str(raw.get("side") or ""),
                        order_type=str(raw.get("type") or ""),
                        status=str(raw.get("status") or "open"),
                        quantity=float(raw.get("amount") or raw.get("remaining") or 0),
                        avg_price=float(raw["average"]) if raw.get("average") else None,
                        reduce_only=bool((raw.get("info") or {}).get("reduceOnly", False)),
                        update_time=int(raw["timestamp"]) if raw.get("timestamp") else None,
                    )
                )
        except Exception:  # noqa: BLE001 - open-order scan is best-effort for desk sync
            pass
        # Conditional TP/SL on USDM are algo orders; web UI "Open Orders" includes them.
        try:
            open_algo = getattr(client, "fapiPrivateGetOpenAlgoOrders", None)
            payload = open_algo({}) if callable(open_algo) else []
            for raw in payload or []:
                open_orders.append(
                    BinanceTestnetOrderView(
                        order_id=str(raw.get("algoId") or raw.get("clientAlgoId") or ""),
                        symbol=str(raw.get("symbol") or ""),
                        side=str(raw.get("side") or ""),
                        order_type=str(raw.get("orderType") or raw.get("type") or "CONDITIONAL"),
                        status=str(raw.get("algoStatus") or raw.get("status") or "NEW"),
                        quantity=float(raw.get("quantity") or 0),
                        avg_price=None,
                        reduce_only=bool(raw.get("reduceOnly", False)),
                        update_time=int(raw["updateTime"]) if raw.get("updateTime") else None,
                    )
                )
        except Exception:  # noqa: BLE001
            pass
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
            open_position_count=len(positions),
            positions=positions,
            open_orders=open_orders,
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
