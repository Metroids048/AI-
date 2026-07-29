"""Task 13: Runtime Truth API tests (plan section 13 / Gate 13).

Gate 13:
- /runtime never returns placeholder zeros for unavailable data.
- Exchange and Local Projection are returned separately.
- Decision reason, incidents, and LLM token records are accessible.
- Entry control endpoints work and audit the change.
- Legacy API does not bleed into the V2 runtime response.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.api.routers import automated_trading
from services.automated_trading.domain.enums import V2ExecutionMode
from services.automated_trading.infrastructure.repository import AutomatedTradingRepository
from services.database import get_db_session
from services.strategy_library import LlmInvocationRepository
from shared.models import LlmInvocation, LlmInvocationStage


@pytest.fixture
def v2_client(db_session) -> TestClient:
    app = FastAPI()
    app.include_router(automated_trading.router)

    def _override_db():
        yield db_session

    app.dependency_overrides[get_db_session] = _override_db
    return TestClient(app, raise_server_exceptions=True)


def _seed_exchange_snapshot(repo: AutomatedTradingRepository) -> None:
    repo.record_reconciliation(
        execution_mode=V2ExecutionMode.BINANCE_TESTNET,
        exchange_positions={"positions": [{"symbol": "BTC/USDT", "qty": "0.001"}]},
        exchange_open_orders={"open_orders": []},
        local_positions={"positions": []},
        discrepancies={"mismatches": []},
        status="HEALTHY",
        captured_at=datetime.now(UTC),
    )
    repo.commit()


# ---------------------------------------------------------------------------
# /runtime
# ---------------------------------------------------------------------------


class TestRuntimeEndpoint:
    def test_returns_200(self, v2_client: TestClient) -> None:
        resp = v2_client.get("/api/v2/automated-trading/runtime")
        assert resp.status_code == 200

    def test_engine_fields_present(self, v2_client: TestClient) -> None:
        resp = v2_client.get("/api/v2/automated-trading/runtime")
        body = resp.json()
        engine = body["engine"]
        assert engine["engine_id"] == "automated-trading-v2"
        assert engine["mainnet_supported"] is False
        assert "activation" in engine
        assert "entry_enabled" in engine
        assert engine["entry_enabled"] is False

    def test_exchange_unavailable_does_not_return_zero(self, v2_client: TestClient) -> None:
        """When no exchange snapshot exists, available=False, not 0."""
        resp = v2_client.get("/api/v2/automated-trading/runtime")
        body = resp.json()
        exchange = body["exchange"]
        assert exchange["available"] is False
        assert exchange["positions"] == []

    def test_exchange_available_when_snapshot_present(self, v2_client: TestClient, db_session) -> None:
        repo = AutomatedTradingRepository(db_session)
        _seed_exchange_snapshot(repo)
        resp = v2_client.get("/api/v2/automated-trading/runtime")
        body = resp.json()
        assert body["exchange"]["available"] is True
        assert len(body["exchange"]["positions"]) == 1

    def test_local_projection_separate_from_exchange(self, v2_client: TestClient) -> None:
        resp = v2_client.get("/api/v2/automated-trading/runtime")
        body = resp.json()
        assert "local_projection" in body
        assert "exchange" in body
        assert body["local_projection"]["source"] == "V2_LOCAL_PROJECTION"

    def test_reconciliation_unavailable_by_default(self, v2_client: TestClient) -> None:
        resp = v2_client.get("/api/v2/automated-trading/runtime")
        body = resp.json()
        assert body["reconciliation"]["status"] == "UNAVAILABLE"

    def test_reconciliation_reflects_persisted_snapshot(self, v2_client: TestClient, db_session) -> None:
        repo = AutomatedTradingRepository(db_session)
        _seed_exchange_snapshot(repo)
        resp = v2_client.get("/api/v2/automated-trading/runtime")
        body = resp.json()
        assert body["reconciliation"]["status"] == "HEALTHY"

    def test_decisions_empty_by_default(self, v2_client: TestClient) -> None:
        resp = v2_client.get("/api/v2/automated-trading/runtime")
        assert resp.json()["latest_decisions"] == []

    def test_decisions_returned_from_db(self, v2_client: TestClient, db_session) -> None:
        repo = AutomatedTradingRepository(db_session)
        repo.create_cycle(
            cycle_id="cycle-1",
            symbol="BTC/USDT",
            timeframe="15m",
            bar_timestamp=datetime(2026, 7, 28, 10, 0, tzinfo=UTC),
            execution_mode=V2ExecutionMode.BINANCE_TESTNET,
            fencing_token="fence-1",
        )
        repo.complete_cycle(
            cycle_id="cycle-1",
            decision_terminal="ENTRY_SIGNAL_EVALUATED",
            completed_at=datetime(2026, 7, 28, 10, 1, tzinfo=UTC),
        )
        repo.create_decision(
            decision_id="decision-1",
            cycle_id="cycle-1",
            terminal_reason="NO_ENTRY_SIGNAL",
            payload={"exchange_submitted": False},
        )
        repo.commit()
        resp = v2_client.get("/api/v2/automated-trading/runtime")
        decisions = resp.json()["latest_decisions"]
        assert len(decisions) == 1
        assert decisions[0]["reason_code"] == "NO_ENTRY_SIGNAL"

    def test_llm_invocation_null_when_absent(self, v2_client: TestClient) -> None:
        resp = v2_client.get("/api/v2/automated-trading/runtime")
        assert resp.json()["latest_llm_invocation"] is None

    def test_llm_invocation_returned_when_present(self, v2_client: TestClient, db_session) -> None:
        LlmInvocationRepository(db_session).create_invocation(
            LlmInvocation(
                cycle_id="cycle-llm",
                symbol="BTC/USDT",
                called=True,
                provider="anthropic",
                model="claude-haiku-4-5-20251001",
                stage=LlmInvocationStage.MARKET_REVIEW,
                status="CALLED",
                total_tokens=120,
            )
        )
        resp = v2_client.get("/api/v2/automated-trading/runtime")
        llm = resp.json()["latest_llm_invocation"]
        assert llm is not None
        assert llm["status"] == "CALLED"
        assert llm["total_tokens"] == 120


# ---------------------------------------------------------------------------
# /positions
# ---------------------------------------------------------------------------


class TestPositionsEndpoint:
    def test_exchange_and_local_are_separate(self, v2_client: TestClient) -> None:
        resp = v2_client.get("/api/v2/automated-trading/positions")
        body = resp.json()
        assert "exchange" in body
        assert "local_projection" in body

    def test_exchange_positions_from_snapshot(self, v2_client: TestClient, db_session) -> None:
        repo = AutomatedTradingRepository(db_session)
        repo.record_reconciliation(
            execution_mode=V2ExecutionMode.BINANCE_TESTNET,
            exchange_positions={"positions": [{"symbol": "ETH/USDT", "qty": "0.01"}]},
            exchange_open_orders={"open_orders": []},
            local_positions={"positions": []},
            discrepancies={},
            status="HEALTHY",
            captured_at=datetime.now(UTC),
        )
        repo.commit()
        resp = v2_client.get("/api/v2/automated-trading/positions")
        body = resp.json()
        assert body["exchange"]["available"] is True
        assert len(body["exchange"]["positions"]) == 1

    def test_exchange_unavailable_shows_no_positions(self, v2_client: TestClient) -> None:
        resp = v2_client.get("/api/v2/automated-trading/positions")
        body = resp.json()
        assert body["exchange"]["available"] is False


# ---------------------------------------------------------------------------
# /controls
# ---------------------------------------------------------------------------


class TestEntryControls:
    def test_disable_entry(self, v2_client: TestClient, db_session) -> None:
        repo = AutomatedTradingRepository(db_session)
        repo.set_runtime_control(
            scope="global",
            entry_enabled=True,
            reason="seed",
            updated_by="test",
        )
        repo.commit()
        resp = v2_client.post(
            "/api/v2/automated-trading/controls/entry-disable",
            json={"reason": "manual test disable"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["entry_enabled"] is False
        assert body["reason"] == "manual test disable"
        assert "changed_at" in body
        assert repo.get_runtime_control("global").entry_enabled is False

    def test_enable_entry(self, v2_client: TestClient, db_session) -> None:
        repo = AutomatedTradingRepository(db_session)
        assert repo.get_runtime_control("global").entry_enabled is False
        resp = v2_client.post(
            "/api/v2/automated-trading/controls/entry-enable",
            json={"reason": "cleared manually"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["entry_enabled"] is True
        assert repo.get_runtime_control("global").entry_enabled is True

    def test_disable_entry_reflected_in_runtime(self, v2_client: TestClient, db_session) -> None:
        repo = AutomatedTradingRepository(db_session)
        repo.set_runtime_control(scope="global", entry_enabled=True, reason="seed", updated_by="test")
        repo.commit()
        v2_client.post(
            "/api/v2/automated-trading/controls/entry-disable",
            json={"reason": "risk event"},
        )
        runtime_resp = v2_client.get("/api/v2/automated-trading/runtime")
        assert runtime_resp.json()["engine"]["entry_enabled"] is False

    def test_runtime_entry_enabled_after_re_enable(self, v2_client: TestClient) -> None:
        v2_client.post("/api/v2/automated-trading/controls/entry-disable", json={"reason": "test"})
        v2_client.post("/api/v2/automated-trading/controls/entry-enable", json={"reason": "cleared"})
        runtime_resp = v2_client.get("/api/v2/automated-trading/runtime")
        assert runtime_resp.json()["engine"]["entry_enabled"] is True


# ---------------------------------------------------------------------------
# /cycles, /decisions, /incidents, /llm-invocations
# ---------------------------------------------------------------------------


class TestListEndpoints:
    def test_cycles_empty(self, v2_client: TestClient) -> None:
        assert v2_client.get("/api/v2/automated-trading/cycles").json() == []

    def test_decisions_empty(self, v2_client: TestClient) -> None:
        assert v2_client.get("/api/v2/automated-trading/decisions").json() == []

    def test_incidents_empty(self, v2_client: TestClient) -> None:
        assert v2_client.get("/api/v2/automated-trading/incidents").json() == []

    def test_llm_invocations_empty(self, v2_client: TestClient) -> None:
        assert v2_client.get("/api/v2/automated-trading/llm-invocations").json() == []

    def test_evidence_latest_no_data(self, v2_client: TestClient) -> None:
        body = v2_client.get("/api/v2/automated-trading/evidence/latest").json()
        assert body["available"] is False
