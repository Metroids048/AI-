from datetime import UTC, datetime, timedelta
from threading import Event, Thread
from time import monotonic, sleep

import pytest
from starlette.websockets import WebSocketDisconnect

from apps.api.config import settings
from apps.api.routers import runtime
from services.strategy_library import LlmInvocationRepository, PaperRunRepository
from services.strategy_library.repository import ExecutionRepository
from shared.models import (
    ExchangeOrderRecord,
    ExchangeOrderState,
    LlmInvocation,
    LlmInvocationStage,
    OrderExecution,
    PaperRun,
    PositionManagementStatus,
    PositionRecord,
    TradeSide,
)
from shared.models.execution_truth import ExecutionMode


def test_runtime_exchange_truth_reuses_one_snapshot_for_parallel_endpoint_reads(monkeypatch) -> None:
    class Gateway:
        calls = 0

        def reconcile(self, *, live_run_id: str, include_account_summary: bool = False) -> dict:
            del live_run_id
            del include_account_summary
            self.calls += 1
            return {"open_positions": [], "open_orders": [], "reconciliation_status": "healthy"}

    gateway = Gateway()
    monkeypatch.setattr(runtime, "_exchange_cache", None)
    monkeypatch.setattr(runtime, "configured_gateways", lambda: [gateway])

    first = runtime._exchange_truth()
    second = runtime._exchange_truth()

    assert first == second
    assert gateway.calls == 1


def test_runtime_exchange_truth_includes_account_summary_in_exchange_value(monkeypatch) -> None:
    class Gateway:
        def reconcile(self, *, live_run_id: str, include_account_summary: bool = False) -> dict:
            del live_run_id
            assert include_account_summary is True
            return {
                "open_positions": [],
                "open_orders": [],
                "reconciliation_status": "healthy",
                "account": {
                    "wallet_balance": 12345.67,
                    "available_balance": 12000.0,
                    "margin_balance": 12310.42,
                    "unrealized_pnl": -35.25,
                    "open_position_count": 2,
                },
            }

    monkeypatch.setattr(runtime, "_exchange_cache", None)
    monkeypatch.setattr(runtime, "configured_gateways", lambda: [Gateway()])

    result = runtime._exchange_truth()

    assert result["status"] == "available"
    assert result["value"]["account"] == {
        "wallet_balance": 12345.67,
        "available_balance": 12000.0,
        "margin_balance": 12310.42,
        "unrealized_pnl": -35.25,
        "open_position_count": 2,
    }


