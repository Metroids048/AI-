from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest

from services.data.universe import AUTO_SIMULATION_EXECUTION_SYMBOLS
from services.execution import runtime_state
from services.execution.runtime_state import (
    ExternalSchedulerState,
    load_external_scheduler_state,
    write_external_scheduler_state,
)
from services.execution.scheduler import (
    RuntimeScheduler,
    _aligned_run_delay_seconds,
    _default_exchange_info_refresh_runner,
    _preload_celery_task_api,
)


@pytest.fixture(autouse=True)
def _isolate_scheduler_state_file(monkeypatch, tmp_path) -> None:  # noqa: ANN001
    monkeypatch.setenv("LOCAL_SCHEDULER_STATE_PATH", str(tmp_path / "scheduler-state.json"))


def _raise_runtime_error(message: str) -> None:
    raise RuntimeError(message)


def test_scheduler_preloads_celery_task_api_before_starting_threads() -> None:
    _preload_celery_task_api()


def test_paper_cycle_alignment_stays_inside_pretrade_decision_age_window() -> None:
    now = datetime(2026, 7, 27, 10, 1, 46, tzinfo=UTC)

    delay = _aligned_run_delay_seconds(
        now=now,
        interval_seconds=300,
        offset_seconds=45,
    )

    assert delay == pytest.approx(239)
    assert now + timedelta(seconds=delay) == datetime(2026, 7, 27, 10, 5, 45, tzinfo=UTC)


def test_exchange_info_ready_uses_configured_fixed_universe_size(monkeypatch) -> None:
    from services.data import binance
    from services.data.universe import FIXED_TOP20_ASSETS
    from services.execution import bootstrap

    symbols = [
        {
            "symbol": item["exchange_symbol"],
            "status": "TRADING",
            "pricePrecision": 2,
            "quantityPrecision": 3,
            "filters": [{"filterType": "MIN_NOTIONAL", "notional": "5"}],
        }
        for item in FIXED_TOP20_ASSETS
    ]
    monkeypatch.setattr(binance, "resolve_usdm_public_rest_base", lambda: "https://testnet.example")
    monkeypatch.setattr(binance, "fetch_usdm_exchange_info_symbols", lambda: symbols)
    monkeypatch.setattr(bootstrap, "refresh_fixed_top20_runtime_universe", lambda _: 3)

    result = _default_exchange_info_refresh_runner()

    assert len(FIXED_TOP20_ASSETS) == 10
    assert result["ready"] is True
    assert result["updated_runs"] == 3


def test_external_scheduler_state_requires_fresh_heartbeat(monkeypatch, tmp_path) -> None:
    state_path = tmp_path / "scheduler-state.json"
    monkeypatch.setenv("LOCAL_SCHEDULER_STATE_PATH", str(state_path))
    now = datetime.now(UTC)
    write_external_scheduler_state(
        {
            "running": True,
            "heartbeat_at": (now - timedelta(seconds=121)).isoformat(),
            "top20_coverage_count": 20,
            "exchange_info_ready": True,
            "data_fresh": True,
            "last_auto_cycle_at": now.isoformat(),
        }
    )

    stale = load_external_scheduler_state(max_age_seconds=120, now=now)

    assert stale.running is False
    assert stale.reason == "scheduler_heartbeat_stale"

    write_external_scheduler_state(
        {
            "running": True,
            "heartbeat_at": now.isoformat(),
            "top20_coverage_count": 20,
            "exchange_info_ready": True,
            "data_fresh": True,
            "last_auto_cycle_at": now.isoformat(),
        }
    )
    healthy = load_external_scheduler_state(max_age_seconds=120, now=now)

    assert healthy.running is True
    assert healthy.top20_coverage_count == 20
    assert healthy.exchange_info_ready is True


