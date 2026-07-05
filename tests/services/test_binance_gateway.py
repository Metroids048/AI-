from __future__ import annotations

from services.execution.gateway import BinanceUsdtPerpetualGateway
from shared.models import ExecutionOrderRequest


class StubCcxtClient:
    def __init__(self) -> None:
        self.sandbox_mode_calls: list[bool] = []

    def set_sandbox_mode(self, enabled: bool) -> None:
        self.sandbox_mode_calls.append(enabled)

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
        assert side == "buy"
        assert amount == 0.01
        assert params["stopLoss"]["triggerPrice"] == 59000
        return {"id": "binance-order-1", "status": "open"}

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
    cancelled = gateway.cancel_order(gateway_order_id="binance-order-1")
    reconciled = gateway.reconcile(live_run_id="live-run-1")

    assert snapshot.wallet_balance == 1200.0
    assert snapshot.open_position_count == 1
    assert client.sandbox_mode_calls == [True]
    assert submitted["gateway_order_id"] == "binance-order-1"
    assert cancelled["gateway_status"] == "cancelled"
    assert reconciled["reconciliation_status"] == "ok"
