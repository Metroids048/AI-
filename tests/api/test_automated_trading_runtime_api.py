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

from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.api.routers.automated_trading import _runtime_state, router

# Build a minimal test app with just this router
_app = FastAPI()
_app.include_router(router)
client = TestClient(_app, raise_server_exceptions=True)


def _reset_state() -> None:
    _runtime_state.clear()
    _runtime_state.update(
        {
            "engine_id": "automated-trading-v2",
            "mode": "BINANCE_TESTNET",
            "activation": "DISABLED",
            "entry_enabled": True,
            "entry_disabled_reason": None,
            "last_cycle_at": None,
            "cycles": [],
            "decisions": [],
            "incidents": [],
            "llm_invocations": [],
        }
    )


# ---------------------------------------------------------------------------
# /runtime
# ---------------------------------------------------------------------------


class TestRuntimeEndpoint:
    def setup_method(self) -> None:
        _reset_state()

    def test_returns_200(self) -> None:
        resp = client.get("/api/v2/automated-trading/runtime")
        assert resp.status_code == 200

    def test_engine_fields_present(self) -> None:
        resp = client.get("/api/v2/automated-trading/runtime")
        body = resp.json()
        engine = body["engine"]
        assert engine["engine_id"] == "automated-trading-v2"
        assert engine["mainnet_supported"] is False
        assert "activation" in engine
        assert "entry_enabled" in engine

    def test_exchange_unavailable_does_not_return_zero(self) -> None:
        """When no exchange snapshot exists, available=False, not 0."""
        resp = client.get("/api/v2/automated-trading/runtime")
        body = resp.json()
        exchange = body["exchange"]
        assert exchange["available"] is False
        # positions should be an empty list, not a fabricated zero
        assert exchange["positions"] == []

    def test_exchange_available_when_snapshot_present(self) -> None:
        _runtime_state["exchange_snapshot"] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "positions": [{"symbol": "BTC/USDT", "qty": "0.001"}],
            "open_orders": [],
        }
        resp = client.get("/api/v2/automated-trading/runtime")
        body = resp.json()
        assert body["exchange"]["available"] is True
        assert len(body["exchange"]["positions"]) == 1

    def test_local_projection_separate_from_exchange(self) -> None:
        resp = client.get("/api/v2/automated-trading/runtime")
        body = resp.json()
        assert "local_projection" in body
        assert "exchange" in body
        # They are distinct objects
        assert body["local_projection"]["source"] == "V2_LOCAL_PROJECTION"

    def test_reconciliation_unavailable_by_default(self) -> None:
        resp = client.get("/api/v2/automated-trading/runtime")
        body = resp.json()
        assert body["reconciliation"]["status"] == "UNAVAILABLE"

    def test_reconciliation_reflects_injected_state(self) -> None:
        now = datetime.now(UTC).isoformat()
        _runtime_state["reconciliation"] = {
            "status": "HEALTHY",
            "mismatches": [],
            "entry_blocked_symbols": [],
            "last_run_at": now,
        }
        resp = client.get("/api/v2/automated-trading/runtime")
        body = resp.json()
        assert body["reconciliation"]["status"] == "HEALTHY"

    def test_decisions_empty_by_default(self) -> None:
        resp = client.get("/api/v2/automated-trading/runtime")
        assert resp.json()["latest_decisions"] == []

    def test_decisions_returned_from_state(self) -> None:
        _runtime_state["decisions"] = [
            {
                "symbol": "BTC/USDT",
                "terminal_stage": "ENTRY_SIGNAL_EVALUATED",
                "reason_code": "NO_ENTRY_SIGNAL",
                "evaluated_at": datetime.now(UTC).isoformat(),
                "candidate_id": None,
                "exchange_submitted": False,
            }
        ]
        resp = client.get("/api/v2/automated-trading/runtime")
        decisions = resp.json()["latest_decisions"]
        assert len(decisions) == 1
        assert decisions[0]["reason_code"] == "NO_ENTRY_SIGNAL"

    def test_llm_invocation_null_when_absent(self) -> None:
        resp = client.get("/api/v2/automated-trading/runtime")
        assert resp.json()["latest_llm_invocation"] is None

    def test_llm_invocation_returned_when_present(self) -> None:
        now = datetime.now(UTC).isoformat()
        _runtime_state["llm_invocations"] = [
            {
                "stage": "MARKET_REVIEW",
                "status": "CALLED",
                "provider": "anthropic",
                "model": "claude-haiku-4-5-20251001",
                "total_tokens": 120,
                "error_code": None,
                "skip_reason": None,
                "invoked_at": now,
            }
        ]
        resp = client.get("/api/v2/automated-trading/runtime")
        llm = resp.json()["latest_llm_invocation"]
        assert llm is not None
        assert llm["status"] == "CALLED"
        assert llm["total_tokens"] == 120