def test_external_scheduler_state_exposes_actual_active_mode_contract(monkeypatch, tmp_path) -> None:
    assert hasattr(runtime_state, "active_startup_contract_errors")

    state_path = tmp_path / "scheduler-state.json"
    monkeypatch.setenv("LOCAL_SCHEDULER_STATE_PATH", str(state_path))
    now = datetime.now(UTC)
    write_external_scheduler_state(
        {
            "running": True,
            "heartbeat_at": now.isoformat(),
            "engine_activation": "ACTIVE",
            "execution_mode": "BINANCE_TESTNET",
            "execution_strategy_id": "production_strategy_pending",
            "execution_coverage_count": len(AUTO_SIMULATION_EXECUTION_SYMBOLS),
            "execution_symbols": list(AUTO_SIMULATION_EXECUTION_SYMBOLS),
            "registered_jobs": ["automated_trading_v2_cycle"],
            "legacy_writer_enabled": False,
            "entry_enabled": False,
            "sampling_fallback_enabled": False,
            "external_baseline_captured": True,
            "entry_authorized": False,
            "entry_authority": "NONE",
            "trading_state": "ENTRY_PAUSED",
            "startup_contract_errors": [],
        }
    )

    state = load_external_scheduler_state(now=now)

    assert state.engine_activation == "ACTIVE"
    assert state.execution_mode == "BINANCE_TESTNET"
    assert state.execution_strategy_id == "production_strategy_pending"
    assert state.registered_jobs == ("automated_trading_v2_cycle",)
    errors = runtime_state.active_startup_contract_errors(state, requested_engine="v2_active")
    assert "ENTRY_DISABLED" in errors
    assert "ENTRY_NOT_AUTHORIZED" in errors
    assert "ENTRY_AUTHORITY_INVALID" in errors
    assert "TRADING_STATE_NOT_TRADING" in errors


def test_active_startup_contract_accepts_testnet_canary_entry_ready_state() -> None:
    state = ExternalSchedulerState(
        running=True,
        heartbeat_at=datetime.now(UTC),
        engine_activation="ACTIVE",
        execution_mode="BINANCE_TESTNET",
        execution_strategy_id="testnet_sampling_candidate",
        execution_symbols=tuple(AUTO_SIMULATION_EXECUTION_SYMBOLS),
        execution_coverage_count=len(AUTO_SIMULATION_EXECUTION_SYMBOLS),
        registered_jobs=("automated_trading_v2_cycle",),
        legacy_writer_enabled=False,
        entry_enabled=True,
        sampling_fallback_enabled=True,
        external_baseline_captured=True,
        entry_authorized=True,
        entry_authority="TESTNET_CANARY",
        trading_state="TRADING",
    )

    assert runtime_state.active_startup_contract_errors(state) == ()


def test_active_startup_contract_accepts_sampling_strategy_when_canary_is_authority() -> None:
    """The Canary strategy ID is valid when its explicit authority is visible."""
    state = ExternalSchedulerState(
        running=True,
        heartbeat_at=datetime.now(UTC),
        engine_activation="ACTIVE",
        execution_mode="BINANCE_TESTNET",
        execution_strategy_id="testnet_sampling_v2",
        execution_symbols=tuple(AUTO_SIMULATION_EXECUTION_SYMBOLS),
        execution_coverage_count=len(AUTO_SIMULATION_EXECUTION_SYMBOLS),
        registered_jobs=("automated_trading_v2_cycle",),
        legacy_writer_enabled=False,
        entry_enabled=True,
        sampling_fallback_enabled=True,
        external_baseline_captured=True,
        entry_authorized=True,
        entry_authority="TESTNET_CANARY",
        production_authorization_state="PENDING",
        active_entry_strategy="testnet_sampling_v2",
        trading_state="TRADING",
    )

    assert runtime_state.active_startup_contract_errors(state) == ()


def test_active_startup_contract_accepts_approved_production_state() -> None:
    state = ExternalSchedulerState(
        running=True,
        heartbeat_at=datetime.now(UTC),
        engine_activation="ACTIVE",
        execution_mode="BINANCE_TESTNET",
        execution_strategy_id="approved_candidate",
        execution_symbols=tuple(AUTO_SIMULATION_EXECUTION_SYMBOLS),
        execution_coverage_count=len(AUTO_SIMULATION_EXECUTION_SYMBOLS),
        registered_jobs=("automated_trading_v2_cycle",),
        legacy_writer_enabled=False,
        entry_enabled=True,
        sampling_fallback_enabled=False,
        external_baseline_captured=True,
        entry_authorized=True,
        entry_authority="PRODUCTION",
        production_authorization_state="APPROVED",
        trading_state="TRADING",
    )

    assert runtime_state.active_startup_contract_errors(state) == ()


