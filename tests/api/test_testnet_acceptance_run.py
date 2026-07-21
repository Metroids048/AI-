from services.strategy_library import ExecutionRepository
from shared.config import settings
from shared.models import (
    BinanceTestnetAccountStatus,
)
from shared.models import (
    TestnetAcceptanceOrderEvidence as AcceptanceOrderEvidence,
)
from shared.models import (
    TestnetAcceptanceRunResult as AcceptanceResult,
)


class StubAcceptanceService:
    def run(self, request):  # noqa: ANN001
        return AcceptanceResult(
            run_status="completed",
            requested_symbols=["BTC/USDT"],
            completed_symbols=["BTC/USDT"],
            filled_order_count=2,
            orders=[
                AcceptanceOrderEvidence(
                    gateway_order_id="acceptance-open-1",
                    gateway_status="filled",
                    symbol="BTC/USDT",
                    side="buy",
                    action="open",
                    quantity=0.01,
                    requested_notional=600.0,
                    leverage=5.0,
                    reduce_only=False,
                ),
                AcceptanceOrderEvidence(
                    gateway_order_id="acceptance-close-1",
                    gateway_status="filled",
                    symbol="BTC/USDT",
                    side="sell",
                    action="close",
                    quantity=0.01,
                    requested_notional=600.0,
                    leverage=5.0,
                    reduce_only=True,
                ),
            ],
        )


def test_testnet_acceptance_api_persists_result_and_exposes_status(api_client, db_session, monkeypatch) -> None:
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
    orders = ExecutionRepository(db_session).list_orders()
    assert [order.gateway_order_id for order in orders] == ["acceptance-open-1", "acceptance-close-1"]
    assert {order.entry_context["execution_kind"] for order in orders} == {"testnet_acceptance"}

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


def test_execution_scope_acceptance_arms_only_validated_directional_run(api_client, db_session, monkeypatch) -> None:
    from apps.api.routers import runs as runs_router
    from services.data.universe import AUTO_SIMULATION_EXECUTION_SYMBOLS
    from services.execution.bootstrap import AUTO_PAPER_TECHNICAL_KEY, SIGNAL_OBSERVATION_RUNTIME_KEY
    from services.strategy_library import PaperRunRepository
    from shared.models import PaperRun

    symbols = list(AUTO_SIMULATION_EXECUTION_SYMBOLS)

    class FullAcceptanceService:
        def run(self, request):  # noqa: ANN001
            return AcceptanceResult(
                run_status="completed",
                requested_symbols=symbols,
                completed_symbols=symbols,
                filled_order_count=2 * len(symbols),
                final_open_position_count=0,
                final_open_order_count=0,
            )

    monkeypatch.setattr(settings, "binance_use_testnet", True)
    monkeypatch.setattr(settings, "live_trading_enabled", False)
    monkeypatch.setattr(settings, "binance_api_key", "key")
    monkeypatch.setattr(settings, "binance_api_secret", "secret")
    monkeypatch.setattr(runs_router, "_testnet_acceptance_service", lambda: FullAcceptanceService())
    technical_run = PaperRunRepository(db_session).create_paper_run(
        PaperRun(
            strategy_id="technical-strategy",
            execution_profile={
                "auto_paper_runtime_key": AUTO_PAPER_TECHNICAL_KEY,
                "execution_mode": "paper_only",
                "mirror_to_gateway": False,
                "cost_gate_verified": False,
            },
            paper_status="running",
        )
    )
    observation_run = PaperRunRepository(db_session).create_paper_run(
        PaperRun(
            strategy_id="observation-strategy",
            execution_profile={
                "auto_paper_runtime_key": SIGNAL_OBSERVATION_RUNTIME_KEY,
                "execution_mode": "paper_only",
                "mirror_to_gateway": False,
                "cost_gate_verified": False,
            },
            paper_status="running",
        )
    )

    response = api_client.post("/api/v1/execution/testnet-acceptance-runs", json={"symbols": symbols})

    assert response.status_code == 201
    technical = PaperRunRepository(db_session).get_paper_run(technical_run.paper_run_id or "")
    observation = PaperRunRepository(db_session).get_paper_run(observation_run.paper_run_id or "")
    assert technical is not None
    assert observation is not None
    assert technical.execution_profile["cost_gate_verified"] is True
    assert technical.execution_profile["execution_mode"] == "binance_simulation_first"
    assert technical.execution_profile["mirror_to_gateway"] is True
    assert technical.execution_profile["acceptance_symbols"] == symbols
    assert technical.execution_profile["acceptance_scope_hash"]
    assert observation.execution_profile["cost_gate_verified"] is False
    assert observation.execution_profile["execution_mode"] == "paper_only"
    assert observation.execution_profile["mirror_to_gateway"] is False

    from services.strategy_library import ConfigSnapshotRepository

    active = ConfigSnapshotRepository(db_session).get_active(technical.paper_run_id or "")
    assert active is not None
    assert active.config["execution_profile"]["execution_mode"] == "binance_simulation_first"


def test_binance_testnet_account_probe_persists_a_local_balance_snapshot(api_client, db_session, monkeypatch) -> None:
    from apps.api.routers import runs as runs_router

    monkeypatch.setattr(
        runs_router,
        "probe_testnet_account",
        lambda: BinanceTestnetAccountStatus(
            connected=True,
            wallet_balance=10_870.04,
            available_balance=10_620.04,
            unrealized_pnl=-3.5,
            open_position_count=2,
            api_backend="demo",
        ),
    )

    response = api_client.get("/api/v1/execution/binance-testnet-account")

    assert response.status_code == 200
    snapshots = ExecutionRepository(db_session).list_account_snapshots()
    assert len(snapshots) == 1
    assert snapshots[0].wallet_balance == 10_870.04
    assert snapshots[0].available_balance == 10_620.04
    assert snapshots[0].source_ref == "binance_demo_testnet_api"
