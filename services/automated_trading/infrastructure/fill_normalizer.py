"""Fail-closed normalization for Binance execution fills.

The adapter owns network access; this module owns only deterministic payload
parsing and de-duplication.  It intentionally has no submission functions.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any


class FillNormalizationError(ValueError):
    """A venue payload lacks the immutable evidence needed for a fill."""


@dataclass(frozen=True)
class NormalizedFill:
    """One exchange-confirmed execution, with a fee amount when supplied."""

    exchange_order_id: str
    trade_id: str
    filled_quantity: Decimal
    fill_price: Decimal
    fee: Decimal
    fill_timestamp: datetime

    def __post_init__(self) -> None:
        if not self.exchange_order_id or not self.trade_id:
            raise FillNormalizationError("fill requires exchange_order_id and trade_id")
        if (
            not self.filled_quantity.is_finite()
            or not self.fill_price.is_finite()
            or self.filled_quantity <= 0
            or self.fill_price <= 0
        ):
            raise FillNormalizationError("fill requires finite positive quantity and price")
        if not self.fee.is_finite() or self.fee < 0:
            raise FillNormalizationError("fill fee must be finite and non-negative")


def normalize_ccxt_trade(payload: Mapping[str, Any], *, expected_order_id: str) -> NormalizedFill:
    """Normalize one CCXT trade while preserving the queried order identity."""
    order_id = _required_text(payload.get("order"), "exchange_order_id")
    if order_id != expected_order_id:
        raise FillNormalizationError("CCXT trade order id does not match queried order")
    fee_payload = payload.get("fee")
    fee = _fee_from_mapping(fee_payload)
    return NormalizedFill(
        exchange_order_id=order_id,
        trade_id=_required_text(payload.get("id"), "trade_id"),
        filled_quantity=_positive_decimal(payload.get("amount"), "amount"),
        fill_price=_positive_decimal(payload.get("price"), "price"),
        fee=fee,
        fill_timestamp=_timestamp(payload.get("timestamp")),
    )


def normalize_binance_order_trade_update(payload: Mapping[str, Any]) -> NormalizedFill:
    """Normalize a Binance ``ORDER_TRADE_UPDATE`` event for recovery paths."""
    raw = payload.get("o") if payload.get("e") == "ORDER_TRADE_UPDATE" else payload
    if not isinstance(raw, Mapping):
        raise FillNormalizationError("Binance execution payload must be an object")
    return NormalizedFill(
        exchange_order_id=_required_text(raw.get("i"), "exchange_order_id"),
        trade_id=_required_text(raw.get("t"), "trade_id"),
        filled_quantity=_positive_decimal(raw.get("l"), "last_fill_quantity"),
        fill_price=_positive_decimal(raw.get("L") or raw.get("ap"), "last_fill_price"),
        fee=_fee_from_currency_amount(raw.get("N"), raw.get("n")),
        fill_timestamp=_timestamp(raw.get("T") or payload.get("E")),
    )


def deduplicate_fills(fills: Iterable[NormalizedFill]) -> tuple[NormalizedFill, ...]:
    """De-duplicate repeated user-stream/REST observations by order + trade id."""
    unique: dict[tuple[str, str], NormalizedFill] = {}
    for fill in fills:
        key = (fill.exchange_order_id, fill.trade_id)
        previous = unique.get(key)
        if previous is not None and previous != fill:
            raise FillNormalizationError(f"conflicting duplicate fill for {key[0]}/{key[1]}")
        unique[key] = fill
    return tuple(sorted(unique.values(), key=lambda item: (item.fill_timestamp, item.trade_id)))


def _fee_from_mapping(value: object) -> Decimal:
    if not isinstance(value, Mapping):
        raise FillNormalizationError("CCXT fill requires a fee receipt")
    return _fee_from_currency_amount(value.get("currency"), value.get("cost"))


def _fee_from_currency_amount(currency_value: object, amount: object) -> Decimal:
    currency = _required_text(currency_value, "fee currency").upper()
    if currency != "USDT":
        raise FillNormalizationError("non-USDT fee cannot be aggregated without an exchange conversion receipt")
    return _required_fee(amount)


def _required_text(value: object, name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise FillNormalizationError(f"fill requires {name}")
    return text


def _positive_decimal(value: object, name: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except Exception as exc:  # noqa: BLE001
        raise FillNormalizationError(f"fill {name} must be numeric") from exc
    if not result.is_finite() or result <= 0:
        raise FillNormalizationError(f"fill {name} must be finite and positive")
    return result


def _required_fee(value: object) -> Decimal:
    try:
        fee = Decimal(str(value))
    except Exception as exc:  # noqa: BLE001
        raise FillNormalizationError("fill requires a numeric fee") from exc
    if not fee.is_finite():
        raise FillNormalizationError("fill fee must be finite")
    return abs(fee)


def _timestamp(value: object) -> datetime:
    try:
        milliseconds = int(str(value))
    except Exception as exc:  # noqa: BLE001
        raise FillNormalizationError("fill requires a millisecond timestamp") from exc
    if milliseconds <= 0:
        raise FillNormalizationError("fill timestamp must be positive")
    return datetime.fromtimestamp(milliseconds / 1000, tz=UTC)
