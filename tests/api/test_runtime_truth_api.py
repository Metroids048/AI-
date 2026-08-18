from datetime import UTC, datetime, timedelta
from threading import Event, Thread
from time import monotonic, sleep
from types import SimpleNamespace

import pytest
from starlette.websockets import WebSocketDisconnect

from apps.api.config import settings
from apps.api.routers import runtime
from services.automated_trading.infrastructure.models import (
    V2ExecutionCycle,
    V2ExecutionDecision,
    V2ExecutionIntent,
    V2ManagedPosition,
)
from services.execution.runtime_state import write_external_scheduler_state
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


def _v2_position(*, position_id: str, symbol: str, direction: str, quantity: float) -> V2ManagedPosition:
    observed_at = datetime.now(UTC)
    return V2ManagedPosition(
        position_id=position_id,
        intent_id=f"intent-{position_id}",
        order_record_id=f"order-{position_id}",
        symbol=symbol,
        direction=direction,
        execution_mode="BINANCE_TESTNET",
        quantity=quantity,
        entry_price=65000,
        entry_fee=0,
        state="PROTECTED",
        projected_at=observed_at,
    )


def test_exchange_truth_marks_web_algo_order_as_external_manual() -> None:
    tagged = runtime._tag_order_ownership({"client_order_id": "web_algo_kkc57fil7l80zcai0qkdnkw"})

    assert tagged["ownership"] == "EXTERNAL_MANUAL_ORDER"
    assert tagged["system_owned"] is False


def test_exchange_truth_normalizes_raw_binance_client_algo_id() -> None:
    tagged = runtime._tag_order_ownership({"clientAlgoId": "A2S-raw-binance"})

    assert tagged["client_order_id"] == "A2S-raw-binance"
    assert tagged["ownership"] == "SYSTEM_V2_ORDER"
    assert tagged["system_owned"] is True


def test_external_baseline_requires_completed_capture_flag() -> None:
    scheduler = SimpleNamespace(
        external_baseline_captured=False,
        external_baseline_value={"BTC/USDT:short": "0.5"},
    )

    assert runtime._effective_external_baseline(scheduler) is None


def test_runtime_projection_id_ignores_observation_times_but_changes_with_facts() -> None:
    first_observed = datetime(2026, 8, 18, 0, 0, tzinfo=UTC)
    second_observed = first_observed.replace(minute=1)
    first_scheduler = SimpleNamespace(
        running=True,
        exchange_info_ready=True,
        data_fresh=True,
        execution_mode="BINANCE_TESTNET",
        execution_symbols=("BTC/USDT",),
        entry_enabled=True,
        entry_authorized=True,
        entry_authority="TESTNET_CANARY",
        trading_state="TRADING",
        external_baseline_captured=True,
        external_baseline_value={"BTC/USDT:short": "0.5"},
        heartbeat_at=first_observed,
    )
    second_scheduler = SimpleNamespace(**{**first_scheduler.__dict__, "heartbeat_at": second_observed})
    exchange = {
        "status": "available",
        "observed_at": first_observed.isoformat(),
        "value": {
            "positions": [{"symbol": "BTC/USDT", "side": "short", "contracts": 0.5}],
            "open_orders": [],
        },
    }
    later_exchange = {**exchange, "observed_at": second_observed.isoformat()}

    first = runtime._runtime_projection_id(exchange=exchange, scheduler=first_scheduler, local_open=[])
    same_facts = runtime._runtime_projection_id(exchange=later_exchange, scheduler=second_scheduler, local_open=[])
    changed = runtime._runtime_projection_id(
        exchange={
            **later_exchange,
            "value": {
                **later_exchange["value"],
                "positions": [{"symbol": "BTC/USDT", "side": "short", "contracts": 0.6}],
            },
        },
        scheduler=second_scheduler,
        local_open=[],
    )
    identity_changed = runtime._runtime_projection_id(
        exchange=later_exchange,
        scheduler=second_scheduler,
        local_open=[],
        db_fingerprint={"fills": [{"trade_id": "trade-2"}]},
    )

    assert same_facts == first
    assert changed != first
    assert identity_changed != first


