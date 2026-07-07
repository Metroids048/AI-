from __future__ import annotations

import asyncio

import pytest

from services.execution.scheduler import RuntimeScheduler


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