def test_active_startup_contract_rejects_canary_without_sampling_fallback() -> None:
    state = ExternalSchedulerState(
        running=True,
        heartbeat_at=datetime.now(UTC),
        engine_activation="ACTIVE",
        execution_mode="BINANCE_TESTNET",
        execution_strategy_id="testnet_sampling_candidate",
        execution_symbols=tuple(AUTO_SIMULATION_EXECUTION_SYMBOLS),
        execution_coverage_count=len(AUTO_SIMULATION_EXECUTION_SYMBOLS),
        registered_jobs=("automated_trading_v2_cycle",),
        legacy_writer_enabled=False,
        entry_enabled=True,
        sampling_fallback_enabled=False,
        external_baseline_captured=True,
        entry_authorized=True,
        entry_authority="TESTNET_CANARY",
        trading_state="TRADING",
    )

    assert "CANARY_SAMPLING_FALLBACK_DISABLED" in runtime_state.active_startup_contract_errors(state)


def test_active_strategy_identity_requires_full_execution_scope_approval(monkeypatch) -> None:
    """Five-symbol validation must never upgrade a narrower approval."""
    from services.automated_trading.application.production_strategy import ProductionAuthorization
    from services.execution import scheduler as scheduler_module

    class _Run:
        paper_run_id = "run-1"
        paper_status = "running"
        execution_profile = {"strategy_lane": "directional"}

    class _Snapshot:
        config: dict[str, dict[str, object]] = {"strategy_rules": {}}
        config_hash = "sha256:snapshot"

    class _PaperRunRepository:
        def __init__(self, _session) -> None:  # noqa: ANN001
            pass

        def list_paper_runs(self) -> list[_Run]:
            return [_Run()]

    class _ConfigSnapshotRepository:
        def __init__(self, _session) -> None:  # noqa: ANN001
            pass

        def get_active(self, _run_id: str) -> _Snapshot:
            return _Snapshot()

    class _Session:
        def __enter__(self) -> _Session:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    import services.strategy_library as strategy_library
    from services import database

    monkeypatch.setattr(database, "get_session_factory", lambda: lambda: _Session())
    monkeypatch.setattr(strategy_library, "PaperRunRepository", _PaperRunRepository)
    monkeypatch.setattr(strategy_library, "ConfigSnapshotRepository", _ConfigSnapshotRepository)

    approved_symbols = {"BTC/USDT", "ETH/USDT"}
    monkeypatch.setattr(
        "services.automated_trading.application.production_strategy.resolve_production_authorization",
        lambda *, snapshot_config, snapshot_hash, symbol: ProductionAuthorization(
            symbol in approved_symbols,
            "partial_scope_probe",
            candidate_id="trend_momentum_v2_enriched" if symbol in approved_symbols else None,
        ),
    )

    assert scheduler_module._active_execution_strategy_identity() == ("production_strategy_pending", False)

    monkeypatch.setattr(
        "services.automated_trading.application.production_strategy.resolve_production_authorization",
        lambda *, snapshot_config, snapshot_hash, symbol: ProductionAuthorization(
            True,
            "full_scope_probe",
            candidate_id="trend_momentum_v2_enriched",
        ),
    )

    assert scheduler_module._active_execution_strategy_identity() == ("trend_momentum_v2_enriched", True)