def test_runtime_snapshot_exposes_shared_reconciliation_and_live_protection_truth(
    api_client,
    monkeypatch,
) -> None:
    observed_at = datetime.now(UTC)
    monkeypatch.setattr(
        runtime,
        "_exchange_truth",
        lambda: runtime._datum(
            value={
                "positions": [
                    {
                        "symbol": "BTC/USDT:USDT",
                        "contracts": 0.5,
                        "side": "short",
                        "entry_price": 64000,
                        "mark_price": 64100,
                    }
                ],
                "open_orders": [],
            },
            source="BINANCE_USDT_M_TESTNET",
            observed_at=observed_at,
        ),
    )

    body = api_client.get("/api/v1/runtime/snapshot").json()

    assert body["reconciliation"]["value"]["status"] == "degraded"
    assert body["reconciliation"]["value"]["discrepancy_codes"] == ["EXCHANGE_ONLY_POSITION"]
    assert body["reconciliation"]["value"]["recovery_action"] == (
        "UNMANAGED_EXTERNAL_POSITION_REQUIRES_OPERATOR_ADOPTION"
    )
    protection = body["current_protection"]["value"]
    assert protection["unprotected_positions"] == 1
    assert protection["p0_unprotected"] is True


def test_runtime_truth_keeps_manual_baseline_out_of_reconciliation_and_managed_p0(
    api_client,
    db_session,
    monkeypatch,
    tmp_path,
) -> None:
    """Manual BTC and managed ETH share one-way account truth without cross-contamination."""
    observed_at = datetime.now(UTC)
    monkeypatch.setenv("LOCAL_SCHEDULER_STATE_PATH", str(tmp_path / "scheduler-state.json"))
    write_external_scheduler_state(
        {
            "running": True,
            "heartbeat_at": observed_at.isoformat(),
            "data_fresh": True,
            "exchange_info_ready": True,
            "external_baseline_captured": True,
            "external_baseline_value": {"BTC/USDT:short": "0.5"},
            "entry_authorized": True,
            "entry_authority": "TESTNET_CANARY",
            "trading_state": "TRADING",
        }
    )
    db_session.add(_v2_position(position_id="managed-eth", symbol="ETH/USDT", direction="long", quantity=1.0))
    db_session.commit()
    monkeypatch.setattr(
        runtime,
        "_exchange_truth",
        lambda: runtime._datum(
            value={
                "positions": [
                    {"symbol": "BTC/USDT", "contracts": 0.5, "side": "short"},
                    {"symbol": "ETH/USDT", "contracts": 1.0, "side": "long"},
                ],
                "open_orders": [
                    {
                        "symbol": "ETH/USDT",
                        "side": "sell",
                        "status": "NEW",
                        "order_type": "stop_market",
                        "quantity": 1.0,
                        "reduce_only": True,
                    },
                    {
                        "symbol": "ETH/USDT",
                        "side": "sell",
                        "status": "NEW",
                        "order_type": "take_profit_market",
                        "quantity": 1.0,
                        "reduce_only": True,
                    },
                ],
            },
            source="BINANCE_USDT_M_TESTNET",
            observed_at=observed_at,
        ),
    )

    snapshot = api_client.get("/api/v1/runtime/snapshot").json()
    positions = api_client.get("/api/v1/runtime/positions").json()
    reconciliation = api_client.get("/api/v1/runtime/reconciliation").json()
    no_trade = api_client.get("/api/v1/runtime/no-trade-summary").json()

    # This hand-built fixture has no durable V2 order/fill identity. The
    # canonical service must fail closed for that managed row, while the
    # captured BTC manual baseline remains outside managed protection/P0.
    assert snapshot["reconciliation"]["value"]["status"] == "recovery_required"
    assert positions["reconciliation"]["value"]["status"] == "recovery_required"
    assert reconciliation["status"] == "recovery_required"
    assert no_trade["reconciliation"]["status"] == "recovery_required"
    assert (
        len(
            {
                snapshot["projection_id"],
                positions["projection_id"],
                reconciliation["projection_id"],
                no_trade["projection_id"],
            }
        )
        == 1
    )
    ownership = snapshot["reconciliation"]["value"]["ownership"]
    assert ownership["external_manual_positions"] == [
        {
            "symbol": "BTC/USDT",
            "side": "short",
            "exchange_quantity": 0.5,
            "local_quantity": 0.0,
            "external_manual_quantity": 0.5,
            "managed_quantity": 0.0,
            "ownership": "EXTERNAL_MANUAL",
        }
    ]
    protection = snapshot["current_protection"]["value"]
    btc = next(item for item in protection["positions"] if item["symbol"] == "BTC/USDT")
    assert btc["ownership"] == "EXTERNAL_MANUAL"
    assert btc["protected"] is None
    assert protection["external_manual_positions"] == 1
    assert protection["p0_unprotected"] is False


