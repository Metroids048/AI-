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


def test_fetch_authoritative_snapshot_preserves_unknown_exchange_leverage():
    """A real open position remains observable when CCXT reports leverage=None."""
    mock_client = MagicMock()
    mock_client.fetch_balance.return_value = {"USDT": {"free": "500.0", "total": "500.0"}}
    mock_client.fetch_positions.return_value = [
        {
            "symbol": "BTC/USDT:USDT",
            "side": "short",
            "contracts": "0.0324",
            "entryPrice": "118000.0",
            "markPrice": "119000.0",
            "unrealizedPnl": "-32.4",
            "leverage": None,
        }
    ]
    mock_client.fetch_open_orders.return_value = []

    adapter = _adapter_with_mock_client(mock_client)
    snapshot = adapter.fetch_authoritative_snapshot()

    assert len(snapshot.positions) == 1
    assert snapshot.positions[0].symbol == "BTC/USDT"
    assert snapshot.positions[0].quantity == Decimal("0.0324")
    assert snapshot.positions[0].leverage is None


def test_fetch_authoritative_snapshot_normalizes_perpetual_open_order_symbol():
    mock_client = MagicMock()
    mock_client.fetch_balance.return_value = {"USDT": {"free": "500.0", "total": "500.0"}}
    mock_client.fetch_positions.return_value = []
    mock_client.fetch_open_orders.return_value = [
        {
            "id": "order-1",
            "clientOrderId": "A2S-protection",
            "symbol": "ETH/USDT:USDT",
            "side": "sell",
            "type": "stop_market",
            "amount": "0.1",
            "price": None,
            "status": "open",
            "reduceOnly": True,
        }
    ]

    snapshot = _adapter_with_mock_client(mock_client).fetch_authoritative_snapshot()

    assert snapshot.pending_orders[0].symbol == "ETH/USDT"


def test_fetch_authoritative_snapshot_includes_open_algo_protection_orders():
    """Binance USDM conditional SL/TP live on the algo endpoint, not CCXT open orders."""
    mock_client = MagicMock()
    mock_client.fetch_balance.return_value = {"USDT": {"free": "500.0", "total": "500.0"}}
    mock_client.fetch_positions.return_value = []
    mock_client.fetch_open_orders.return_value = []
    mock_client.fapiPrivateGetOpenAlgoOrders.return_value = [
        {
            "algoId": "1000000149467048",
            "clientAlgoId": "A2S-d325aac9142a94fcc2",
            "symbol": "ETHUSDT",
            "side": "SELL",
            "orderType": "STOP_MARKET",
            "quantity": "0.164",
            "triggerPrice": "1912.79",
            "algoStatus": "NEW",
            "reduceOnly": True,
        }
    ]

    snapshot = _adapter_with_mock_client(mock_client).fetch_authoritative_snapshot()

    assert len(snapshot.pending_orders) == 1
    assert snapshot.pending_orders[0].exchange_order_id == "1000000149467048"
    assert snapshot.pending_orders[0].client_order_id == "A2S-d325aac9142a94fcc2"
    assert snapshot.pending_orders[0].symbol == "ETH/USDT"
    assert snapshot.pending_orders[0].reduce_only is True


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

    mock_client.set_leverage.assert_called_once_with(10, "BTC/USDT")
    mock_client.create_order.assert_called_once_with(
        symbol="BTC/USDT",
        type="market",
        side="buy",
        amount=0.1,
        params={"newClientOrderId": "client_abc"},
    )


def test_submit_market_order_configures_leverage_before_submission() -> None:
    """S-102: an entry may be submitted only after leverage setup succeeds."""
    command = SubmitEntryToExchange(
        intent_id="intent_leverage",
        quantity=Decimal("0.1"),
        leverage=7,
        client_order_id="client_leverage",
    )
    mock_client = MagicMock()
    mock_client.create_order.return_value = {"id": "exchange_order_789", "status": "filled"}

    _adapter_with_mock_client(mock_client).submit_market_order(command, "BTC/USDT", "buy")

    mock_client.set_leverage.assert_called_once_with(7, "BTC/USDT")
    mock_client.create_order.assert_called_once()
    assert "leverage" not in mock_client.create_order.call_args.kwargs["params"]


