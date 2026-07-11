from services.data.universe import FIXED_TOP20_SYMBOLS
from services.execution.testnet_acceptance import TestnetAcceptanceService as AcceptanceService
from shared.models import TestnetAcceptanceRunRequest as AcceptanceRequest


class FakeAcceptanceGateway:
    def __init__(self, *, fail_close_symbol: str | None = None) -> None:
        self.fail_close_symbol = fail_close_symbol
        self.orders: list[dict] = []
        self.leverages: dict[str, float] = {}
        self.positions: dict[str, float] = {}

    def preflight(self) -> dict:
        return {"open_orders": [], "open_positions": []}

    def account_equity(self) -> float:
        return 10_000.0

    def fetch_last_price(self, symbol: str) -> float:
        return 100.0

    def set_leverage(self, *, symbol: str, leverage: float) -> dict:
        self.leverages[symbol] = leverage
        return {"gateway_status": "acknowledged"}

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
    ) -> dict:
        if reduce_only and symbol == self.fail_close_symbol:
            self.fail_close_symbol = None
            raise ValueError("forced close failure")
        quantity = requested_notional / reference_price
        order = {
            "gateway_order_id": f"order-{len(self.orders) + 1}",
            "gateway_status": "filled",
            "symbol": symbol,
            "side": side,
            "quantity": quantity,
            "requested_notional": requested_notional,
            "reduce_only": reduce_only,
            "protection_order_refs": ([{"gateway_order_id": f"stop-{symbol}"}] if not reduce_only else []),
        }
        self.orders.append(order)
        self.positions[symbol] = 0.0 if reduce_only else quantity
        return order

    def cancel_protection_order(self, *, symbol: str, gateway_order_id: str) -> None:
        return None

    def final_state(self) -> dict:
        return {
            "open_orders": [],
            "open_positions": [symbol for symbol, quantity in self.positions.items() if quantity],
        }


def test_acceptance_run_completes_twenty_round_trips_with_tiered_risk() -> None:
    gateway = FakeAcceptanceGateway()
    service = AcceptanceService(gateway=gateway)

    result = service.run(AcceptanceRequest())

    assert result.run_status == "completed"
    assert result.completed_symbols == list(FIXED_TOP20_SYMBOLS)
    assert result.filled_order_count == 40
    assert len(gateway.orders) == 40
    assert gateway.leverages["BTC/USDT"] == 10
    assert gateway.leverages["ETH/USDT"] == 10
    assert gateway.leverages["SOL/USDT"] == 10
    assert gateway.leverages["XRP/USDT"] == 5
    assert gateway.orders[0]["requested_notional"] == 1_500
    assert gateway.orders[6]["requested_notional"] == 600
    assert result.final_open_position_count == 0
    assert result.final_open_order_count == 0


def test_acceptance_run_retries_compensating_close_and_stops_after_failure() -> None:
    gateway = FakeAcceptanceGateway(fail_close_symbol="ETH/USDT")
    service = AcceptanceService(gateway=gateway)

    result = service.run(AcceptanceRequest())

    assert result.run_status == "failed"
    assert result.completed_symbols == ["BTC/USDT"]
    assert result.failed_symbol == "ETH/USDT"
    assert result.compensation_attempted is True
    assert result.final_open_position_count == 0
    assert all(order["symbol"] in {"BTC/USDT", "ETH/USDT"} for order in gateway.orders)