def test_manual_baseline_drift_is_symbol_scoped_and_not_managed_p0(
    api_client,
    monkeypatch,
    tmp_path,
) -> None:
    """Closing a captured manual position pauses only that symbol."""
    observed_at = datetime.now(UTC)
    monkeypatch.setenv("LOCAL_SCHEDULER_STATE_PATH", str(tmp_path / "scheduler-state.json"))
    write_external_scheduler_state(
        {
            "running": True,
            "heartbeat_at": observed_at.isoformat(),
            "data_fresh": True,
            "exchange_info_ready": True,
            "external_baseline_captured": True,
            "external_baseline_value": {"BTC/USDT:short": "0.5346"},
            "external_baseline_lifecycle": "MANUAL_BASELINE_ACK_REQUIRED",
            "external_baseline_drift_keys": ["BTC/USDT:short"],
            "entry_authorized": True,
            "entry_authority": "TESTNET_CANARY",
            "trading_state": "TRADING",
        }
    )
    monkeypatch.setattr(
        runtime,
        "_exchange_truth",
        lambda: runtime._datum(
            value={"positions": [], "open_orders": []},
            source="BINANCE_USDT_M_TESTNET",
            observed_at=observed_at,
        ),
    )

    responses = [
        api_client.get("/api/v1/runtime/snapshot").json(),
        api_client.get("/api/v1/runtime/positions").json(),
        api_client.get("/api/v1/runtime/reconciliation").json(),
        api_client.get("/api/v1/runtime/no-trade-summary").json(),
    ]

    assert len({response["projection_id"] for response in responses}) == 1
    statuses = []
    for response in responses:
        reconciliation = response.get("reconciliation", {})
        value = reconciliation.get("value") if isinstance(reconciliation, dict) else None
        statuses.append(
            value.get("status") if isinstance(value, dict) else reconciliation.get("status") or response.get("status")
        )
    assert set(statuses) == {"degraded"}
    assert responses[0]["reconciliation"]["value"]["entry_blocked_symbols"] == ["BTC/USDT"]
    assert responses[0]["reconciliation"]["value"]["discrepancy_codes"] == ["MANUAL_BASELINE_DRIFT"]
    assert responses[0]["current_protection"]["value"]["p0_unprotected"] is False
    assert responses[2]["recovery_action"] == "MANUAL_BASELINE_ACK_REQUIRED"


def test_current_protection_truth_accepts_raw_binance_algo_order_fields() -> None:
    observed_at = datetime.now(UTC)
    result = runtime._current_protection_truth(
        exchange={
            "value": {
                "positions": [
                    {"symbol": "ETHUSDT", "positionAmt": "5.354", "side": "long"},
                ],
                "open_orders": [
                    {
                        "symbol": "ETHUSDT",
                        "side": "SELL",
                        "algoStatus": "NEW",
                        "orderType": "STOP_MARKET",
                        "quantity": "5.354",
                        "reduceOnly": True,
                        "clientAlgoId": "A2S-stop",
                        "algoId": "1001",
                    },
                    {
                        "symbol": "ETHUSDT",
                        "side": "SELL",
                        "algoStatus": "NEW",
                        "orderType": "TAKE_PROFIT_MARKET",
                        "quantity": "5.354",
                        "reduceOnly": "true",
                        "clientAlgoId": "A2T-target",
                        "algoId": "1002",
                    },
                ],
            },
            "status": "available",
        },
        observed_at=observed_at,
    )

    assert result["protected_positions"] == 1
    assert result["unprotected_positions"] == 0
    assert result["p0_unprotected"] is False
    position = result["positions"][0]
    assert position["symbol"] == "ETH/USDT"
    assert position["reduce_only"] is True
    assert position["stop_client_order_id"] == "A2S-stop"
    assert position["take_profit_exchange_order_id"] == "1002"