def test_pending_production_keeps_canary_authority_and_fails_closed_without_sampling() -> None:
    from services.automated_trading.application.production_strategy import EntryAuthority, resolve_entry_authority

    canary = resolve_entry_authority(
        production_authorized=False,
        production_strategy_id=None,
        execution_mode="BINANCE_TESTNET",
        operator_testnet_canary_enabled=True,
    )
    disabled = resolve_entry_authority(
        production_authorized=False,
        production_strategy_id=None,
        execution_mode="BINANCE_TESTNET",
        operator_testnet_canary_enabled=False,
    )

    canary_state = ExternalSchedulerState(
        running=True,
        heartbeat_at=datetime.now(UTC),
        engine_activation="ACTIVE",
        execution_mode="BINANCE_TESTNET",
        execution_strategy_id="production_strategy_pending",
        execution_symbols=tuple(AUTO_SIMULATION_EXECUTION_SYMBOLS),
        execution_coverage_count=len(AUTO_SIMULATION_EXECUTION_SYMBOLS),
        registered_jobs=("automated_trading_v2_cycle",),
        legacy_writer_enabled=False,
        entry_enabled=True,
        sampling_fallback_enabled=True,
        external_baseline_captured=True,
        entry_authorized=True,
        entry_authority=canary.authority.value,
        production_authorization_state="PENDING",
        trading_state="TRADING",
    )
    paused_state = ExternalSchedulerState(
        running=True,
        heartbeat_at=datetime.now(UTC),
        engine_activation="ACTIVE",
        execution_mode="BINANCE_TESTNET",
        execution_strategy_id="production_strategy_pending",
        execution_symbols=tuple(AUTO_SIMULATION_EXECUTION_SYMBOLS),
        execution_coverage_count=len(AUTO_SIMULATION_EXECUTION_SYMBOLS),
        registered_jobs=("automated_trading_v2_cycle",),
        legacy_writer_enabled=False,
        entry_enabled=True,
        sampling_fallback_enabled=False,
        external_baseline_captured=True,
        entry_authorized=False,
        entry_authority=disabled.authority.value,
        production_authorization_state="PENDING",
        trading_state="ENTRY_PAUSED",
    )

    assert canary.authority is EntryAuthority.TESTNET_CANARY
    assert canary.promotion_eligible is False
    assert runtime_state.active_startup_contract_errors(canary_state) == ()

    assert disabled.authority is EntryAuthority.NONE
    errors = runtime_state.active_startup_contract_errors(paused_state)
    assert "ENTRY_NOT_AUTHORIZED" in errors
    assert "ENTRY_AUTHORITY_INVALID" in errors
    assert "TRADING_STATE_NOT_TRADING" in errors


def test_active_startup_contract_rejects_non_boolean_or_incomplete_runtime_state(tmp_path, monkeypatch) -> None:
    state_path = tmp_path / "scheduler-state.json"
    monkeypatch.setenv("LOCAL_SCHEDULER_STATE_PATH", str(state_path))
    now = datetime.now(UTC)
    write_external_scheduler_state(
        {
            "running": "false",
            "heartbeat_at": now.isoformat(),
            "engine_activation": "ACTIVE",
            "execution_mode": "BINANCE_TESTNET",
            "execution_strategy_id": "testnet_sampling_v2",
            "execution_coverage_count": 1,
            "execution_symbols": ["BTC/USDT"],
            "registered_jobs": ["automated_trading_v2_cycle", "paper_runtime_cycle"],
            "legacy_writer_enabled": False,
            "entry_enabled": True,
            "sampling_fallback_enabled": True,
            "external_baseline_captured": True,
            "entry_authorized": True,
        }
    )

    state = load_external_scheduler_state(now=now)
    errors = runtime_state.active_startup_contract_errors(state, requested_engine="v2_active")

    assert state.running is False
    assert "SCHEDULER_NOT_RUNNING" in errors
    assert "EXECUTION_SCOPE_MISMATCH" in errors
    assert "EXECUTION_SCOPE_INCOMPLETE" in errors
    assert "LEGACY_JOB_REGISTERED" in errors


def test_active_startup_contract_rejects_actual_shadow_state() -> None:
    assert hasattr(runtime_state, "active_startup_contract_errors")

    state = ExternalSchedulerState(
        running=True,
        heartbeat_at=datetime.now(UTC),
        engine_activation="SHADOW",
        execution_mode="BINANCE_TESTNET",
        execution_strategy_id="testnet_sampling_v2",
        registered_jobs=("automated_trading_v2_cycle", "paper_runtime_cycle"),
        legacy_writer_enabled=True,
        entry_enabled=True,
        sampling_fallback_enabled=True,
        external_baseline_captured=True,
        entry_authorized=False,
    )

    errors = runtime_state.active_startup_contract_errors(state, requested_engine="v2_active")

    assert "ENGINE_ACTIVATION_MISMATCH" in errors
    assert "LEGACY_WRITER_ENABLED" in errors
    assert "EXECUTION_STRATEGY_UNAUTHORIZED" in errors