# ---------------------------------------------------------------------------
# /positions
# ---------------------------------------------------------------------------


class TestPositionsEndpoint:
    def setup_method(self) -> None:
        _reset_state()

    def test_exchange_and_local_are_separate(self) -> None:
        resp = client.get("/api/v2/automated-trading/positions")
        body = resp.json()
        assert "exchange" in body
        assert "local_projection" in body

    def test_exchange_positions_from_snapshot(self) -> None:
        _runtime_state["exchange_snapshot"] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "positions": [{"symbol": "ETH/USDT", "qty": "0.01"}],
            "open_orders": [],
        }
        resp = client.get("/api/v2/automated-trading/positions")
        body = resp.json()
        assert body["exchange"]["available"] is True
        assert len(body["exchange"]["positions"]) == 1

    def test_exchange_unavailable_shows_no_positions(self) -> None:
        resp = client.get("/api/v2/automated-trading/positions")
        body = resp.json()
        assert body["exchange"]["available"] is False


# ---------------------------------------------------------------------------
# /controls
# ---------------------------------------------------------------------------


class TestEntryControls:
    def setup_method(self) -> None:
        _reset_state()

    def test_disable_entry(self) -> None:
        resp = client.post(
            "/api/v2/automated-trading/controls/entry-disable",
            json={"reason": "manual test disable"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["entry_enabled"] is False
        assert body["reason"] == "manual test disable"
        assert "changed_at" in body

    def test_enable_entry(self) -> None:
        _runtime_state["entry_enabled"] = False
        resp = client.post(
            "/api/v2/automated-trading/controls/entry-enable",
            json={"reason": "cleared manually"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["entry_enabled"] is True

    def test_disable_entry_reflected_in_runtime(self) -> None:
        client.post(
            "/api/v2/automated-trading/controls/entry-disable",
            json={"reason": "risk event"},
        )
        runtime_resp = client.get("/api/v2/automated-trading/runtime")
        assert runtime_resp.json()["engine"]["entry_enabled"] is False

    def test_runtime_entry_enabled_after_re_enable(self) -> None:
        client.post("/api/v2/automated-trading/controls/entry-disable", json={"reason": "test"})
        client.post("/api/v2/automated-trading/controls/entry-enable", json={"reason": "cleared"})
        runtime_resp = client.get("/api/v2/automated-trading/runtime")
        assert runtime_resp.json()["engine"]["entry_enabled"] is True


# ---------------------------------------------------------------------------
# /cycles, /decisions, /incidents, /llm-invocations
# ---------------------------------------------------------------------------


class TestListEndpoints:
    def setup_method(self) -> None:
        _reset_state()

    def test_cycles_empty(self) -> None:
        assert client.get("/api/v2/automated-trading/cycles").json() == []

    def test_decisions_empty(self) -> None:
        assert client.get("/api/v2/automated-trading/decisions").json() == []

    def test_incidents_empty(self) -> None:
        assert client.get("/api/v2/automated-trading/incidents").json() == []

    def test_llm_invocations_empty(self) -> None:
        assert client.get("/api/v2/automated-trading/llm-invocations").json() == []

    def test_evidence_latest_no_data(self) -> None:
        body = client.get("/api/v2/automated-trading/evidence/latest").json()
        assert body["available"] is False