def test_runtime_snapshot_exposes_entry_paused_as_an_explicit_runtime_state(api_client, monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("LOCAL_SCHEDULER_STATE_PATH", str(tmp_path / "scheduler-state.json"))
    now = datetime.now(UTC)
    write_external_scheduler_state(
        {
            "running": True,
            "heartbeat_at": now.isoformat(),
            "entry_authority": "NONE",
            "entry_authorized": False,
            "entry_authority_reason": "production_pending",
            "production_authorization_state": "PENDING",
            "active_entry_strategy": None,
            "promotion_eligible": False,
            "trading_state": "ENTRY_PAUSED",
        }
    )

    body = api_client.get("/api/v1/runtime/snapshot").json()

    assert body["entry_runtime"]["status"] == "available"
    assert body["entry_runtime"]["value"] == {
        "entry_authority": "NONE",
        "entry_authorized": False,
        "entry_authority_reason": "production_pending",
        "production_authorization_state": "PENDING",
        "active_entry_strategy": None,
        "promotion_eligible": False,
        "trading_state": "ENTRY_PAUSED",
    }


def test_runtime_decisions_reads_persisted_v2_canary_fact(api_client, db_session) -> None:
    observed_at = datetime.now(UTC)
    db_session.add(
        V2ExecutionCycle(
            cycle_id="runtime-canary-cycle",
            symbol="BTC/USDT",
            timeframe="15m",
            bar_timestamp=observed_at,
            execution_mode="BINANCE_TESTNET",
            fencing_token="runtime-canary-fence",
            decision_terminal="RISK_APPROVED",
        )
    )
    db_session.add(
        V2ExecutionDecision(
            decision_id="runtime-canary-decision",
            cycle_id="runtime-canary-cycle",
            candidate_key="testnet_sampling_v2",
            terminal_reason="NO_ENTRY_SIGNAL",
            payload={
                "strategy_id": "testnet_sampling_v2",
                "candidate_key": "testnet_sampling_v2",
                "entry_authority": "TESTNET_CANARY",
                "terminal_stage": "RISK_APPROVED",
                "reason_code": "NO_ENTRY_SIGNAL",
                "stages": [{"stage": "EXCHANGE_SUBMITTED", "outcome": "REJECTED", "reason_code": "NO_ENTRY_SIGNAL"}],
            },
        )
    )
    db_session.commit()

    body = api_client.get("/api/v1/runtime/decisions?symbol=BTC/USDT").json()

    assert body["items"] == [
        {
            "decision_id": "runtime-canary-decision",
            "cycle_id": "runtime-canary-cycle",
            "symbol": "BTC/USDT",
            "created_at": body["items"][0]["created_at"],
            "last_decision_at": body["items"][0]["last_decision_at"],
            "strategy": "testnet_sampling_v2",
            "entry_authority": "TESTNET_CANARY",
            "signal_generated": True,
            "entry_gate_result": "RISK_APPROVED",
            "entry_submitted": False,
            "terminal_reason": "NO_ENTRY_SIGNAL",
        }
    ]


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


def test_runtime_snapshot_excludes_legacy_local_paper_positions_from_v2_truth(
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

    assert body["local_projection"]["value"] == []
    assert body["mismatch"]["value"]["consistent"] is True
    assert body["mismatch"]["value"]["local_only_positions"] == []


def test_runtime_snapshot_uses_v2_position_facts_not_stale_legacy_rows(
    api_client,
    db_session,
    monkeypatch,
) -> None:
    observed_at = datetime.now(UTC)
    monkeypatch.setattr(
        runtime,
        "_exchange_truth",
        lambda: runtime._datum(
            value={
                "positions": [{"symbol": "BTC/USDT", "side": "long", "contracts": 0.5}],
                "open_orders": [],
            },
            source="BINANCE_USDT_M_TESTNET",
            observed_at=observed_at,
        ),
    )
    db_session.add(_v2_position(position_id="v2-btc", symbol="BTC/USDT", direction="long", quantity=0.5))
    ExecutionRepository(db_session).create_position_record(
        PositionRecord(
            exchange_account="binance:usdt_perpetual:testnet",
            symbol="BTC/USDT",
            position_side=TradeSide.SHORT,
            opened_at=observed_at,
            quantity=99,
            order_origin="stale-legacy-projection",
            run_id="legacy-run",
            management_status=PositionManagementStatus.LEGACY_UNVERIFIED,
            execution_mode=ExecutionMode.BINANCE_TESTNET,
        )
    )
    db_session.commit()

    body = api_client.get("/api/v1/runtime/snapshot").json()

    assert body["local_projection"]["source"] == "V2_MANAGED_POSITION_FACTS"
    assert len(body["local_projection"]["value"]) == 1
    assert body["local_projection"]["value"][0]["position_record_id"] == "v2-btc"
    assert body["local_projection"]["value"][0]["position_side"] == "long"
    assert body["local_projection"]["value"][0]["quantity"] == 0.5
    assert body["mismatch"]["value"]["consistent"] is True


def test_runtime_reconciliation_detects_v2_side_and_quantity_mismatch(
    api_client,
    db_session,
    monkeypatch,
) -> None:
    observed_at = datetime.now(UTC)
    monkeypatch.setattr(
        runtime,
        "_exchange_truth",
        lambda: runtime._datum(
            value={
                "positions": [{"symbol": "ETH/USDT", "side": "long", "contracts": 1.0}],
                "open_orders": [],
            },
            source="BINANCE_USDT_M_TESTNET",
            observed_at=observed_at,
        ),
    )
    db_session.add(_v2_position(position_id="v2-eth", symbol="ETH/USDT", direction="short", quantity=2))
    db_session.commit()

    body = api_client.get("/api/v1/runtime/reconciliation").json()

    assert body["status"] == "recovery_required"
    assert body["mismatch"]["value"]["exchange_only_positions"] == [{"symbol": "ETH/USDT", "side": "long"}]
    assert body["mismatch"]["value"]["local_only_positions"] == [{"symbol": "ETH/USDT", "side": "short"}]
    assert body["entry_blocked_symbols"] == ["ETH/USDT"]


def test_runtime_reconciliation_reports_persisted_kill_switch_while_ignoring_legacy_unknown_order(
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
    execution_repo.create_exchange_order(
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
    assert body["unresolved_exchange_order_ids"] == []
    assert body["actions"] == [
        "ENTRY_KILL_SWITCH_ACTIVE",
    ]


def test_runtime_reconciliation_reports_v2_exchange_unknown_intent(
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
    db_session.add(
        V2ExecutionCycle(
            cycle_id="runtime-unknown-cycle",
            symbol="BTC/USDT",
            timeframe="15m",
            bar_timestamp=observed_at,
            execution_mode="BINANCE_TESTNET",
            fencing_token="runtime-unknown-fence",
        )
    )
    db_session.add(
        V2ExecutionIntent(
            intent_id="runtime-v2-unknown",
            cycle_id="runtime-unknown-cycle",
            symbol="BTC/USDT",
            direction="long",
            candidate_key="production-candidate",
            candidate_type="PRIMARY",
            execution_mode="BINANCE_TESTNET",
            decision_bar_timestamp=observed_at,
            state="EXCHANGE_UNKNOWN",
            version=0,
        )
    )
    db_session.commit()

    body = api_client.get("/api/v1/runtime/reconciliation").json()

    assert body["status"] == "degraded"
    assert body["entry_blocked_symbols"] == ["BTC/USDT"]
    assert body["actions"] == ["EXCHANGE_UNKNOWN_REQUIRES_RECONCILIATION"]
    assert body["unresolved_exchange_order_ids"] == ["runtime-v2-unknown"]


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
