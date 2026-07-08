"""Symbol precision utilities — unified price/quantity rounding per trading pair.

Previously each call site rounded prices and quantities ad-hoc (or not at all),
risking exchange rejections for sub-step sizes. This module centralises the
precision rules so that the gatekeeper, paper runtime, and gateway all apply
identical rounding.

Precision values for Binance USDT-perpetual contracts are baked in as
defaults; unknown symbols fall back to conservative rounding (2 decimals for
price, 3 for quantity) and log a warning so the gap is visible.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal

from shared.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class SymbolPrecision:
    """Precision rules for a single trading pair."""

    symbol: str
    price_precision: int  # decimal places for price
    quantity_precision: int  # decimal places for quantity
    min_quantity: float = 0.001
    min_notional: float = 5.0  # minimum order value in quote currency


# Binance USDT-perpetual contract specs (subset — extensible).
_DEFAULT_PRECISION: dict[str, SymbolPrecision] = {
    "BTC/USDT": SymbolPrecision("BTC/USDT", price_precision=2, quantity_precision=3, min_quantity=0.001, min_notional=5.0),
    "ETH/USDT": SymbolPrecision("ETH/USDT", price_precision=2, quantity_precision=3, min_quantity=0.001, min_notional=5.0),
    "SOL/USDT": SymbolPrecision("SOL/USDT", price_precision=3, quantity_precision=0, min_quantity=1.0, min_notional=5.0),
    "BNB/USDT": SymbolPrecision("BNB/USDT", price_precision=2, quantity_precision=2, min_quantity=0.01, min_notional=5.0),
    "XRP/USDT": SymbolPrecision("XRP/USDT", price_precision=4, quantity_precision=0, min_quantity=1.0, min_notional=5.0),
    "DOGE/USDT": SymbolPrecision("DOGE/USDT", price_precision=5, quantity_precision=0, min_quantity=1.0, min_notional=5.0),
    "ADA/USDT": SymbolPrecision("ADA/USDT", price_precision=4, quantity_precision=0, min_quantity=1.0, min_notional=5.0),
}

_FALLBACK = SymbolPrecision("__fallback__", price_precision=2, quantity_precision=3, min_quantity=0.001, min_notional=5.0)


def get_precision(symbol: str) -> SymbolPrecision:
    """Return the precision rules for ``symbol``, or a conservative fallback."""
    spec = _DEFAULT_PRECISION.get(symbol)
    if spec is not None:
        return spec
    logger.warning("no precision spec registered, using fallback", extra={"symbol": symbol})
    return SymbolPrecision(
        symbol=symbol,
        price_precision=_FALLBACK.price_precision,
        quantity_precision=_FALLBACK.quantity_precision,
        min_quantity=_FALLBACK.min_quantity,
        min_notional=_FALLBACK.min_notional,
    )


def register_precision(spec: SymbolPrecision) -> None:
    """Register or override precision rules for a symbol (hot-updatable)."""
    _DEFAULT_PRECISION[spec.symbol] = spec


def round_price(symbol: str, price: float) -> float:
    """Round a price to the symbol's tick size (always rounds down to be safe)."""
    spec = get_precision(symbol)
    quant = Decimal(10) ** -spec.price_precision
    return float(Decimal(str(price)).quantize(quant, rounding=ROUND_DOWN))


def round_quantity(symbol: str, quantity: float) -> float:
    """Round a quantity to the symbol's step size (always rounds down)."""
    spec = get_precision(symbol)
    quant = Decimal(10) ** -spec.quantity_precision
    return float(Decimal(str(quantity)).quantize(quant, rounding=ROUND_DOWN))


def validate_order(symbol: str, price: float, quantity: float) -> list[str]:
    """Return a list of precision/min-size violations for an order.

    Empty list means the order passes precision checks. Call sites (gatekeeper,
    paper runtime) should append these to their rejection_reasons.
    """
    spec = get_precision(symbol)
    violations: list[str] = []
    rounded_qty = round_quantity(symbol, quantity)
    if rounded_qty < spec.min_quantity:
        violations.append("quantity_below_min")
    notional = rounded_qty * round_price(symbol, price)
    if notional < spec.min_notional:
        violations.append("notional_below_min")
    return violations