def test_submit_market_order_rejects_when_leverage_setup_fails() -> None:
    """S-102: failed leverage setup is fail-closed and never reaches create_order."""
    command = SubmitEntryToExchange(
        intent_id="intent_leverage_failure",
        quantity=Decimal("0.1"),
        leverage=7,
        client_order_id="client_leverage_failure",
    )
    mock_client = MagicMock()
    mock_client.set_leverage.side_effect = RuntimeError("leverage endpoint unavailable")

    with pytest.raises(BinanceAdapterUnavailable, match="leverage configuration failed"):
        _adapter_with_mock_client(mock_client).submit_market_order(command, "BTC/USDT", "buy")

    mock_client.create_order.assert_not_called()


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
            "order": "exchange_order_456",
            "amount": "0.05",
            "price": "50000.0",
            "fee": {"cost": "2.5", "currency": "USDT"},
            "timestamp": 1700000000000,
        },
        {
            "id": "trade_2",
            "order": "exchange_order_456",
            "amount": "0.05",
            "price": "50100.0",
            "fee": {"cost": "2.5", "currency": "USDT"},
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


def test_fetch_fills_resolves_algo_order_to_actual_filled_order() -> None:
    mock_client = MagicMock()
    mock_client.fetch_my_trades.side_effect = [
        [],
        [
            {
                "id": "309068467",
                "order": "14984144295",
                "amount": "0.164",
                "price": "1909.35",
                "fee": {"cost": "0.12525336", "currency": "USDT"},
                "timestamp": 1785381075368,
            }
        ],
    ]
    mock_client.fapiPrivateGetAlgoOrder.return_value = {
        "algoId": "1000000150045714",
        "clientAlgoId": "A2S-8a9e64e4c0d938d733",
        "algoStatus": "FINISHED",
        "actualOrderId": "14984144295",
    }
    adapter = _adapter_with_mock_client(mock_client)

    fills = adapter.fetch_fills("ETH/USDT", "1000000150045714")

    assert len(fills) == 1
    assert fills[0].exchange_order_id == "14984144295"
    assert fills[0].trade_id == "309068467"
    assert mock_client.fetch_my_trades.call_args_list[1].kwargs == {"params": {"orderId": "14984144295"}}


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


def test_query_filled_order_by_id_reads_authoritative_order_status() -> None:
    mock_client = MagicMock()
    mock_client.fapiPrivateGetOrder.return_value = {
        "orderId": "order-filled",
        "symbol": "ETHUSDT",
        "clientOrderId": "A2E-filled",
        "status": "FILLED",
        "type": "MARKET",
        "origQty": "0.444",
        "executedQty": "0.444",
        "avgPrice": "1912.25131",
        "side": "BUY",
        "updateTime": 1786982460507,
    }

    receipt = _adapter_with_mock_client(mock_client).query_filled_order_by_id("ETH/USDT", "order-filled")

    assert receipt is not None
    assert receipt.status == "filled"
    assert receipt.client_order_id == "A2E-filled"
    assert receipt.quantity == Decimal("0.444")
    assert receipt.price == Decimal("1912.25131")
    mock_client.fapiPrivateGetOrder.assert_called_once_with({"symbol": "ETHUSDT", "orderId": "order-filled"})


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


def test_query_order_by_client_id_finds_open_algo_order():
    """USDM conditional orders are recoverable from Binance's algo endpoint."""
    mock_client = MagicMock()
    mock_client.fapiPrivateGetOpenAlgoOrders.return_value = [
        {
            "algoId": "1000000151515912",
            "clientAlgoId": "client_abc",
            "symbol": "BTCUSDT",
            "side": "SELL",
            "orderType": "STOP_MARKET",
            "quantity": "0.001",
            "triggerPrice": "50000.0",
            "algoStatus": "NEW",
        }
    ]

    adapter = _adapter_with_mock_client(mock_client)
    receipt = adapter.query_order_by_client_id("BTC/USDT", "client_abc")

    assert receipt is not None
    assert receipt.exchange_order_id == "1000000151515912"
    assert receipt.client_order_id == "client_abc"
    assert receipt.order_type == "stop_market"
    assert receipt.quantity == Decimal("0.001")
    mock_client.fapiPrivateGetOpenAlgoOrders.assert_called_once_with({"symbol": "BTCUSDT"})
    mock_client.fetch_open_orders.assert_not_called()


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


def test_cancel_order_falls_back_to_binance_algo_endpoint():
    mock_client = MagicMock()
    mock_client.cancel_order.side_effect = RuntimeError("Unknown order sent")
    mock_client.fapiPrivateDeleteAlgoOrder.return_value = {
        "algoId": "1000000149468905",
        "clientAlgoId": "A2S-172b588b45c5817468",
        "side": "SELL",
        "orderType": "STOP_MARKET",
        "quantity": "0.164",
        "triggerPrice": "1912.80",
        "algoStatus": "CANCELED",
    }

    receipt = _adapter_with_mock_client(mock_client).cancel_order(
        "ETH/USDT",
        "1000000149468905",
    )

    assert receipt.exchange_order_id == "1000000149468905"
    assert receipt.client_order_id == "A2S-172b588b45c5817468"
    assert receipt.status == "canceled"
    mock_client.fapiPrivateDeleteAlgoOrder.assert_called_once_with({"algoId": "1000000149468905", "symbol": "ETHUSDT"})


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