def test_scheduler_publishes_only_active_execution_scope(monkeypatch) -> None:
    from services.execution import scheduler as scheduler_module

    captured = {}
    monkeypatch.setattr(scheduler_module, "write_external_scheduler_state", captured.update)
    scheduler = RuntimeScheduler()
    scheduler.status.last_results["market_data_heartbeat"] = {
        "checked_symbols": list(AUTO_SIMULATION_EXECUTION_SYMBOLS),
        "stale_symbols": [],
    }
    scheduler.status.engine_activation = "ACTIVE"
    scheduler.status.execution_mode = "BINANCE_TESTNET"
    scheduler.status.execution_strategy_id = "testnet_sampling_v2"
    scheduler.status.registered_jobs = ("automated_trading_v2_cycle",)
    scheduler.status.legacy_writer_enabled = False
    scheduler.status.entry_enabled = True
    scheduler.status.sampling_fallback_enabled = True
    scheduler.status.external_baseline_captured = True
    scheduler.status.external_baseline_lifecycle = "MANUAL_BASELINE_ACK_REQUIRED"
    scheduler.status.external_baseline_drift_keys = ("BTC/USDT:short",)
    scheduler.status.entry_authorized = True

    scheduler._publish_external_state()

    assert captured["top20_coverage_count"] == len(AUTO_SIMULATION_EXECUTION_SYMBOLS)
    assert captured["execution_symbols"] == list(AUTO_SIMULATION_EXECUTION_SYMBOLS)
    assert captured["execution_coverage_count"] == len(AUTO_SIMULATION_EXECUTION_SYMBOLS)
    assert captured["engine_activation"] == "ACTIVE"
    assert captured["execution_mode"] == "BINANCE_TESTNET"
    assert captured["entry_authorized"] is True
    assert captured["external_baseline_lifecycle"] == "MANUAL_BASELINE_ACK_REQUIRED"
    assert captured["external_baseline_drift_keys"] == ["BTC/USDT:short"]


def test_scheduler_publishes_fixed_execution_scope_before_market_heartbeat(monkeypatch) -> None:
    """The ACTIVE launch contract must not wait for the first market heartbeat."""
    from services.execution import scheduler as scheduler_module

    captured = {}
    monkeypatch.setattr(scheduler_module, "write_external_scheduler_state", captured.update)
    scheduler = RuntimeScheduler()
    scheduler.status.engine_activation = "ACTIVE"
    scheduler.status.execution_mode = "BINANCE_TESTNET"
    scheduler.status.execution_strategy_id = "testnet_sampling_v2"
    scheduler.status.registered_jobs = ("automated_trading_v2_cycle",)
    scheduler.status.legacy_writer_enabled = False
    scheduler.status.entry_enabled = True
    scheduler.status.sampling_fallback_enabled = True
    scheduler.status.external_baseline_captured = True
    scheduler.status.entry_authorized = True

    scheduler._publish_external_state()

    assert captured["execution_symbols"] == list(AUTO_SIMULATION_EXECUTION_SYMBOLS)
    assert captured["execution_coverage_count"] == len(AUTO_SIMULATION_EXECUTION_SYMBOLS)
    assert captured["data_fresh"] is False


def test_empty_external_baseline_is_not_captured(monkeypatch) -> None:
    from services.execution import scheduler as scheduler_module

    monkeypatch.setenv("V2_ALLOW_UNMANAGED_EXTERNAL_POSITIONS", "true")
    monkeypatch.setenv("V2_EXTERNAL_BASELINE_JSON", "{}")

    assert scheduler_module._external_baseline_capture() == (False, None, None)


def test_external_baseline_rejects_transient_environment_without_persistent_source(tmp_path, monkeypatch) -> None:
    from services.execution import scheduler as scheduler_module

    baseline_path = tmp_path / "testnet-external-baseline.json"
    baseline_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "execution_mode": "BINANCE_TESTNET",
                "captured_symbols": list(AUTO_SIMULATION_EXECUTION_SYMBOLS),
                "source": "binance_testnet_authoritative_snapshot",
                "positions": {"ETH/USDT:short": "10.976"},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("V2_ALLOW_UNMANAGED_EXTERNAL_POSITIONS", "true")
    monkeypatch.setenv("V2_EXTERNAL_BASELINE_JSON", '{"ETH/USDT:short":"10.976"}')
    monkeypatch.setenv("V2_EXTERNAL_BASELINE_PATH", str(baseline_path))
    monkeypatch.delenv("V2_EXTERNAL_BASELINE_SOURCE", raising=False)

    assert scheduler_module._external_baseline_capture() == (False, None, None)

    monkeypatch.setenv("V2_EXTERNAL_BASELINE_SOURCE", f"persistent_file:{baseline_path.resolve()}")
    assert scheduler_module._external_baseline_capture() == (
        True,
        {"ETH/USDT:short": "10.976"},
        f"persistent_file:{baseline_path.resolve()}",
    )


