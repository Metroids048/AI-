"""Tests for deterministic Binance fill normalization and recovery parsing."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from services.automated_trading.domain.enums import V2ExecutionMode
from services.automated_trading.infrastructure.binance_adapter import BinanceTestnetAdapter
from services.automated_trading.infrastructure.fill_normalizer import (
    FillNormalizationError,
    NormalizedFill,
    deduplicate_fills,
    normalize_binance_order_trade_update,
    normalize_ccxt_trade,
)

_DEFAULT_FEE = object()


def _ccxt_trade(
    *,
    trade_id: str = "trade-1",
    amount: str = "0.01",
    fee: object = _DEFAULT_FEE,
    order_id: object = "order-1",
) -> dict[str, object]:
    result: dict[str, object] = {
        "id": trade_id,
        "amount": amount,
        "price": "65000",
        "timestamp": 1_754_390_400_000,
    }
    if order_id is not None:
        result["order"] = order_id
    result["fee"] = {"cost": "0.325", "currency": "USDT"} if fee is _DEFAULT_FEE else fee
    return result


def test_ccxt_fills_are_deduplicated_with_exchange_fee_evidence() -> None:
    fill = normalize_ccxt_trade(_ccxt_trade(), expected_order_id="order-1")

    assert fill.fee == Decimal("0.325")
    assert deduplicate_fills((fill, fill)) == (fill,)


def test_conflicting_duplicate_and_non_quote_fee_fail_closed() -> None:
    first = normalize_ccxt_trade(_ccxt_trade(), expected_order_id="order-1")
    conflicting = normalize_ccxt_trade(_ccxt_trade(amount="0.02"), expected_order_id="order-1")

    with pytest.raises(FillNormalizationError, match="conflicting duplicate"):
        deduplicate_fills((first, conflicting))
    with pytest.raises(FillNormalizationError, match="non-USDT fee"):
        normalize_ccxt_trade(_ccxt_trade(fee={"cost": "0.01", "currency": "BNB"}), expected_order_id="order-1")
    with pytest.raises(FillNormalizationError, match="requires a fee receipt"):
        normalize_ccxt_trade(_ccxt_trade(fee=None), expected_order_id="order-1")
    with pytest.raises(FillNormalizationError, match="requires exchange_order_id"):
        normalize_ccxt_trade(_ccxt_trade(order_id=None), expected_order_id="order-1")


def test_order_trade_update_normalizes_partial_fill() -> None:
    fill = normalize_binance_order_trade_update(
        {
            "e": "ORDER_TRADE_UPDATE",
            "E": 1_754_390_400_000,
            "o": {
                "i": 123,
                "t": 456,
                "l": "0.01",
                "L": "65000",
                "N": "USDT",
                "n": "0.325",
                "T": 1_754_390_400_000,
            },
        }
    )

    assert fill.exchange_order_id == "123"
    assert fill.trade_id == "456"
    assert fill.filled_quantity == Decimal("0.01")
    assert fill.fill_price == Decimal("65000")
    assert fill.fee == Decimal("0.325")
    assert fill.fill_timestamp == datetime(2025, 8, 5, 10, 40, tzinfo=UTC)


def test_adapter_fetch_fills_uses_normalizer_for_deduplication_and_fee_evidence() -> None:
    client = MagicMock()
    client.fetch_my_trades.return_value = [_ccxt_trade(), _ccxt_trade()]
    adapter = BinanceTestnetAdapter(execution_mode=V2ExecutionMode.BINANCE_TESTNET)
    gateway = MagicMock()
    gateway.client = client
    adapter._gateway = gateway

    fills = adapter.fetch_fills("BTC/USDT", "order-1")

    assert len(fills) == 1
    assert fills[0].trade_id == "trade-1"
    assert fills[0].fee == Decimal("0.325")


def test_order_trade_update_rejects_non_quote_or_missing_fee_currency() -> None:
    payload = {
        "e": "ORDER_TRADE_UPDATE",
        "E": 1_754_390_400_000,
        "o": {"i": 123, "t": 456, "l": "0.01", "L": "65000", "n": "0.325", "T": 1_754_390_400_000},
    }

    with pytest.raises(FillNormalizationError, match="fee currency"):
        normalize_binance_order_trade_update(payload)
    payload["o"]["N"] = "BNB"
    with pytest.raises(FillNormalizationError, match="non-USDT fee"):
        normalize_binance_order_trade_update(payload)


@pytest.mark.parametrize("fee", ["NaN", "Infinity", "-Infinity"])
def test_ccxt_fill_rejects_non_finite_fee(fee: str) -> None:
    with pytest.raises(FillNormalizationError, match="fee must be finite"):
        normalize_ccxt_trade(_ccxt_trade(fee={"cost": fee, "currency": "USDT"}), expected_order_id="order-1")


@pytest.mark.parametrize("field", ["amount", "price"])
def test_ccxt_fill_rejects_non_finite_execution_values(field: str) -> None:
    payload = _ccxt_trade()
    payload[field] = "NaN"

    with pytest.raises(FillNormalizationError, match="finite and positive"):
        normalize_ccxt_trade(payload, expected_order_id="order-1")


def test_normalized_fill_rejects_non_finite_values_when_constructed_directly() -> None:
    with pytest.raises(FillNormalizationError, match="finite and non-negative"):
        NormalizedFill(
            exchange_order_id="order-1",
            trade_id="trade-1",
            filled_quantity=Decimal("0.01"),
            fill_price=Decimal("65000"),
            fee=Decimal("NaN"),
            fill_timestamp=datetime(2025, 8, 5, 10, 40, tzinfo=UTC),
        )
