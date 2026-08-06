"""Binance Testnet Adapter for V2 automated trading.

This adapter is the sole interface between V2 Application layer and Binance exchange.
It enforces the Exchange-First invariant: all order submissions, fills, and account
state must come from real Binance Testnet responses.

Key principles:
- Never creates local Position objects (Application layer does that)
- Never executes strategy logic (Application layer does that)
- Returns immutable receipts as proof of exchange actions
- Gateway unavailable = explicit error, never silent local fill
- All exchange responses are logged for audit trail
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from services.automated_trading.domain.enums import V2ExecutionMode
from services.automated_trading.infrastructure.fill_normalizer import (
    deduplicate_fills,
    normalize_ccxt_trade,
)
from services.automated_trading.infrastructure.market_snapshot_provider import (
    AuthoritativeAccountSnapshot,
    ExchangeOrderSnapshot,
    ExchangePositionSnapshot,
    PreSubmitMarketSnapshot,
)

if TYPE_CHECKING:
    from services.automated_trading.domain.commands import (
        SubmitEntryToExchange,
        SubmitProtectionOrders,
        SubmitReduceOnlyExit,
    )

logger = logging.getLogger(__name__)


def _canonical_symbol(symbol: str) -> str:
    """Map CCXT perpetual symbols to the platform's execution-universe form."""
    canonical = symbol.split(":", 1)[0]
    if "/" not in canonical and canonical.endswith("USDT"):
        return f"{canonical[:-4]}/USDT"
    return canonical


def _looks_like_binance_algo_order_id(exchange_order_id: str) -> bool:
    """Binance algo ids occupy a distinct long numeric id space."""
    return exchange_order_id.isdigit() and len(exchange_order_id) >= 15


@dataclass(frozen=True)
class ExchangeOrderReceipt:
    """Immutable evidence of exchange order submission.

    This is the proof that exchange acknowledged an order submission.
    Does NOT imply the order is filled - only that exchange accepted it.
    """

    exchange_order_id: str
    client_order_id: str
    symbol: str
    side: str  # "buy" | "sell"
    order_type: str  # "market" | "stop_market" | "take_profit_market"
    quantity: Decimal
    price: Decimal | None
    status: str  # "new" | "partially_filled" | "filled"
    acknowledged_at: datetime


@dataclass(frozen=True)
class ExchangeFillReceipt:
    """Immutable evidence of exchange fill execution.

    This is the Exchange-First proof required before any local position projection.
    """

    exchange_order_id: str
    trade_id: str
    filled_quantity: Decimal
    fill_price: Decimal
    fee: Decimal
    fill_timestamp: datetime


class BinanceAdapterUnavailable(Exception):
    """Raised when Binance gateway is unavailable or credentials missing."""

    pass