def test_active_scheduler_fails_before_job_registration_when_contract_is_incomplete(monkeypatch) -> None:
    from services.automated_trading.domain.enums import V2ExecutionMode
    from services.automated_trading.infrastructure import runtime_lock
    from services.automated_trading.infrastructure.runtime_lock import EngineActivation, EngineActivationConfig
    from services.execution import scheduler as scheduler_module

    captured = {}
    config = EngineActivationConfig(
        v2_activation=EngineActivation.ACTIVE,
        execution_mode=V2ExecutionMode.BINANCE_TESTNET,
        allow_legacy_writer=False,
        warnings=[],
    )
    monkeypatch.setattr(runtime_lock, "resolve_engine_activation", lambda _settings: config)
    monkeypatch.setattr(scheduler_module, "_active_entry_authorization", lambda: (False, False, ()))
    monkeypatch.setattr(scheduler_module, "_external_baseline_capture", lambda: (False, None, None))
    monkeypatch.setattr(scheduler_module, "write_external_scheduler_state", captured.update)
    scheduler = RuntimeScheduler(coordinator=object())  # type: ignore[arg-type]

    with pytest.raises(RuntimeError, match="ACTIVE_STARTUP_CONTRACT_FAILED"):
        scheduler.start()

    assert scheduler._tasks == []
    assert captured["running"] is False
    assert captured["entry_authorized"] is False
    assert "EXTERNAL_BASELINE_NOT_CAPTURED" in captured["startup_contract_errors"]


@pytest.mark.asyncio
async def test_runtime_scheduler_runs_periodic_jobs_and_stops() -> None:
    calls = {"paper": 0, "heartbeat": 0, "risk": 0, "edge_stats": 0, "notification": 0, "daily": 0}

    scheduler = RuntimeScheduler(
        paper_cycle_seconds=0.03,
        heartbeat_seconds=0.03,
        notification_seconds=0.03,
        risk_sweep_seconds=0.03,
        edge_stats_refresh_seconds=0.03,
        daily_review_check_seconds=0.03,
        paper_cycle_runner=lambda: calls.__setitem__("paper", calls["paper"] + 1),
        heartbeat_runner=lambda: calls.__setitem__("heartbeat", calls["heartbeat"] + 1),
        risk_sweep_runner=lambda: calls.__setitem__("risk", calls["risk"] + 1),
        edge_stats_refresh_runner=lambda: calls.__setitem__("edge_stats", calls["edge_stats"] + 1),
        notification_runner=lambda: calls.__setitem__("notification", calls["notification"] + 1),
        daily_review_runner=lambda _: calls.__setitem__("daily", calls["daily"] + 1),
    )

    scheduler.start()
    deadline = asyncio.get_running_loop().time() + 1.0
    while calls["edge_stats"] == 0 and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.01)
    await scheduler.stop()
    stopped_at = dict(calls)
    await asyncio.sleep(0.07)

    assert stopped_at["paper"] >= 1
    assert stopped_at["heartbeat"] >= 1
    assert stopped_at["risk"] >= 1
    assert stopped_at["edge_stats"] >= 1
    assert stopped_at["notification"] >= 1
    assert stopped_at["daily"] == 1
    assert calls == stopped_at
    assert scheduler.status.running is False
    assert scheduler.status.last_auto_cycle_at is not None


@pytest.mark.asyncio
async def test_paper_cycle_does_not_run_immediately_on_process_start() -> None:
    calls = {"paper": 0, "heartbeat": 0}
    scheduler = RuntimeScheduler(
        paper_cycle_seconds=0.20,
        heartbeat_seconds=0.01,
        paper_cycle_runner=lambda: calls.__setitem__("paper", calls["paper"] + 1),
        heartbeat_runner=lambda: calls.__setitem__("heartbeat", calls["heartbeat"] + 1),
    )

    scheduler.start()
    await asyncio.sleep(0.06)
    await scheduler.stop()

    assert calls["paper"] == 0
    assert calls["heartbeat"] >= 1


