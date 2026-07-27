from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from services.data.universe import AUTO_SIMULATION_EXECUTION_SYMBOLS
from services.execution.runtime_state import (
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


def test_scheduler_publishes_only_active_execution_scope(monkeypatch) -> None:
    from services.execution import scheduler as scheduler_module

    captured = {}
    monkeypatch.setattr(scheduler_module, "write_external_scheduler_state", captured.update)
    scheduler = RuntimeScheduler()
    scheduler.status.last_results["market_data_heartbeat"] = {
        "checked_symbols": ["BTC/USDT", "ETH/USDT", "SOL/USDT"],
        "stale_symbols": [],
    }

    scheduler._publish_external_state()

    assert captured["top20_coverage_count"] == 3
    assert captured["execution_symbols"] == list(AUTO_SIMULATION_EXECUTION_SYMBOLS)
    assert captured["execution_coverage_count"] == len(AUTO_SIMULATION_EXECUTION_SYMBOLS)


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
