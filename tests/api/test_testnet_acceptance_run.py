from shared.config import settings
from shared.models import TestnetAcceptanceRunResult as AcceptanceResult


class StubAcceptanceService:
    def run(self, request):  # noqa: ANN001
        return AcceptanceResult(
            run_status="completed",
            requested_symbols=["BTC/USDT"],
            completed_symbols=["BTC/USDT"],
            filled_order_count=2,
        )


def test_testnet_acceptance_api_persists_result_and_exposes_status(api_client, monkeypatch) -> None:
    from apps.api.routers import runs as runs_router

    monkeypatch.setattr(settings, "binance_use_testnet", True)
    monkeypatch.setattr(settings, "live_trading_enabled", False)
    monkeypatch.setattr(settings, "binance_api_key", "key")
    monkeypatch.setattr(settings, "binance_api_secret", "secret")
    monkeypatch.setattr(runs_router, "_testnet_acceptance_service", lambda: StubAcceptanceService())

    response = api_client.post(
        "/api/v1/execution/testnet-acceptance-runs",
        json={"symbols": ["BTC/USDT"], "idempotency_key": "acceptance-api-test"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["run_status"] == "completed"
    assert body["result"]["filled_order_count"] == 2

    fetched = api_client.get(f"/api/v1/execution/testnet-acceptance-runs/{body['run_id']}")
    assert fetched.status_code == 200
    assert fetched.json()["result"]["completed_symbols"] == ["BTC/USDT"]


def test_testnet_acceptance_api_accepts_direct_demo_network(api_client, monkeypatch) -> None:
    from apps.api.routers import runs as runs_router

    monkeypatch.setattr(settings, "binance_use_testnet", True)
    monkeypatch.setattr(settings, "live_trading_enabled", False)
    monkeypatch.setattr(settings, "binance_api_key", "key")
    monkeypatch.setattr(settings, "binance_api_secret", "secret")
    monkeypatch.setattr(settings, "binance_https_proxy", "")
    monkeypatch.setattr(runs_router, "_testnet_acceptance_service", lambda: StubAcceptanceService())

    response = api_client.post("/api/v1/execution/testnet-acceptance-runs", json={})

    assert response.status_code == 201
    assert response.json()["run_status"] == "completed"
