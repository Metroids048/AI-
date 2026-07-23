"""Single fail-closed conversion from domain TradeIntent to exchange order parameters."""

from __future__ import annotations

import hashlib
from datetime import datetime
from decimal import ROUND_DOWN, Decimal
from typing import Any

from shared.models import (
    ExchangeSide,
    MarketRulesSnapshot,
    NormalizedOrder,
    PositionSide,
    TradeAction,
    TradeIntent,
)


class OrderNormalizationError(ValueError):
    """Raised when an intent cannot be proven executable from exchange metadata."""


class CcxtMarketRulesLoader:
    def __init__(self, client: Any) -> None:
        self.client = client

    def load(
        self,
        *,
        symbol: str,
        exchange_symbol: str | None = None,
        position_mode: str,
        margin_mode: str,
        leverage: Decimal,
        loaded_at: datetime,
    ) -> MarketRulesSnapshot:
        markets = self.client.load_markets()
        market = markets.get(exchange_symbol or symbol) or markets.get(symbol)
        if not isinstance(market, dict):
            raise OrderNormalizationError("exchange market metadata is unavailable")
        raw_info = market.get("info")
        info: dict[str, Any] = raw_info if isinstance(raw_info, dict) else {}
        raw_filters = info.get("filters")
        filters: list[Any] = raw_filters if isinstance(raw_filters, list) else []
        by_type = {
            str(item.get("filterType")): item for item in filters if isinstance(item, dict) and item.get("filterType")
        }
        price_filter = by_type.get("PRICE_FILTER", {})
        lot_filter = by_type.get("LOT_SIZE", {})
        notional_filter = by_type.get("MIN_NOTIONAL", by_type.get("NOTIONAL", {}))
        raw_limits = market.get("limits")
        limits: dict[str, Any] = raw_limits if isinstance(raw_limits, dict) else {}
        raw_amount = limits.get("amount")
        amount_limits: dict[str, Any] = raw_amount if isinstance(raw_amount, dict) else {}
        raw_cost = limits.get("cost")
        cost_limits: dict[str, Any] = raw_cost if isinstance(raw_cost, dict) else {}

        def required_decimal(value: Any, name: str) -> Decimal:
            if value is None or Decimal(str(value)) <= 0:
                raise OrderNormalizationError(f"exchange market metadata missing {name}")
            return Decimal(str(value))

        tick_size = required_decimal(price_filter.get("tickSize"), "tick_size")
        step_size = required_decimal(lot_filter.get("stepSize"), "step_size")
        min_quantity = required_decimal(amount_limits.get("min"), "min_quantity")
        min_notional = required_decimal(
            cost_limits.get("min") or notional_filter.get("notional") or notional_filter.get("minNotional"),
            "min_notional",
        )
        snapshot_key = f"{symbol}|{position_mode}|{margin_mode}|{leverage}|{loaded_at.isoformat()}"
        max_qty = amount_limits.get("max")
        max_notional = cost_limits.get("max")
        exchange = str(getattr(self.client, "id", None) or "").strip()
        market_type = str(market.get("type") or market.get("subType") or "").strip()
        resolved_exchange_symbol = str(market.get("symbol") or market.get("id") or "").strip()
        if not exchange or not market_type or not resolved_exchange_symbol:
            raise OrderNormalizationError("exchange market metadata missing identity fields")
        if not isinstance(market.get("active"), bool):
            raise OrderNormalizationError("exchange market metadata missing active state")
        contract_size = required_decimal(market.get("contractSize"), "contract_size")

        def precision_or_increment(raw_value: Any, increment: Decimal) -> int:
            """Normalize CCXT decimal-mode precision and derive it from exchange steps when needed."""
            if raw_value is not None:
                try:
                    parsed = Decimal(str(raw_value))
                    if parsed >= 1 and parsed == parsed.to_integral_value():
                        return int(parsed)
                    if parsed > 0:
                        exponent = parsed.as_tuple().exponent
                        return max(0, -int(exponent)) if isinstance(exponent, int) else 0
                except (ArithmeticError, ValueError):
                    pass
            exponent = increment.as_tuple().exponent
            return max(0, -int(exponent)) if isinstance(exponent, int) else 0

        return MarketRulesSnapshot(
            rules_snapshot_id=f"rules:{hashlib.sha256(snapshot_key.encode('utf-8')).hexdigest()}",
            symbol=symbol,
            market_status=str(info.get("status") or ("TRADING" if market.get("active") else "UNKNOWN")),
            position_mode=position_mode,
            margin_mode=margin_mode,
            leverage=leverage,
            tick_size=tick_size,
            step_size=step_size,
            min_quantity=min_quantity,
            max_quantity=(Decimal(str(max_qty)) if max_qty is not None else None),
            min_notional=min_notional,
            max_notional=(Decimal(str(max_notional)) if max_notional is not None else None),
            loaded_at=loaded_at,
            exchange=exchange,
            market_type=market_type,
            exchange_symbol=resolved_exchange_symbol,
            price_precision=precision_or_increment(
                market.get("precision", {}).get("price") if isinstance(market.get("precision"), dict) else None,
                tick_size,
            ),
            amount_precision=precision_or_increment(
                market.get("precision", {}).get("amount") if isinstance(market.get("precision"), dict) else None,
                step_size,
            ),
            contract_size=contract_size,
            market_active=market["active"],
        )


