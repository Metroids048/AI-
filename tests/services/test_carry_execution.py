from decimal import Decimal

from services.execution.carry_execution import CarryExecutionService
from shared.models import CarryExecutionRequest, FundingArbitrageSignal


class FakeSpotGateway:
    def __init__(self) -> None:
        self.orders: list[dict] = []
        self.quantity = 0.0

    def preflight(self) -> dict:
        return {"open_orders": [], "open_positions": []}

    def submit_market_order(
        self, *, symbol: str, side: str, notional_usdt: float, quantity: float | None, idempotency_key: str
    ) -> dict:
        resolved_quantity = quantity or notional_usdt / 100.0
        self.quantity += resolved_quantity if side == "buy" else -resolved_quantity
        order = {
            "gateway_order_id": f"spot-{len(self.orders) + 1}",
            "gateway_status": "filled",
            "symbol": symbol,
            "side": side,
            "quantity": resolved_quantity,
            "notional_usdt": notional_usdt,
        }
        self.orders.append(order)
        return order

    def final_state(self) -> dict:
        return {"open_orders": [], "open_positions": [] if abs(self.quantity) < 1e-9 else ["BTC/USDT"]}


class FakePerpGateway:
    def __init__(self, *, fail_entry: bool = False) -> None:
        self.fail_entry = fail_entry
        self.orders: list[dict] = []
        self.quantity = 0.0

    def preflight(self) -> dict:
        return {"open_orders": [], "open_positions": []}

    def submit_carry_order(
        self, *, symbol: str, side: str, notional_usdt: float, quantity: float, reduce_only: bool, idempotency_key: str
    ) -> dict:
        if self.fail_entry and not reduce_only:
            raise ValueError("forced perp entry failure")
        self.quantity = 0.0 if reduce_only else -quantity
        order = {
            "gateway_order_id": f"perp-{len(self.orders) + 1}",
            "gateway_status": "filled",
            "symbol": symbol,
            "side": side,
            "quantity": quantity,
            "notional_usdt": notional_usdt,
            "reduce_only": reduce_only,
        }
        self.orders.append(order)
        return order

    def final_state(self) -> dict:
        return {"open_orders": [], "open_positions": [] if abs(self.quantity) < 1e-9 else ["BTC/USDT"]}


def _signal() -> FundingArbitrageSignal:
    return FundingArbitrageSignal(
        symbol="BTC/USDT",
        perp_symbol="BTC/USDT:USDT",
        funding_rate=Decimal("0.0015"),
        funding_bps=15,
        basis_bps=1,
        fee_bps=2,
        slippage_bps=1,
        estimated_net_edge_bps=11,
        should_enter_paper=True,
    )


def test_dual_leg_carry_opens_hedges_and_closes_both_venues() -> None:
    spot = FakeSpotGateway()
    perp = FakePerpGateway()
    service = CarryExecutionService(spot_gateway=spot, perp_gateway=perp)

    result = service.run(CarryExecutionRequest(notional_usdt=1_000), signal=_signal())

    assert result.run_status == "completed"
    assert result.carry_state == "closed"
    assert result.state_history == ["planned", "first_leg_filled", "hedged", "closing", "closed"]
    assert [order.side for order in result.legs] == ["buy", "sell", "buy", "sell"]
    assert result.final_net_exposure_usdt == 0
    assert spot.final_state()["open_positions"] == []
    assert perp.final_state()["open_positions"] == []


def test_dual_leg_carry_compensates_spot_when_perp_entry_fails() -> None:
    spot = FakeSpotGateway()
    perp = FakePerpGateway(fail_entry=True)
    service = CarryExecutionService(spot_gateway=spot, perp_gateway=perp)

    result = service.run(CarryExecutionRequest(notional_usdt=1_000), signal=_signal())

    assert result.run_status == "failed"
    assert result.carry_state == "compensated"
    assert result.state_history == ["planned", "first_leg_filled", "compensated"]
    assert [order.side for order in result.legs] == ["buy", "sell"]
    assert "forced perp entry failure" in (result.error_summary or "")
    assert spot.final_state()["open_positions"] == []


def test_dual_leg_carry_rejects_non_admitted_funding_signal() -> None:
    service = CarryExecutionService(spot_gateway=FakeSpotGateway(), perp_gateway=FakePerpGateway())
    signal = _signal().model_copy(update={"should_enter_paper": False, "rejection_reasons": ["negative_net_edge"]})

    result = service.run(CarryExecutionRequest(notional_usdt=1_000), signal=signal)

    assert result.run_status == "rejected"
    assert result.carry_state == "planned"
    assert result.legs == []
