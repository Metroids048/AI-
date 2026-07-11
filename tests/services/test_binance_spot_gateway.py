from services.execution.spot_gateway import BinanceSpotTestnetGateway, _spot_demo_credentials
from shared.config import settings


class StubSpotClient:
    def __init__(self) -> None:
        self.btc_balance = 1.0
        self.orders: list[dict] = []

    def fetch_balance(self):
        return {
            "total": {"BTC": self.btc_balance, "USDT": 10_000.0},
            "free": {"BTC": self.btc_balance, "USDT": 10_000.0},
            "info": {"canWithdraw": False},
        }

    def fetch_open_orders(self):
        return []

    def fetch_ticker(self, symbol):  # noqa: ANN001
        assert symbol == "BTC/USDT"
        return {"last": 100.0}

    def amount_to_precision(self, symbol, quantity):  # noqa: ANN001
        assert symbol == "BTC/USDT"
        return f"{quantity:.4f}"

    def create_order(self, symbol, order_type, side, amount, price=None, params=None):  # noqa: ANN001
        self.btc_balance += amount if side == "buy" else -amount
        order = {
            "id": f"spot-order-{len(self.orders) + 1}",
            "status": "closed",
            "symbol": symbol,
            "side": side,
            "amount": amount,
            "params": params or {},
        }
        self.orders.append(order)
        return order


def test_spot_gateway_executes_round_trip_and_restores_baseline_balance() -> None:
    client = StubSpotClient()
    gateway = BinanceSpotTestnetGateway(client=client)

    assert gateway.preflight() == {"open_orders": [], "open_positions": []}
    opened = gateway.submit_market_order(
        symbol="BTC/USDT",
        side="buy",
        notional_usdt=1_000,
        quantity=None,
        idempotency_key="carry-spot-open",
    )
    closed = gateway.submit_market_order(
        symbol="BTC/USDT",
        side="sell",
        notional_usdt=1_000,
        quantity=opened["quantity"],
        idempotency_key="carry-spot-close",
    )

    assert opened["gateway_status"] == "filled"
    assert opened["quantity"] == 10.0
    assert closed["side"] == "sell"
    assert client.orders[0]["params"]["newClientOrderId"].startswith("aqs-")
    assert gateway.final_state() == {"open_orders": [], "open_positions": []}


def test_spot_gateway_reuses_existing_binance_demo_credentials(monkeypatch) -> None:
    monkeypatch.setattr(settings, "binance_api_key", "future-demo-key")
    monkeypatch.setattr(settings, "binance_api_secret", "future-demo-secret")
    monkeypatch.setattr(settings, "spot_testnet_api_key", "")
    monkeypatch.setattr(settings, "spot_testnet_api_secret", "")

    assert _spot_demo_credentials() == ("future-demo-key", "future-demo-secret")
