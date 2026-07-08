"""Prometheus metrics for runtime scheduler and live feed bus."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse
from prometheus_client import CONTENT_TYPE_LATEST, Gauge, generate_latest

from services.data.live_feed_bus import live_feed_bus
from services.execution.scheduler import runtime_scheduler_status

router = APIRouter(tags=["metrics"])

SCHEDULER_RUNNING = Gauge("scheduler_running", "Whether the in-process runtime scheduler is running")
SCHEDULER_RUN_TOTAL = Gauge(
    "scheduler_run_total",
    "Total successful scheduler job runs",
    ["job"],
)
SCHEDULER_FAILURE_TOTAL = Gauge(
    "scheduler_failure_total",
    "Total failed scheduler job runs",
    ["job"],
)
LIVE_FEED_SUBSCRIBERS = Gauge("live_feed_subscribers", "Active live feed bus subscribers")
LIVE_FEED_DROPPED_TOTAL = Gauge("live_feed_dropped_total", "Total dropped live feed messages")
LIVE_FEED_LAST_PUBLISH_AGE_SECONDS = Gauge(
    "live_feed_last_publish_age_seconds",
    "Seconds since the most recent live feed publish",
)


def _refresh_metrics() -> None:
    status = runtime_scheduler_status()
    SCHEDULER_RUNNING.set(1 if status.running else 0)
    for job_name, count in status.run_counts.items():
        SCHEDULER_RUN_TOTAL.labels(job=job_name).set(count)
    for job_name, count in status.failure_counts.items():
        SCHEDULER_FAILURE_TOTAL.labels(job=job_name).set(count)

    feed_status = live_feed_bus.status()
    subscribers = 0
    dropped = 0
    latest_event_at: datetime | None = None
    if isinstance(feed_status, dict):
        for entry in feed_status.values():
            if not isinstance(entry, dict):
                continue
            subscribers += int(entry.get("subscribers") or 0)
            dropped += int(entry.get("dropped_count") or 0)
            raw_time = entry.get("last_event_at")
            if raw_time:
                event_at = datetime.fromisoformat(str(raw_time))
                if latest_event_at is None or event_at > latest_event_at:
                    latest_event_at = event_at

    LIVE_FEED_SUBSCRIBERS.set(subscribers)
    LIVE_FEED_DROPPED_TOTAL.set(dropped)
    if latest_event_at is not None:
        LIVE_FEED_LAST_PUBLISH_AGE_SECONDS.set(max((datetime.now(UTC) - latest_event_at).total_seconds(), 0.0))
    else:
        LIVE_FEED_LAST_PUBLISH_AGE_SECONDS.set(-1)


@router.get("/metrics")
def metrics() -> PlainTextResponse:
    _refresh_metrics()
    return PlainTextResponse(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
