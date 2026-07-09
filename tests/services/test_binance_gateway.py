from __future__ import annotations

from services.execution.gateway import BinanceUsdtPerpetualGateway
from shared.models import ExecutionOrderRequest


class StubCcxtClient:
    def __init__(self) -> None:
        self.leverage_calls: list[tuple[int, str]] = []
        self.algo_orders: list[dict] = []
        self.created_orders: list[dict] = []
        self.urls = {
            "test": {"fapiPrivate": "https://testnet.binancefuture.com/fapi/v1"},
            "api": {"fapiPrivate": "https://fapi.binance.com/fapi/v1"},
        }

    def clone(self, value):  # noqa: ANN001
        return dict(value)

    def fetch_time(self, params=None):  # noqa: ANN001
        raise RuntimeError("demo blocked")

    def fetch_balance(self, params=None):  # noqa: ANN001
        assert params == {"type": "future"}
        return {
            "total": {"USDT": 1200.0},
            "free": {"USDT": 1000.0},
            "info": {"totalMarginBalance": "1180.0", "totalUnrealizedProfit": "12.0"},
        }

    def fetch_positions(self):
        return [{"symbol": "BTC/USDT:USDT", "contracts": 1}]

    def create_order(self, symbol, order_type, side, amount, price=None, params=None):  # noqa: ANN001
        assert symbol == "BTC/USDT:USDT"
        assert order_type == "market"
        assert amount == 0.01
        self.created_orders.append(
            {
                "symbol": symbol,
                "order_type": order_type,
                "side": side,
                "amount": amount,
                "price": price,
                "params": params,
            }
        )
        return {"id": "binance-order-1", "status": "open"}

    def fapiPrivatePostAlgoOrder(self, payload):  # noqa: N802, ANN001
        self.algo_orders.append(payload)
        return {"algoId": f"algo-{len(self.algo_orders)}", "status": "NEW"}

    def set_leverage(self, leverage, symbol):  # noqa: ANN001
        self.leverage_calls.append((leverage, symbol))
        return {"leverage": leverage, "symbol": symbol}

    def cancel_order(self, order_id, symbol=None):  # noqa: ANN001
        assert order_id == "binance-order-1"
        return {"id": order_id, "status": "canceled"}

    def fetch_open_orders(self):
        return []


def test_binance_gateway_maps_account_order_cancel_and_reconcile() -> None:
    client = StubCcxtClient()
    gateway = BinanceUsdtPerpetualGateway(client=client, use_testnet=True)

    snapshot = gateway.sync_account(live_run_id="live-run-1")
    submitted = gateway.submit_order(
        live_run_id="live-run-1",
        order_request=ExecutionOrderRequest(
            strategy_id="strategy-1",
            symbol="BTC/USDT",
            direction="long",
            stoploss_plan={"price": 59000},
            takeprofit_plan={"price": 62000},
            entry_context={"order_type": "market", "quantity": 0.01},
        ),
    )
    leverage = gateway.set_leverage(symbol="BTC/USDT", leverage=2)
    cancelled = gateway.cancel_order(gateway_order_id="binance-order-1")
    reconciled = gateway.reconcile(live_run_id="live-run-1")

    assert snapshot.wallet_balance == 1200.0
    assert snapshot.open_position_count == 1
    assert client.urls["api"]["fapiPrivate"] == "https://testnet.binancefuture.com/fapi/v1"
    assert submitted["gateway_order_id"] == "binance-order-1"
    assert client.created_orders[0]["side"] == "buy"
    assert client.created_orders[0]["params"]["stopLoss"]["triggerPrice"] == 59000
    assert len(submitted["protection_order_refs"]) == 2
    assert client.algo_orders[0]["algoType"] == "CONDITIONAL"
    assert client.algo_orders[0]["type"] == "STOP_MARKET"
    assert client.algo_orders[1]["type"] == "TAKE_PROFIT_MARKET"
    assert leverage["gateway_status"] == "acknowledged"
    assert client.leverage_calls == [(2, "BTC/USDT:USDT")]
    assert cancelled["gateway_status"] == "cancelled"
    assert reconciled["reconciliation_status"] == "ok"


def test_binance_gateway_close_only_inverts_position_side() -> None:
    client = StubCcxtClient()
    gateway = BinanceUsdtPerpetualGateway(client=client, use_testnet=True)

    submitted = gateway.submit_order(
        live_run_id="live-run-1",
        order_request=ExecutionOrderRequest(
            strategy_id="strategy-1",
            symbol="BTC/USDT",
            direction="long",
            entry_context={"order_type": "market", "quantity": 0.01, "close_only_mode": True},
        ),
    )

    assert submitted["gateway_order_id"] == "binance-order-1"
    assert client.created_orders[0]["side"] == "sell"
    assert client.created_orders[0]["params"]["reduceOnly"] is True