class BinanceTestnetAdapter:
    """Authoritative Binance Testnet adapter for V2 automated trading.

    This adapter wraps the existing services/execution/gateway.py Binance client
    and provides V2-specific interfaces with immutable receipts.

    It does NOT:
    - Create local positions
    - Execute strategy logic
    - Fallback to local fills when exchange unavailable
    - Cache or synthesize exchange responses
    """

    def __init__(self, execution_mode: V2ExecutionMode):
        """Initialize adapter.

        Args:
            execution_mode: Must be BINANCE_TESTNET for this adapter

        Raises:
            ValueError: If execution_mode is not BINANCE_TESTNET
        """
        if execution_mode != V2ExecutionMode.BINANCE_TESTNET:
            raise ValueError(f"BinanceTestnetAdapter requires BINANCE_TESTNET mode, got {execution_mode}")

        self.execution_mode = execution_mode
        self._gateway: Any | None = None  # Lazy-initialized

    def _ensure_gateway(self) -> Any:
        """Lazy-initialize the Binance gateway and return its exchange client.

        Returns:
            The raw exchange client used for all order/account calls.

        Raises:
            BinanceAdapterUnavailable: If gateway cannot be initialized
        """
        if self._gateway is not None:
            return self._gateway.client

        try:
            from services.execution.gateway import BinanceUsdtPerpetualGateway, _UnavailableBinanceClient
            from shared.config import settings

            if not (settings.binance_api_key and settings.binance_api_secret):
                raise BinanceAdapterUnavailable("Binance credentials not configured")

            gateway = BinanceUsdtPerpetualGateway(use_testnet=True)

            # Check if gateway client is unavailable
            if isinstance(gateway.client, _UnavailableBinanceClient):
                raise BinanceAdapterUnavailable("Binance client initialization failed")

            self._gateway = gateway
            return gateway.client

        except BinanceAdapterUnavailable:
            raise
        except Exception as e:
            logger.error("Failed to initialize Binance Testnet gateway: %s", e, exc_info=True)
            raise BinanceAdapterUnavailable(f"Cannot initialize Binance gateway: {e}") from e

    def fetch_authoritative_snapshot(self) -> AuthoritativeAccountSnapshot:
        """Fetch current account state from Binance.

        Returns:
            AuthoritativeAccountSnapshot with positions and orders

        Raises:
            BinanceAdapterUnavailable: If exchange unavailable
        """
        client = self._ensure_gateway()

        try:
            # Fetch account balance
            balance_response = client.fetch_balance()
            usdt_balance = Decimal(str(balance_response.get("USDT", {}).get("free", 0)))
            usdt_total = Decimal(str(balance_response.get("USDT", {}).get("total", 0)))

            # Fetch open positions
            positions_response = client.fetch_positions()
            positions = []
            for pos_data in positions_response:
                if Decimal(str(pos_data.get("contracts", 0))) == 0:
                    continue  # Skip closed positions

                leverage = pos_data.get("leverage")
                positions.append(
                    ExchangePositionSnapshot(
                        symbol=_canonical_symbol(pos_data["symbol"]),
                        direction="long" if pos_data["side"] == "long" else "short",
                        quantity=Decimal(str(pos_data["contracts"])),
                        entry_price=Decimal(str(pos_data["entryPrice"])),
                        mark_price=Decimal(str(pos_data["markPrice"])),
                        unrealized_pnl=Decimal(str(pos_data.get("unrealizedPnl", 0))),
                        leverage=int(leverage) if leverage is not None else None,
                    )
                )

            # Fetch open orders
            orders_response = client.fetch_open_orders()
            orders = []
            for order_data in orders_response:
                orders.append(
                    ExchangeOrderSnapshot(
                        exchange_order_id=str(order_data["id"]),
                        client_order_id=order_data.get("clientOrderId"),
                        symbol=_canonical_symbol(order_data["symbol"]),
                        side=order_data["side"],
                        order_type=order_data["type"],
                        quantity=Decimal(str(order_data["amount"])),
                        price=Decimal(str(order_data["price"])) if order_data.get("price") else None,
                        status=order_data["status"],
                        reduce_only=order_data.get("reduceOnly", False),
                    )
                )
            # Binance USDM conditional SL/TP orders are served by the algo
            # endpoint and are not included in CCXT fetch_open_orders().
            fetch_open_algo_orders = getattr(client, "fapiPrivateGetOpenAlgoOrders", None)
            algo_orders = fetch_open_algo_orders({}) if callable(fetch_open_algo_orders) else []
            known_order_ids = {order.exchange_order_id for order in orders}
            for order_data in algo_orders or []:
                exchange_order_id = str(
                    order_data.get("algoId") or order_data.get("orderId") or order_data.get("id") or ""
                )
                if not exchange_order_id or exchange_order_id in known_order_ids:
                    continue
                orders.append(
                    ExchangeOrderSnapshot(
                        exchange_order_id=exchange_order_id,
                        client_order_id=order_data.get("clientAlgoId") or order_data.get("clientOrderId"),
                        symbol=_canonical_symbol(str(order_data.get("symbol") or "")),
                        side=str(order_data.get("side") or "").lower(),
                        order_type=str(order_data.get("orderType") or order_data.get("type") or "conditional").lower(),
                        quantity=Decimal(str(order_data.get("quantity") or "0")),
                        price=(
                            Decimal(str(order_data.get("triggerPrice") or order_data.get("stopPrice")))
                            if order_data.get("triggerPrice") or order_data.get("stopPrice")
                            else None
                        ),
                        status=str(order_data.get("algoStatus") or order_data.get("status") or "NEW").lower(),
                        reduce_only=bool(order_data.get("reduceOnly", False)),
                    )
                )
                known_order_ids.add(exchange_order_id)

            return AuthoritativeAccountSnapshot(
                balance=usdt_balance,
                equity=usdt_total,
                positions=positions,
                pending_orders=orders,
                snapshot_timestamp=datetime.now(UTC),
            )

        except Exception as e:
            logger.error("Failed to fetch authoritative snapshot: %s", e, exc_info=True)
            raise BinanceAdapterUnavailable(f"Cannot fetch account snapshot: {e}") from e

    def fetch_market_snapshot(self, symbol: str) -> PreSubmitMarketSnapshot:
        """Fetch current market state for symbol.

        Args:
            symbol: Trading symbol (e.g., "BTC/USDT")

        Returns:
            PreSubmitMarketSnapshot with current price and symbol config

        Raises:
            BinanceAdapterUnavailable: If exchange unavailable
        """
        client = self._ensure_gateway()

        try:
            # Fetch current ticker
            ticker = client.fetch_ticker(symbol)
            current_price = Decimal(str(ticker["last"]))

            # Fetch market config
            market = client.market(symbol)
            tick_size = Decimal(str(market["precision"]["price"]))
            step_size = Decimal(str(market["precision"]["amount"]))
            min_notional = Decimal(str(market["limits"]["cost"]["min"]))

            # Fetch ATR (if available from existing data service)
            # For now, use a placeholder - will be integrated with data service
            atr = Decimal("0")  # TODO: integrate with data service ATR

            return PreSubmitMarketSnapshot(
                symbol=symbol,
                current_price=current_price,
                atr=atr,
                last_update=datetime.now(UTC),
                tick_size=tick_size,
                step_size=step_size,
                min_notional=min_notional,
            )

        except Exception as e:
            logger.error("Failed to fetch market snapshot for %s: %s", symbol, e, exc_info=True)
            raise BinanceAdapterUnavailable(f"Cannot fetch market snapshot: {e}") from e

    def submit_market_order(self, command: SubmitEntryToExchange, symbol: str, side: str) -> ExchangeOrderReceipt:
        """Submit market order to exchange.

        Args:
            command: Entry submission command
            symbol: Trading symbol
            side: "buy" or "sell"

        Returns:
            ExchangeOrderReceipt with exchange order ID

        Raises:
            BinanceAdapterUnavailable: If exchange unavailable
        """
        client = self._ensure_gateway()

        try:
            # Binance futures applies leverage through its dedicated endpoint;
            # including it in create_order params is not a verified substitute.
            # Fail before an entry is submitted when this prerequisite cannot be
            # established. Reduce-only exits use their own submission path.
            try:
                client.set_leverage(command.leverage, symbol)
            except Exception as exc:
                raise BinanceAdapterUnavailable(f"leverage configuration failed: {exc}") from exc

            order_response = client.create_order(
                symbol=symbol,
                type="market",
                side=side,
                amount=float(command.quantity),
                params={
                    "newClientOrderId": command.client_order_id,
                },
            )

            return ExchangeOrderReceipt(
                exchange_order_id=str(order_response["id"]),
                client_order_id=command.client_order_id,
                symbol=symbol,
                side=side,
                order_type="market",
                quantity=command.quantity,
                price=None,
                status=order_response["status"],
                acknowledged_at=datetime.now(UTC),
            )

        except BinanceAdapterUnavailable:
            raise
        except Exception as e:
            logger.error(
                "Failed to submit market order %s %s %s: %s",
                side,
                command.quantity,
                symbol,
                e,
                exc_info=True,
            )
            raise BinanceAdapterUnavailable(f"Cannot submit market order: {e}") from e

    def query_order_by_client_id(self, symbol: str, client_order_id: str) -> ExchangeOrderReceipt | None:
        """Query order status by client order ID.

        Args:
            symbol: Trading symbol
            client_order_id: Client-side order ID

        Returns:
            ExchangeOrderReceipt if found, None if not found

        Raises:
            BinanceAdapterUnavailable: If exchange unavailable
        """
        client = self._ensure_gateway()

        try:

            def receipt_from_algo(order_data: dict[str, Any]) -> ExchangeOrderReceipt:
                return ExchangeOrderReceipt(
                    exchange_order_id=str(
                        order_data.get("algoId") or order_data.get("orderId") or order_data.get("id") or ""
                    ),
                    client_order_id=str(order_data.get("clientAlgoId") or order_data.get("clientOrderId") or ""),
                    symbol=_canonical_symbol(str(order_data.get("symbol") or symbol)),
                    side=str(order_data.get("side") or "").lower(),
                    order_type=str(order_data.get("orderType") or order_data.get("type") or "conditional").lower(),
                    quantity=Decimal(str(order_data.get("quantity") or order_data.get("origQty") or 0)),
                    price=(
                        Decimal(
                            str(
                                order_data.get("triggerPrice") or order_data.get("stopPrice") or order_data.get("price")
                            )
                        )
                        if order_data.get("triggerPrice") or order_data.get("stopPrice") or order_data.get("price")
                        else None
                    ),
                    status=str(order_data.get("algoStatus") or order_data.get("status") or "new").lower(),
                    acknowledged_at=datetime.now(UTC),
                )

            # USDM conditional SL/TP orders are Binance algo orders and do not
            # appear in CCXT's fetch_open_orders/fetch_closed_orders results.
            fetch_open_algo_orders = getattr(client, "fapiPrivateGetOpenAlgoOrders", None)
            if callable(fetch_open_algo_orders):
                algo_orders = fetch_open_algo_orders({"symbol": symbol.replace("/", "").split(":", 1)[0]})
                for order_data in algo_orders or []:
                    candidate = str(order_data.get("clientAlgoId") or order_data.get("clientOrderId") or "")
                    if candidate == client_order_id:
                        return receipt_from_algo(order_data)

            # Binance requires fetching all open orders and filtering
            # (no direct client_order_id query in CCXT)
            open_orders = client.fetch_open_orders(symbol)
            for order_data in open_orders:
                if order_data.get("clientOrderId") == client_order_id:
                    return ExchangeOrderReceipt(
                        exchange_order_id=str(order_data["id"]),
                        client_order_id=client_order_id,
                        symbol=symbol,
                        side=order_data["side"],
                        order_type=order_data["type"],
                        quantity=Decimal(str(order_data["amount"])),
                        price=Decimal(str(order_data["price"])) if order_data.get("price") else None,
                        status=order_data["status"],
                        acknowledged_at=datetime.now(UTC),
                    )

            # If not in open orders, check closed orders
            closed_orders = client.fetch_closed_orders(symbol, limit=100)
            for order_data in closed_orders:
                if order_data.get("clientOrderId") == client_order_id:
                    return ExchangeOrderReceipt(
                        exchange_order_id=str(order_data["id"]),
                        client_order_id=client_order_id,
                        symbol=symbol,
                        side=order_data["side"],
                        order_type=order_data["type"],
                        quantity=Decimal(str(order_data["amount"])),
                        price=Decimal(str(order_data["price"])) if order_data.get("price") else None,
                        status=order_data["status"],
                        acknowledged_at=datetime.now(UTC),
                    )

            return None  # Order not found

        except Exception as e:
            logger.error(
                "Failed to query order by client_id %s for %s: %s",
                client_order_id,
                symbol,
                e,
                exc_info=True,
            )
            raise BinanceAdapterUnavailable(f"Cannot query order: {e}") from e

    def fetch_fills(self, symbol: str, exchange_order_id: str) -> tuple[ExchangeFillReceipt, ...]:
        """Fetch fill receipts for an exchange order.

        Args:
            symbol: Trading symbol
            exchange_order_id: Exchange order ID

        Returns:
            Tuple of ExchangeFillReceipt (may be multiple for partial fills)

        Raises:
            BinanceAdapterUnavailable: If exchange unavailable
        """
        client = self._ensure_gateway()

        try:
            actual_order_id = exchange_order_id
            trades = client.fetch_my_trades(symbol, params={"orderId": actual_order_id})
            if not trades and _looks_like_binance_algo_order_id(exchange_order_id):
                fetch_algo_order = getattr(client, "fapiPrivateGetAlgoOrder", None)
                if callable(fetch_algo_order):
                    algo_order = fetch_algo_order(
                        {
                            "algoId": exchange_order_id,
                            "symbol": symbol.replace("/", "").split(":", 1)[0],
                        }
                    )
                    if isinstance(algo_order, dict) and algo_order.get("actualOrderId"):
                        actual_order_id = str(algo_order["actualOrderId"])
                        trades = client.fetch_my_trades(
                            symbol,
                            params={"orderId": actual_order_id},
                        )

            normalized_fills = deduplicate_fills(
                normalize_ccxt_trade(trade_data, expected_order_id=actual_order_id) for trade_data in trades
            )
            receipts = []
            for fill in normalized_fills:
                receipts.append(
                    ExchangeFillReceipt(
                        exchange_order_id=fill.exchange_order_id,
                        trade_id=fill.trade_id,
                        filled_quantity=fill.filled_quantity,
                        fill_price=fill.fill_price,
                        fee=fill.fee,
                        fill_timestamp=fill.fill_timestamp,
                    )
                )

            return tuple(receipts)

        except Exception as e:
            logger.error(
                "Failed to fetch fills for order %s on %s: %s",
                exchange_order_id,
                symbol,
                e,
                exc_info=True,
            )
            raise BinanceAdapterUnavailable(f"Cannot fetch fills: {e}") from e

    def submit_protection(
        self,
        command: SubmitProtectionOrders,
        symbol: str,
        side: str,
        quantity: Decimal,
    ) -> tuple[ExchangeOrderReceipt, ExchangeOrderReceipt | None]:
        """Submit stop-loss and take-profit protection orders.

        Args:
            command: Protection submission command
            symbol: Trading symbol
            side: "sell" for long protection, "buy" for short protection
            quantity: Position quantity to protect

        Returns:
            (stop_receipt, tp_receipt) where tp_receipt may be None

        Raises:
            BinanceAdapterUnavailable: If exchange unavailable
        """
        client = self._ensure_gateway()

        try:
            # Submit stop-loss
            stop_response = client.create_order(
                symbol=symbol,
                type="stop_market",
                side=side,
                amount=float(quantity),
                params={
                    "stopPrice": float(command.stop_loss_price),
                    "newClientOrderId": command.stop_client_order_id,
                    "reduceOnly": True,
                },
            )

            stop_receipt = ExchangeOrderReceipt(
                exchange_order_id=str(stop_response["id"]),
                client_order_id=command.stop_client_order_id,
                symbol=symbol,
                side=side,
                order_type="stop_market",
                quantity=quantity,
                price=command.stop_loss_price,
                status=stop_response["status"],
                acknowledged_at=datetime.now(UTC),
            )

            # Submit take-profit (if requested)
            tp_receipt = None
            if command.take_profit_price and command.tp_client_order_id:
                tp_response = client.create_order(
                    symbol=symbol,
                    type="take_profit_market",
                    side=side,
                    amount=float(quantity),
                    params={
                        "stopPrice": float(command.take_profit_price),
                        "newClientOrderId": command.tp_client_order_id,
                        "reduceOnly": True,
                    },
                )

                tp_receipt = ExchangeOrderReceipt(
                    exchange_order_id=str(tp_response["id"]),
                    client_order_id=command.tp_client_order_id,
                    symbol=symbol,
                    side=side,
                    order_type="take_profit_market",
                    quantity=quantity,
                    price=command.take_profit_price,
                    status=tp_response["status"],
                    acknowledged_at=datetime.now(UTC),
                )

            return stop_receipt, tp_receipt

        except Exception as e:
            logger.error(
                "Failed to submit protection for %s: %s",
                symbol,
                e,
                exc_info=True,
            )
            raise BinanceAdapterUnavailable(f"Cannot submit protection: {e}") from e

    def submit_reduce_only_exit(
        self,
        command: SubmitReduceOnlyExit,
        symbol: str,
        side: str,
    ) -> ExchangeOrderReceipt:
        """Submit reduce-only market exit order.

        Args:
            command: Exit submission command
            symbol: Trading symbol
            side: "sell" for long exit, "buy" for short exit

        Returns:
            ExchangeOrderReceipt with exchange order ID

        Raises:
            BinanceAdapterUnavailable: If exchange unavailable
        """
        client = self._ensure_gateway()

        try:
            order_response = client.create_order(
                symbol=symbol,
                type="market",
                side=side,
                amount=float(command.reduce_quantity),
                params={
                    "newClientOrderId": command.client_order_id,
                    "reduceOnly": True,
                },
            )

            return ExchangeOrderReceipt(
                exchange_order_id=str(order_response["id"]),
                client_order_id=command.client_order_id,
                symbol=symbol,
                side=side,
                order_type="market",
                quantity=command.reduce_quantity,
                price=None,
                status=order_response["status"],
                acknowledged_at=datetime.now(UTC),
            )

        except Exception as e:
            logger.error(
                "Failed to submit reduce-only exit %s %s %s: %s",
                side,
                command.reduce_quantity,
                symbol,
                e,
                exc_info=True,
            )
            raise BinanceAdapterUnavailable(f"Cannot submit reduce-only exit: {e}") from e

    def cancel_order(self, symbol: str, exchange_order_id: str) -> ExchangeOrderReceipt:
        """Cancel an open order.

        Args:
            symbol: Trading symbol
            exchange_order_id: Exchange order ID to cancel

        Returns:
            ExchangeOrderReceipt with updated status

        Raises:
            BinanceAdapterUnavailable: If exchange unavailable
        """
        client = self._ensure_gateway()

        try:
            cancel_algo_order = getattr(client, "fapiPrivateDeleteAlgoOrder", None)
            if _looks_like_binance_algo_order_id(exchange_order_id) and callable(cancel_algo_order):
                cancel_response = cancel_algo_order(
                    {
                        "algoId": exchange_order_id,
                        "symbol": symbol.replace("/", "").split(":", 1)[0],
                    }
                )
                return ExchangeOrderReceipt(
                    exchange_order_id=str(cancel_response.get("algoId") or exchange_order_id),
                    client_order_id=cancel_response.get("clientAlgoId"),
                    symbol=symbol,
                    side=str(cancel_response.get("side") or "").lower(),
                    order_type=str(cancel_response.get("orderType") or "conditional").lower(),
                    quantity=Decimal(str(cancel_response.get("quantity") or "0")),
                    price=(
                        Decimal(str(cancel_response.get("triggerPrice") or cancel_response.get("stopPrice")))
                        if cancel_response.get("triggerPrice") or cancel_response.get("stopPrice")
                        else None
                    ),
                    status="canceled",
                    acknowledged_at=datetime.now(UTC),
                )
            try:
                cancel_response = client.cancel_order(exchange_order_id, symbol)
                return ExchangeOrderReceipt(
                    exchange_order_id=str(cancel_response["id"]),
                    client_order_id=cancel_response.get("clientOrderId"),
                    symbol=symbol,
                    side=cancel_response["side"],
                    order_type=cancel_response["type"],
                    quantity=Decimal(str(cancel_response["amount"])),
                    price=Decimal(str(cancel_response["price"])) if cancel_response.get("price") else None,
                    status="canceled",
                    acknowledged_at=datetime.now(UTC),
                )
            except Exception:
                if not callable(cancel_algo_order):
                    raise
                cancel_response = cancel_algo_order(
                    {
                        "algoId": exchange_order_id,
                        "symbol": symbol.replace("/", "").split(":", 1)[0],
                    }
                )
                return ExchangeOrderReceipt(
                    exchange_order_id=str(cancel_response.get("algoId") or exchange_order_id),
                    client_order_id=cancel_response.get("clientAlgoId"),
                    symbol=symbol,
                    side=str(cancel_response.get("side") or "").lower(),
                    order_type=str(cancel_response.get("orderType") or "conditional").lower(),
                    quantity=Decimal(str(cancel_response.get("quantity") or "0")),
                    price=(
                        Decimal(str(cancel_response.get("triggerPrice") or cancel_response.get("stopPrice")))
                        if cancel_response.get("triggerPrice") or cancel_response.get("stopPrice")
                        else None
                    ),
                    status="canceled",
                    acknowledged_at=datetime.now(UTC),
                )
        except Exception as e:
            logger.error(
                "Failed to cancel order %s on %s: %s",
                exchange_order_id,
                symbol,
                e,
                exc_info=True,
            )
            raise BinanceAdapterUnavailable(f"Cannot cancel order: {e}") from e