def _floor_to_increment(value: Decimal, increment: Decimal) -> Decimal:
    return (value / increment).to_integral_value(rounding=ROUND_DOWN) * increment


def _client_order_id(intent_id: str) -> str:
    digest = hashlib.sha256(intent_id.encode("utf-8")).hexdigest()[:24]
    return f"aqrp-{digest}"


class OrderNormalizer:
    """Build an auditable order without consulting static precision fallbacks."""

    def normalize(
        self,
        intent: TradeIntent,
        rules: MarketRulesSnapshot,
        *,
        confirmed_position_quantity: Decimal | None = None,
    ) -> NormalizedOrder:
        if intent.symbol != rules.symbol:
            raise OrderNormalizationError("intent symbol does not match market rules snapshot")
        if rules.market_status != "TRADING":
            raise OrderNormalizationError("market status is not executable")
        if not rules.market_active:
            raise OrderNormalizationError("market is not active")
        if rules.margin_mode not in {"CROSS", "ISOLATED"}:
            raise OrderNormalizationError("margin mode is unknown")
        if rules.position_mode not in {"ONE_WAY", "HEDGE"}:
            raise OrderNormalizationError("position mode is unknown")

        expected_side = self._expected_side(intent.action, intent.position_side)
        if intent.exchange_side is not expected_side:
            raise OrderNormalizationError("exchange side conflicts with action and position side")

        requested_quantity = intent.target_quantity
        closing = intent.action in {TradeAction.CLOSE, TradeAction.REDUCE}
        if rules.position_mode == "HEDGE" and closing:
            if confirmed_position_quantity is None or confirmed_position_quantity <= 0:
                raise OrderNormalizationError("hedge-mode close requires confirmed position quantity")
            requested_quantity = min(requested_quantity, confirmed_position_quantity)

        quantity = _floor_to_increment(requested_quantity, rules.step_size)
        if quantity < rules.min_quantity:
            raise OrderNormalizationError("normalized quantity is below exchange minimum")
        if rules.max_quantity is not None and quantity > rules.max_quantity:
            raise OrderNormalizationError("normalized quantity exceeds exchange maximum")
        notional = quantity * intent.signal_reference_price
        if notional < rules.min_notional:
            raise OrderNormalizationError("normalized notional is below exchange minimum")
        if rules.max_notional is not None and notional > rules.max_notional:
            raise OrderNormalizationError("normalized notional exceeds exchange maximum")

        if rules.position_mode == "ONE_WAY":
            position_side = "BOTH"
            reduce_only: bool | None = closing
        else:
            position_side = intent.position_side.value
            reduce_only = None

        stop_price = _floor_to_increment(intent.protection.stop_price, rules.tick_size)
        return NormalizedOrder(
            intent_id=intent.intent_id,
            client_order_id=_client_order_id(intent.intent_id),
            symbol=intent.symbol,
            side=intent.exchange_side,
            position_side=position_side,
            reduce_only=reduce_only,
            close_position=False,
            quantity=quantity,
            stop_price=stop_price,
            working_type=intent.protection.working_type,
            rules_snapshot=rules,
        )

    @staticmethod
    def _expected_side(action: TradeAction, position_side: PositionSide) -> ExchangeSide:
        if position_side is PositionSide.FLAT:
            raise OrderNormalizationError("FLAT is not an executable position side")
        if action is TradeAction.OPEN:
            return ExchangeSide.BUY if position_side is PositionSide.LONG else ExchangeSide.SELL
        return ExchangeSide.SELL if position_side is PositionSide.LONG else ExchangeSide.BUY