@pytest.mark.asyncio
async def test_standby_scheduler_does_not_report_an_executed_auto_cycle() -> None:
    class StandbyCoordinator:
        def acquire_or_renew_lease(self, **kwargs) -> bool:  # noqa: ANN003
            return False

    scheduler = RuntimeScheduler(coordinator=StandbyCoordinator())  # type: ignore[arg-type]
    scheduler._stop_event = asyncio.Event()

    task = asyncio.create_task(
        scheduler._run_periodic(
            name="paper_runtime_cycle",
            interval_seconds=0.01,
            runner=lambda: {"status": "unexpected"},
            records_auto_cycle=True,
            coordinated=True,
        )
    )
    await asyncio.sleep(0.03)
    scheduler._stop_event.set()
    await task

    assert scheduler.status.last_auto_cycle_at is None
    assert scheduler.status.last_results["paper_runtime_cycle"]["status"] == "standby_not_leader"


@pytest.mark.asyncio
async def test_optional_source_failure_does_not_mark_scheduler_unhealthy() -> None:
    scheduler = RuntimeScheduler()

    await scheduler._run_once(
        name="poll_macro_calendar",
        runner=lambda: _raise_runtime_error("upstream returned 403"),
        affects_scheduler_health=False,
    )

    assert scheduler.status.scheduler_error is None
    assert scheduler.status.failure_counts["poll_macro_calendar"] == 1
    assert scheduler.status.last_results["poll_macro_calendar"] == {
        "status": "error",
        "error": "upstream returned 403",
    }


@pytest.mark.asyncio
async def test_v2_cycle_refreshes_runtime_truth_with_cycle_resolved_authority() -> None:
    scheduler = RuntimeScheduler()
    scheduler.status.entry_authority = "TESTNET_CANARY"
    scheduler.status.entry_authorized = True
    scheduler.status.trading_state = "TRADING"

    await scheduler._run_once(
        name="automated_trading_v2_cycle",
        runner=lambda: {
            "status": "completed",
            "results": [
                {
                    "status": "completed",
                    "entry_authority": "PRODUCTION",
                    "entry_authorized": True,
                    "entry_authority_reason": "production_approved",
                    "production_authorization_state": "APPROVED",
                    "active_entry_strategy": "approved_primary_v1",
                    "promotion_eligible": True,
                    "trading_state": "TRADING",
                },
                {
                    "status": "completed",
                    "entry_authority": "PRODUCTION",
                    "entry_authorized": True,
                    "entry_authority_reason": "production_approved",
                    "production_authorization_state": "APPROVED",
                    "active_entry_strategy": "approved_primary_v1",
                    "promotion_eligible": True,
                    "trading_state": "TRADING",
                },
            ],
        },
    )

    assert scheduler.status.entry_authority == "PRODUCTION"
    assert scheduler.status.entry_authority_reason == "production_approved"
    assert scheduler.status.production_authorization_state == "APPROVED"
    assert scheduler.status.active_entry_strategy == "approved_primary_v1"
    assert scheduler.status.promotion_eligible is True
    published = load_external_scheduler_state()
    assert published.entry_authority == "PRODUCTION"
    assert published.production_authorization_state == "APPROVED"


@pytest.mark.asyncio
async def test_scheduler_respects_task_retry_after_before_next_cycle() -> None:
    calls = 0

    def rate_limited_runner() -> dict:
        nonlocal calls
        calls += 1
        return {"status": "rate_limited", "retry_after_seconds": 60}

    scheduler = RuntimeScheduler(heartbeat_seconds=0.01, heartbeat_runner=rate_limited_runner)
    scheduler.start()
    await asyncio.sleep(0.05)
    await scheduler.stop()

    assert calls == 1


@pytest.mark.asyncio
async def test_core_task_failure_remains_visible_until_that_task_recovers() -> None:
    scheduler = RuntimeScheduler()

    await scheduler._run_once(
        name="paper_runtime_cycle",
        runner=lambda: _raise_runtime_error("database unavailable"),
    )
    await scheduler._run_once(name="market_data_heartbeat", runner=lambda: {"status": "ok"})

    assert scheduler.status.scheduler_error == "paper_runtime_cycle: database unavailable"

    await scheduler._run_once(name="paper_runtime_cycle", runner=lambda: {"status": "ok"})

    assert scheduler.status.scheduler_error is None
    assert "paper_runtime_cycle" in scheduler.status.last_success_at
    assert "paper_runtime_cycle" in scheduler.status.last_failure_at