def test_runtime_exchange_truth_times_out_as_unavailable(monkeypatch) -> None:
    class SlowGateway:
        def reconcile(self, *, live_run_id: str, include_account_summary: bool = False) -> dict:
            del live_run_id
            del include_account_summary
            sleep(0.1)
            return {"open_positions": [], "open_orders": []}

    monkeypatch.setattr(runtime, "_exchange_cache", None)
    monkeypatch.setattr(runtime, "_EXCHANGE_TRUTH_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(runtime, "configured_gateways", lambda: [SlowGateway()])

    result = runtime._exchange_truth()

    assert result["status"] == "unavailable"
    assert result["value"] is None
    assert "exchange truth probe exceeded" in result["error"]


def test_runtime_exchange_truth_returns_stale_when_probe_busy(monkeypatch) -> None:
    observed_at = datetime.now(UTC)
    cached = runtime._datum(
        value={"positions": [], "open_orders": []},
        source="BINANCE_USDT_M_TESTNET",
        observed_at=observed_at,
    )
    monkeypatch.setattr(runtime, "_exchange_cache", (observed_at, cached))
    monkeypatch.setattr(runtime, "_EXCHANGE_CACHE_SECONDS", 0.0)
    monkeypatch.setattr(runtime, "_EXCHANGE_TRUTH_TIMEOUT_SECONDS", 0.05)
    release = Event()

    class BlockedGateway:
        def reconcile(self, *, live_run_id: str, include_account_summary: bool = False) -> dict:
            del live_run_id
            del include_account_summary
            release.wait(timeout=5)
            return {"open_positions": [], "open_orders": []}

    monkeypatch.setattr(runtime, "configured_gateways", lambda: [BlockedGateway()])
    monkeypatch.setattr(runtime, "_exchange_inflight", None)
    try:
        result = runtime._exchange_truth()
    finally:
        release.set()
        # Drain the probe so it cannot hold the single worker into the next test.
        if runtime._exchange_inflight is not None:
            runtime._exchange_inflight.result(timeout=5)

    assert result["status"] == "stale"
    assert result["value"] == {"positions": [], "open_orders": []}


def test_runtime_exchange_truth_shares_one_probe_between_concurrent_callers(monkeypatch) -> None:
    """A cold page load polls snapshot/positions/reconciliation together.

    Each must get the same reconcile result instead of "probe already in progress".
    """
    started = Event()
    release = Event()

    class SlowGateway:
        calls = 0

        def reconcile(self, *, live_run_id: str, include_account_summary: bool = False) -> dict:
            del live_run_id
            del include_account_summary
            type(self).calls += 1
            started.set()
            release.wait(timeout=5)
            return {"open_positions": [], "open_orders": [], "reconciliation_status": "healthy"}

    monkeypatch.setattr(runtime, "_exchange_cache", None)
    monkeypatch.setattr(runtime, "_exchange_inflight", None)
    monkeypatch.setattr(runtime, "configured_gateways", lambda: [SlowGateway()])

    results: list[dict] = []
    threads = [Thread(target=lambda: results.append(runtime._exchange_truth())) for _ in range(3)]
    for thread in threads:
        thread.start()
    assert started.wait(timeout=5)
    release.set()
    for thread in threads:
        thread.join(timeout=10)

    assert SlowGateway.calls == 1
    assert len(results) == 3
    assert [item["status"] for item in results] == ["available"] * 3


def test_exchange_cache_window_outlives_the_console_poll_interval() -> None:
    """The console polls every 30s (FALLBACK_INTERVAL_MS in useRuntimeTruth.js).

    A cache window shorter than that interval means every poll expires the cache and
    pays full reconcile latency, so jitter randomly flips the account panel to
    "unavailable". Keep this invariant if either value changes.
    """
    console_poll_interval_seconds = 30.0
    assert console_poll_interval_seconds < runtime._EXCHANGE_CACHE_SECONDS
    assert console_poll_interval_seconds > runtime._EXCHANGE_BACKGROUND_REFRESH_SECONDS
    assert runtime._EXCHANGE_MAX_SERVE_SECONDS > runtime._EXCHANGE_CACHE_SECONDS


def test_runtime_exchange_truth_serves_cache_and_refreshes_in_background(monkeypatch) -> None:
    """A poll landing past the background-refresh age must return immediately from cache
    and trigger a refresh, never block on the reconcile."""
    aged_at = datetime.now(UTC) - timedelta(seconds=25)
    cached = runtime._datum(
        value={"positions": [], "open_orders": []},
        source="BINANCE_USDT_M_TESTNET",
        observed_at=aged_at,
    )
    release = Event()
    started = Event()

    class SlowGateway:
        def reconcile(self, *, live_run_id: str, include_account_summary: bool = False) -> dict:
            del live_run_id
            del include_account_summary
            started.set()
            release.wait(timeout=5)
            return {"open_positions": [], "open_orders": []}

    monkeypatch.setattr(runtime, "_exchange_cache", (aged_at, cached))
    monkeypatch.setattr(runtime, "_exchange_inflight", None)
    monkeypatch.setattr(runtime, "configured_gateways", lambda: [SlowGateway()])

    started_at = monotonic()
    result = runtime._exchange_truth()
    elapsed = monotonic() - started_at

    try:
        # Served from cache: must not wait on the slow reconcile.
        assert elapsed < 1.0, f"blocked on reconcile for {elapsed:.2f}s instead of serving cache"
        assert result["value"] == {"positions": [], "open_orders": []}
        # And a background refresh must have been kicked off.
        assert started.wait(timeout=5), "no background refresh was started"
    finally:
        release.set()
        if runtime._exchange_inflight is not None:
            runtime._exchange_inflight.result(timeout=5)


def test_runtime_exchange_truth_recovers_after_a_waiter_times_out(monkeypatch) -> None:
    """The bug this guards: a timed-out probe used to be cancelled and its result thrown
    away, so the single worker restarted from zero on every poll and the console stayed
    permanently unavailable. The probe must instead publish its own result.
    """
    release = Event()

    class SlowGateway:
        calls = 0

        def reconcile(self, *, live_run_id: str, include_account_summary: bool = False) -> dict:
            del live_run_id
            del include_account_summary
            type(self).calls += 1
            release.wait(timeout=5)
            return {"open_positions": [], "open_orders": [], "reconciliation_status": "healthy"}

    monkeypatch.setattr(runtime, "_exchange_cache", None)
    monkeypatch.setattr(runtime, "_exchange_inflight", None)
    monkeypatch.setattr(runtime, "_EXCHANGE_TRUTH_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(runtime, "configured_gateways", lambda: [SlowGateway()])

    timed_out = runtime._exchange_truth()
    assert timed_out["status"] == "unavailable"
    assert "exceeded" in timed_out["error"]

    release.set()
    inflight = runtime._exchange_inflight
    assert inflight is not None
    inflight.result(timeout=5)

    recovered = runtime._exchange_truth()
    assert recovered["status"] == "available"
    assert SlowGateway.calls == 1, "the slow probe must not be restarted from zero"


def test_runtime_snapshot_exposes_exchange_unavailable_without_zero_fallback(
    api_client,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        runtime,
        "_exchange_truth",
        lambda: {
            "value": None,
            "source": "BINANCE_USDT_M_TESTNET",
            "observed_at": datetime.now(UTC).isoformat(),
            "freshness": "unavailable",
            "status": "unavailable",
            "error": "gateway timeout",
        },
    )

    response = api_client.get("/api/v1/runtime/snapshot")

    assert response.status_code == 200
    body = response.json()
    assert body["exchange"]["status"] == "unavailable"
    assert body["exchange"]["value"] is None
    assert body["exchange"]["error"] == "gateway timeout"
    assert body["mismatch"]["status"] == "unavailable"
    assert body["mismatch"]["value"]["consistent"] is False


def test_runtime_snapshot_does_not_compare_local_paper_positions_to_binance(
    api_client,
    db_session,
    monkeypatch,
) -> None:
    observed_at = datetime.now(UTC)
    monkeypatch.setattr(
        runtime,
        "_exchange_truth",
        lambda: runtime._datum(
            value={"positions": [], "open_orders": []},
            source="BINANCE_USDT_M_TESTNET",
            observed_at=observed_at,
        ),
    )
    ExecutionRepository(db_session).create_position_record(
        PositionRecord(
            exchange_account="local:paper",
            symbol="SOL/USDT",
            position_side=TradeSide.SHORT,
            opened_at=observed_at,
            quantity=1,
            order_origin="local_paper_test",
            run_id="local-paper-run",
            management_status=PositionManagementStatus.PAPER_SIMULATION_ONLY,
            execution_mode=ExecutionMode.LOCAL_PAPER,
        )
    )

    body = api_client.get("/api/v1/runtime/snapshot").json()

    assert len(body["local_projection"]["value"]) == 1
    assert body["mismatch"]["value"]["consistent"] is True
    assert body["mismatch"]["value"]["local_only_positions"] == []


def test_runtime_reconciliation_reports_persisted_kill_switch_and_unknown_order(
    api_client,
    db_session,
    monkeypatch,
) -> None:
    observed_at = datetime.now(UTC)
    monkeypatch.setattr(
        runtime,
        "_exchange_truth",
        lambda: runtime._datum(
            value={"positions": [], "open_orders": []},
            source="BINANCE_USDT_M_TESTNET",
            observed_at=observed_at,
        ),
    )
    PaperRunRepository(db_session).create_paper_run(
        PaperRun(
            paper_run_id="runtime-kill-switch-run",
            strategy_id="runtime-kill-switch-strategy",
            candidate_symbols=["BTC/USDT", "ETH/USDT"],
            paper_metrics_summary={
                "entry_kill_switch_active": True,
                "reconciliation_consecutive_failures": 3,
            },
        )
    )
    execution_repo = ExecutionRepository(db_session)
    local_order = execution_repo.create_order(
        OrderExecution(
            strategy_id="runtime-kill-switch-strategy",
            symbol="BTC/USDT",
            direction=TradeSide.LONG,
            execution_status="EXCHANGE_UNKNOWN",
            paper_run_id="runtime-kill-switch-run",
        )
    )
    unknown = execution_repo.create_exchange_order(
        ExchangeOrderRecord(
            local_order_execution_id=local_order.order_execution_id or "",
            exchange_account="binance:usdt_perpetual:testnet",
            execution_mode=ExecutionMode.BINANCE_TESTNET,
            client_order_id="runtime-unknown-order",
            symbol="BTC/USDT",
            side="buy",
            state=ExchangeOrderState.EXCHANGE_UNKNOWN,
            requested_quantity=1,
        )
    )

    body = api_client.get("/api/v1/runtime/reconciliation").json()

    assert body["status"] == "degraded"
    assert set(body["entry_blocked_symbols"]) == {"BTC/USDT", "ETH/USDT"}
    assert body["entry_kill_switch_active"] is True
    assert body["consecutive_failures"] == 3
    assert body["unresolved_exchange_order_ids"] == [unknown.exchange_order_record_id]
    assert body["actions"] == [
        "ENTRY_KILL_SWITCH_ACTIVE",
        "EXCHANGE_UNKNOWN_REQUIRES_RECONCILIATION",
    ]


def test_runtime_reconciliation_stale_cache_does_not_force_full_entry_block(
    api_client,
    monkeypatch,
) -> None:
    observed_at = datetime.now(UTC)
    monkeypatch.setattr(
        runtime,
        "_exchange_truth",
        lambda: {
            "value": {"positions": [], "open_orders": []},
            "source": "BINANCE_USDT_M_TESTNET",
            "observed_at": observed_at.isoformat(),
            "freshness": "stale",
            "status": "stale",
            "error": "exchange truth probe already in progress",
        },
    )

    body = api_client.get("/api/v1/runtime/reconciliation").json()

    assert body["status"] == "degraded"
    assert body["entry_blocked_symbols"] == []
    assert body["entry_kill_switch_active"] is False


def test_runtime_llm_invocations_returns_persisted_skip_reason(
    api_client,
    db_session,
) -> None:
    invocation = LlmInvocationRepository(db_session).create_invocation(
        LlmInvocation(
            cycle_id="cycle-no-candidate",
            symbol="BTC/USDT",
            called=False,
            skip_reason="NO_DETERMINISTIC_CANDIDATE",
            stage=LlmInvocationStage.TRADE_REVIEW,
            status="skipped",
        )
    )

    response = api_client.get(
        "/api/v1/runtime/llm-invocations",
        params={"symbol": "BTC/USDT", "limit": 10},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "available"
    assert body["items"][0]["invocation_id"] == invocation.invocation_id
    assert body["items"][0]["called"] is False
    assert body["items"][0]["skip_reason"] == "NO_DETERMINISTIC_CANDIDATE"


def test_runtime_events_rejects_missing_websocket_token(api_client) -> None:
    with (
        pytest.raises(WebSocketDisconnect) as exc_info,
        api_client.websocket_connect("/api/v1/runtime/events"),
    ):
        pass

    assert exc_info.value.code == 1008


def test_runtime_events_accepts_valid_websocket_token(api_client) -> None:
    with api_client.websocket_connect(f"/api/v1/runtime/events?token={settings.admin_api_token}") as websocket:
        payload = websocket.receive_json()

    assert payload["event"] == "runtime_truth"
