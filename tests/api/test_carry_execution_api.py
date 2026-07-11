from decimal import Decimal

from shared.config import settings
from shared.models import CarryExecutionStatus as CarryStatus
from shared.models import FundingArbitrageSignal


class StubCarryService:
    def run(self, request, *, signal):  # noqa: ANN001
        return CarryStatus(
            run_id=request.idempotency_key or "carry-run",
            run_status="completed",
            carry_state="closed",
            state_history=["planned", "first_leg_filled", "hedged", "closing", "closed"],
            signal=signal,
            final_net_exposure_usdt=0,
        )


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


def test_carry_execution_api_persists_closed_dual_leg_result(api_client, monkeypatch) -> None:
    from apps.api.routers import runs as runs_router

    monkeypatch.setattr(settings, "binance_use_testnet", True)
    monkeypatch.setattr(settings, "live_trading_enabled", False)
    monkeypatch.setattr(settings, "binance_api_key", "future-key")
    monkeypatch.setattr(settings, "binance_api_secret", "future-secret")
    monkeypatch.setattr(settings, "spot_testnet_api_key", "")
    monkeypatch.setattr(settings, "spot_testnet_api_secret", "")
    monkeypatch.setattr(settings, "binance_https_proxy", "")
    monkeypatch.setattr(runs_router, "_carry_execution_service", lambda: StubCarryService())
    monkeypatch.setattr(runs_router, "_carry_signal", lambda db, body: _signal())

    response = api_client.post(
        "/api/v1/execution/carry-executions",
        json={"notional_usdt": 1000, "idempotency_key": "carry-api-test"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["run_status"] == "completed"
    assert body["carry_state"] == "closed"

    fetched = api_client.get(f"/api/v1/execution/carry-executions/{body['run_id']}")
    assert fetched.status_code == 200
    assert fetched.json()["state_history"][-1] == "closed"


def test_carry_execution_api_uses_existing_demo_credentials_for_spot(api_client, monkeypatch) -> None:
    from apps.api.routers import runs as runs_router

    monkeypatch.setattr(settings, "binance_use_testnet", True)
    monkeypatch.setattr(settings, "live_trading_enabled", False)
    monkeypatch.setattr(settings, "binance_api_key", "future-key")
    monkeypatch.setattr(settings, "binance_api_secret", "future-secret")
    monkeypatch.setattr(settings, "spot_testnet_api_key", "")
    monkeypatch.setattr(settings, "spot_testnet_api_secret", "")
    monkeypatch.setattr(settings, "binance_https_proxy", "http://127.0.0.1:7890")

    monkeypatch.setattr(runs_router, "_carry_execution_service", lambda: StubCarryService())
    monkeypatch.setattr(runs_router, "_carry_signal", lambda db, body: _signal())

    response = api_client.post("/api/v1/execution/carry-executions", json={"notional_usdt": 1000})

    assert response.status_code == 201
    assert response.json()["run_status"] == "completed"
