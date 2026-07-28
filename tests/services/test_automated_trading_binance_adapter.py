"""Tests for Binance Testnet Adapter.

Verifies:
- Adapter never creates local Position objects
- Adapter never executes strategy logic
- Gateway unavailable raises explicit error (never silent local fill)
- All receipts are immutable
- Exchange responses are converted to domain receipts correctly
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from services.automated_trading.domain.commands import (
    SubmitEntryToExchange,
    SubmitProtectionOrders,
    SubmitReduceOnlyExit,
)
from services.automated_trading.domain.enums import V2ExecutionMode
from services.automated_trading.infrastructure.binance_adapter import (
    BinanceAdapterUnavailable,
    BinanceTestnetAdapter,
    ExchangeOrderReceipt,
)


def _adapter_with_mock_client(mock_client: MagicMock) -> BinanceTestnetAdapter:
    """Build an adapter with a pre-injected mock gateway (bypasses credential lookup)."""
    adapter = BinanceTestnetAdapter(execution_mode=V2ExecutionMode.BINANCE_TESTNET)
    mock_gateway = MagicMock()
    mock_gateway.client = mock_client
    adapter._gateway = mock_gateway
    return adapter


def test_adapter_requires_binance_testnet_mode():
    """Adapter rejects LOCAL_PAPER mode."""
    with pytest.raises(ValueError, match="BINANCE_TESTNET"):
        BinanceTestnetAdapter(execution_mode=V2ExecutionMode.LOCAL_PAPER)


def test_adapter_raises_error_when_credentials_missing(monkeypatch):
    """Missing credentials raise explicit error, never silent local fill."""
    from shared.config import settings

    monkeypatch.setattr(settings, "binance_api_key", "")
    monkeypatch.setattr(settings, "binance_api_secret", "")

    adapter = BinanceTestnetAdapter(execution_mode=V2ExecutionMode.BINANCE_TESTNET)

    with pytest.raises(BinanceAdapterUnavailable, match="credentials not configured"):
        adapter.fetch_authoritative_snapshot()


def test_adapter_raises_error_when_client_unavailable(monkeypatch):
    """_UnavailableBinanceClient raises explicit error, never silent local fill."""
    from services.execution.gateway import _UnavailableBinanceClient
    from shared.config import settings

    monkeypatch.setattr(settings, "binance_api_key", "test_key")
    monkeypatch.setattr(settings, "binance_api_secret", "test_secret")

    unavailable_gateway = MagicMock()
    unavailable_gateway.client = _UnavailableBinanceClient()

    monkeypatch.setattr(
        "services.execution.gateway.BinanceUsdtPerpetualGateway",
        lambda **kwargs: unavailable_gateway,
    )

    adapter = BinanceTestnetAdapter(execution_mode=V2ExecutionMode.BINANCE_TESTNET)

    with pytest.raises(BinanceAdapterUnavailable, match="initialization failed"):
        adapter.fetch_authoritative_snapshot()


def test_fetch_authoritative_snapshot_returns_immutable_snapshot():
    """fetch_authoritative_snapshot returns immutable AuthoritativeAccountSnapshot."""
    mock_client = MagicMock()
    mock_client.fetch_balance.return_value = {
        "USDT": {"free": "10000.0", "total": "10500.0"},
    }
    mock_client.fetch_positions.return_value = [
        {
            "symbol": "BTC/USDT",
            "side": "long",
            "contracts": "0.1",
            "entryPrice": "50000.0",
            "markPrice": "51000.0",
            "unrealizedPnl": "100.0",
            "leverage": 10,
        }
    ]
    mock_client.fetch_open_orders.return_value = []

    adapter = _adapter_with_mock_client(mock_client)
    snapshot = adapter.fetch_authoritative_snapshot()

    assert snapshot.balance == Decimal("10000.0")
    assert snapshot.equity == Decimal("10500.0")
    assert len(snapshot.positions) == 1
    assert snapshot.positions[0].symbol == "BTC/USDT"
    assert snapshot.positions[0].quantity == Decimal("0.1")

    with pytest.raises(AttributeError):
        snapshot.balance = Decimal("9999.0")  # type: ignore[misc]


def test_fetch_authoritative_snapshot_skips_closed_positions():
    """Positions with zero contracts are not projected."""
    mock_client = MagicMock()
    mock_client.fetch_balance.return_value = {"USDT": {"free": "500.0", "total": "500.0"}}
    mock_client.fetch_positions.return_value = [
        {
            "symbol": "ETH/USDT",
            "side": "long",
            "contracts": "0",
            "entryPrice": "3000.0",
            "markPrice": "3000.0",
            "unrealizedPnl": "0",
            "leverage": 5,
        }
    ]
    mock_client.fetch_open_orders.return_value = []

    adapter = _adapter_with_mock_client(mock_client)
    snapshot = adapter.fetch_authoritative_snapshot()

    assert snapshot.positions == []


def test_fetch_market_snapshot_returns_immutable_snapshot():
    """fetch_market_snapshot returns immutable PreSubmitMarketSnapshot."""
    mock_client = MagicMock()
    mock_client.fetch_ticker.return_value = {"last": "50000.0"}
    mock_client.market.return_value = {
        "precision": {"price": "0.01", "amount": "0.001"},
        "limits": {"cost": {"min": "10.0"}},
    }

    adapter = _adapter_with_mock_client(mock_client)
    snapshot = adapter.fetch_market_snapshot("BTC/USDT")

    assert snapshot.symbol == "BTC/USDT"
    assert snapshot.current_price == Decimal("50000.0")
    assert snapshot.tick_size == Decimal("0.01")
    assert snapshot.step_size == Decimal("0.001")
    assert snapshot.min_notional == Decimal("10.0")

    with pytest.raises(AttributeError):
        snapshot.current_price = Decimal("51000.0")  # type: ignore[misc]


def test_submit_market_order_returns_exchange_receipt():
    """submit_market_order returns ExchangeOrderReceipt with exchange order ID."""
    command = SubmitEntryToExchange(
        intent_id="intent_123",
        quantity=Decimal("0.1"),
        leverage=10,
        client_order_id="client_abc",
    )

    mock_client = MagicMock()
    mock_client.create_order.return_value = {"id": "exchange_order_456", "status": "filled"}

    adapter = _adapter_with_mock_client(mock_client)
    receipt = adapter.submit_market_order(command, "BTC/USDT", "buy")

    assert receipt.exchange_order_id == "exchange_order_456"
    assert receipt.client_order_id == "client_abc"
    assert receipt.symbol == "BTC/USDT"
    assert receipt.side == "buy"
    assert receipt.quantity == Decimal("0.1")

    mock_client.create_order.assert_called_once_with(
        symbol="BTC/USDT",
        type="market",
        side="buy",
        amount=0.1,
        params={"newClientOrderId": "client_abc", "leverage": 10},
    )


def test_submit_market_order_failure_raises_and_returns_no_receipt():
    """A failed submission raises; the adapter never fabricates a local fill."""
    command = SubmitEntryToExchange(
        intent_id="intent_123",
        quantity=Decimal("0.1"),
        leverage=10,
        client_order_id="client_abc",
    )

    mock_client = MagicMock()
    mock_client.create_order.side_effect = RuntimeError("binance -2019 margin insufficient")

    adapter = _adapter_with_mock_client(mock_client)

    with pytest.raises(BinanceAdapterUnavailable, match="Cannot submit market order"):
        adapter.submit_market_order(command, "BTC/USDT", "buy")


def test_fetch_fills_returns_tuple_of_fill_receipts():
    """fetch_fills returns tuple of ExchangeFillReceipt for partial fills."""
    mock_client = MagicMock()
    mock_client.fetch_my_trades.return_value = [
        {
            "id": "trade_1",
            "amount": "0.05",
            "price": "50000.0",
            "fee": {"cost": "2.5"},
            "timestamp": 1700000000000,
        },
        {
            "id": "trade_2",
            "amount": "0.05",
            "price": "50100.0",
            "fee": {"cost": "2.5"},
            "timestamp": 1700000001000,
        },
    ]

    adapter = _adapter_with_mock_client(mock_client)
    receipts = adapter.fetch_fills("BTC/USDT", "exchange_order_456")

    assert len(receipts) == 2
    assert receipts[0].trade_id == "trade_1"
    assert receipts[0].filled_quantity == Decimal("0.05")
    assert receipts[0].fill_price == Decimal("50000.0")
    assert receipts[1].trade_id == "trade_2"
    assert receipts[1].fill_price == Decimal("50100.0")


def test_fetch_fills_returns_empty_tuple_when_no_trades():
    """No trades yet is an empty tuple, not a synthesized fill."""
    mock_client = MagicMock()
    mock_client.fetch_my_trades.return_value = []

    adapter = _adapter_with_mock_client(mock_client)

    assert adapter.fetch_fills("BTC/USDT", "exchange_order_456") == ()


def test_submit_protection_returns_stop_and_tp_receipts():
    """submit_protection returns both stop-loss and take-profit receipts."""
    command = SubmitProtectionOrders(
        position_id="pos_123",
        stop_loss_price=Decimal("48000.0"),
        take_profit_price=Decimal("52000.0"),
        stop_client_order_id="stop_abc",
        tp_client_order_id="tp_def",
    )

    mock_client = MagicMock()
    mock_client.create_order.side_effect = [
        {"id": "stop_order_789", "status": "new"},
        {"id": "tp_order_012", "status": "new"},
    ]

    adapter = _adapter_with_mock_client(mock_client)
    stop_receipt, tp_receipt = adapter.submit_protection(command, "BTC/USDT", "sell", Decimal("0.1"))

    assert stop_receipt.exchange_order_id == "stop_order_789"
    assert stop_receipt.order_type == "stop_market"
    assert stop_receipt.price == Decimal("48000.0")

    assert tp_receipt is not None
    assert tp_receipt.exchange_order_id == "tp_order_012"
    assert tp_receipt.order_type == "take_profit_market"
    assert tp_receipt.price == Decimal("52000.0")

    calls = mock_client.create_order.call_args_list
    assert calls[0][1]["params"]["reduceOnly"] is True
    assert calls[1][1]["params"]["reduceOnly"] is True


def test_submit_protection_without_take_profit_returns_none_tp():
    """Stop-only protection returns None for the take-profit receipt."""
    command = SubmitProtectionOrders(
        position_id="pos_123",
        stop_loss_price=Decimal("48000.0"),
        take_profit_price=None,
        stop_client_order_id="stop_abc",
        tp_client_order_id=None,
    )

    mock_client = MagicMock()
    mock_client.create_order.return_value = {"id": "stop_order_789", "status": "new"}

    adapter = _adapter_with_mock_client(mock_client)
    stop_receipt, tp_receipt = adapter.submit_protection(command, "BTC/USDT", "sell", Decimal("0.1"))

    assert stop_receipt.exchange_order_id == "stop_order_789"
    assert tp_receipt is None
    assert mock_client.create_order.call_count == 1


def test_submit_reduce_only_exit_returns_receipt():
    """submit_reduce_only_exit returns ExchangeOrderReceipt with reduceOnly set."""
    command = SubmitReduceOnlyExit(
        position_id="pos_123",
        exit_reason="natural_exit",
        reduce_quantity=Decimal("0.1"),
        client_order_id="exit_xyz",
        is_emergency=False,
    )

    mock_client = MagicMock()
    mock_client.create_order.return_value = {"id": "exit_order_999", "status": "filled"}

    adapter = _adapter_with_mock_client(mock_client)
    receipt = adapter.submit_reduce_only_exit(command, "BTC/USDT", "sell")

    assert receipt.exchange_order_id == "exit_order_999"
    assert receipt.client_order_id == "exit_xyz"
    assert receipt.quantity == Decimal("0.1")

    mock_client.create_order.assert_called_once()
    assert mock_client.create_order.call_args[1]["params"]["reduceOnly"] is True


def test_query_order_by_client_id_finds_open_order():
    """query_order_by_client_id resolves an open order by client ID."""
    mock_client = MagicMock()
    mock_client.fetch_open_orders.return_value = [
        {
            "id": "exchange_order_456",
            "clientOrderId": "client_abc",
            "side": "buy",
            "type": "market",
            "amount": "0.1",
            "price": None,
            "status": "open",
        }
    ]

    adapter = _adapter_with_mock_client(mock_client)
    receipt = adapter.query_order_by_client_id("BTC/USDT", "client_abc")

    assert receipt is not None
    assert receipt.exchange_order_id == "exchange_order_456"
    assert receipt.client_order_id == "client_abc"
    mock_client.fetch_closed_orders.assert_not_called()


def test_query_order_by_client_id_falls_back_to_closed_orders():
    """A filled order that already left the open book is still recoverable."""
    mock_client = MagicMock()
    mock_client.fetch_open_orders.return_value = []
    mock_client.fetch_closed_orders.return_value = [
        {
            "id": "exchange_order_789",
            "clientOrderId": "client_abc",
            "side": "buy",
            "type": "market",
            "amount": "0.1",
            "price": "50000.0",
            "status": "closed",
        }
    ]

    adapter = _adapter_with_mock_client(mock_client)
    receipt = adapter.query_order_by_client_id("BTC/USDT", "client_abc")

    assert receipt is not None
    assert receipt.exchange_order_id == "exchange_order_789"
    assert receipt.price == Decimal("50000.0")


def test_query_order_by_client_id_returns_none_when_not_found():
    """query_order_by_client_id returns None when order not found."""
    mock_client = MagicMock()
    mock_client.fetch_open_orders.return_value = []
    mock_client.fetch_closed_orders.return_value = []

    adapter = _adapter_with_mock_client(mock_client)

    assert adapter.query_order_by_client_id("BTC/USDT", "unknown_client_id") is None


def test_cancel_order_returns_canceled_receipt():
    """cancel_order reports the canceled status from the exchange response."""
    mock_client = MagicMock()
    mock_client.cancel_order.return_value = {
        "id": "stop_order_789",
        "clientOrderId": "stop_abc",
        "side": "sell",
        "type": "stop_market",
        "amount": "0.1",
        "price": "48000.0",
        "status": "canceled",
    }

    adapter = _adapter_with_mock_client(mock_client)
    receipt = adapter.cancel_order("BTC/USDT", "stop_order_789")

    assert receipt.exchange_order_id == "stop_order_789"
    assert receipt.status == "canceled"
    mock_client.cancel_order.assert_called_once_with("stop_order_789", "BTC/USDT")


def test_adapter_never_creates_local_positions():
    """Adapter module never imports or constructs local position models."""
    import inspect

    from services.automated_trading.infrastructure import binance_adapter

    source = inspect.getsource(binance_adapter)

    assert "V2ManagedPosition" not in source
    assert "PaperPosition" not in source
    assert "OrderExecution" not in source


def test_adapter_never_executes_strategy_logic():
    """Adapter never executes strategy logic - only submits orders."""
    adapter = BinanceTestnetAdapter(execution_mode=V2ExecutionMode.BINANCE_TESTNET)

    assert not hasattr(adapter, "evaluate_signal")
    assert not hasattr(adapter, "calculate_position_size")
    assert not hasattr(adapter, "decide_entry")
    assert not hasattr(adapter, "decide_exit")


def test_receipt_immutability():
    """All receipts are immutable dataclasses."""
    receipt = ExchangeOrderReceipt(
        exchange_order_id="order_123",
        client_order_id="client_abc",
        symbol="BTC/USDT",
        side="buy",
        order_type="market",
        quantity=Decimal("0.1"),
        price=None,
        status="filled",
        acknowledged_at=datetime.now(UTC),
    )

    with pytest.raises(AttributeError):
        receipt.exchange_order_id = "order_456"  # type: ignore[misc]

    with pytest.raises(AttributeError):
        receipt.status = "canceled"  # type: ignore[misc]
