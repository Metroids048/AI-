from __future__ import annotations

import asyncio

import pytest

from services.execution.scheduler import RuntimeScheduler


def _raise_runtime_error(message: str) -> None:
    raise RuntimeError(message)


@pytest.mark.asyncio
async def test_runtime_scheduler_runs_periodic_jobs_and_stops() -> None:
    calls = {"paper": 0, "heartbeat": 0, "risk": 0, "notification": 0, "daily": 0}

    scheduler = RuntimeScheduler(
        paper_cycle_seconds=0.03,
        heartbeat_seconds=0.03,
        notification_seconds=0.03,
        risk_sweep_seconds=0.03,
        daily_review_check_seconds=0.03,
        paper_cycle_runner=lambda: calls.__setitem__("paper", calls["paper"] + 1),
        heartbeat_runner=lambda: calls.__setitem__("heartbeat", calls["heartbeat"] + 1),
        risk_sweep_runner=lambda: calls.__setitem__("risk", calls["risk"] + 1),
        notification_runner=lambda: calls.__setitem__("notification", calls["notification"] + 1),
        daily_review_runner=lambda _: calls.__setitem__("daily", calls["daily"] + 1),
    )

    scheduler.start()
    await asyncio.sleep(0.12)
    await scheduler.stop()
    stopped_at = dict(calls)
    await asyncio.sleep(0.07)

    assert stopped_at["paper"] >= 1
    assert stopped_at["heartbeat"] >= 1
    assert stopped_at["risk"] >= 1
    assert stopped_at["notification"] >= 1
    assert stopped_at["daily"] == 1
    assert calls == stopped_at
    assert scheduler.status.running is False
    assert scheduler.status.last_auto_cycle_at is not None


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
