"""In-process runtime scheduler for local Paper operation.

Celery remains the production/multi-process scheduler. This module gives the
one-click local console the same recurring calls without requiring Redis.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from shared.config import settings

Runner = Callable[[], Any]


def _preload_celery_task_api() -> None:
    """Initialize Celery's lazy task exports before worker threads import task modules."""
    from celery import shared_task

    if not callable(shared_task):  # pragma: no cover - dependency contract guard
        raise RuntimeError("Celery shared_task API is unavailable")


@dataclass
class RuntimeSchedulerStatus:
    mode: str = "inprocess"
    running: bool = False
    started_at: datetime | None = None
    last_auto_cycle_at: datetime | None = None
    next_cycle_eta_seconds: int | None = None
    scheduler_error: str | None = None
    run_counts: dict[str, int] = field(default_factory=dict)
    failure_counts: dict[str, int] = field(default_factory=dict)
    last_results: dict[str, Any] = field(default_factory=dict)

    def model_dump(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "running": self.running,
            "started_at": self.started_at,
            "last_auto_cycle_at": self.last_auto_cycle_at,
            "next_cycle_eta_seconds": self.next_cycle_eta_seconds,
            "scheduler_error": self.scheduler_error,
            "run_counts": dict(self.run_counts),
            "failure_counts": dict(self.failure_counts),
            "last_results": dict(self.last_results),
        }


class RuntimeScheduler:
    """Run local recurring jobs inside the FastAPI event loop."""

    def __init__(
        self,
        *,
        paper_cycle_seconds: float | None = None,
        heartbeat_seconds: float | None = None,
        notification_seconds: float | None = None,
        news_poll_seconds: float = 180.0,
        macro_poll_seconds: float = 900.0,
        social_poll_seconds: float = 300.0,
        risk_sweep_seconds: float = 60.0,
        daily_review_check_seconds: float = 60.0,
        paper_cycle_runner: Runner | None = None,
        heartbeat_runner: Runner | None = None,
        news_poll_runner: Runner | None = None,
        macro_poll_runner: Runner | None = None,
        social_poll_runner: Runner | None = None,
        risk_sweep_runner: Runner | None = None,
        notification_runner: Runner | None = None,
        daily_review_runner: Callable[[str | None], Any] | None = None,
    ) -> None:
        self.paper_cycle_seconds = float(paper_cycle_seconds or settings.paper_runtime_cycle_seconds)
        self.heartbeat_seconds = float(heartbeat_seconds or settings.market_data_heartbeat_seconds)
        self.notification_seconds = float(notification_seconds or settings.notification_dispatch_seconds)
        self.news_poll_seconds = float(news_poll_seconds)
        self.macro_poll_seconds = float(macro_poll_seconds)
        self.social_poll_seconds = float(social_poll_seconds)
        self.risk_sweep_seconds = float(risk_sweep_seconds)
        self.daily_review_check_seconds = float(daily_review_check_seconds)
        self.paper_cycle_runner = paper_cycle_runner or _default_paper_cycle_runner
        self.heartbeat_runner = heartbeat_runner or _default_heartbeat_runner
        self.news_poll_runner = news_poll_runner or _default_news_poll_runner
        self.macro_poll_runner = macro_poll_runner or _default_macro_poll_runner
        self.social_poll_runner = social_poll_runner or _default_social_poll_runner
        self.risk_sweep_runner = risk_sweep_runner or _default_risk_sweep_runner
        self.notification_runner = notification_runner or _default_notification_runner
        self.daily_review_runner = daily_review_runner or _default_daily_review_runner
        self.status = RuntimeSchedulerStatus(mode="inprocess")
        self._tasks: list[asyncio.Task] = []
        self._stop_event: asyncio.Event | None = None
        self._last_daily_review_date: date | None = None
        self._next_cycle_at: datetime | None = None
        self._scheduler_errors: dict[str, str] = {}

    def start(self) -> None:
        if self.status.running:
            return
        _preload_celery_task_api()
        self._stop_event = asyncio.Event()
        self.status.running = True
        self.status.started_at = datetime.now(UTC)
        self.status.scheduler_error = None
        self._scheduler_errors.clear()
        self._tasks = [
            asyncio.create_task(
                self._run_periodic(
                    name="paper_runtime_cycle",
                    interval_seconds=self.paper_cycle_seconds,
                    runner=self.paper_cycle_runner,
                    records_auto_cycle=True,
                )
            ),
            asyncio.create_task(
                self._run_periodic(
                    name="market_data_heartbeat",
                    interval_seconds=self.heartbeat_seconds,
                    runner=self.heartbeat_runner,
                )
            ),
            asyncio.create_task(
                self._run_periodic(
                    name="poll_news_feeds",
                    interval_seconds=self.news_poll_seconds,
                    runner=self.news_poll_runner,
                    affects_scheduler_health=False,
                )
            ),
            asyncio.create_task(
                self._run_periodic(
                    name="poll_macro_calendar",
                    interval_seconds=self.macro_poll_seconds,
                    runner=self.macro_poll_runner,
                    affects_scheduler_health=False,
                )
            ),
            asyncio.create_task(
                self._run_periodic(
                    name="poll_social_watchlist",
                    interval_seconds=self.social_poll_seconds,
                    runner=self.social_poll_runner,
                    affects_scheduler_health=False,
                )
            ),
            asyncio.create_task(
                self._run_periodic(
                    name="risk_profile_sweep",
                    interval_seconds=self.risk_sweep_seconds,
                    runner=self.risk_sweep_runner,
                )
            ),
            asyncio.create_task(
                self._run_periodic(
                    name="notification_dispatch",
                    interval_seconds=self.notification_seconds,
                    runner=self.notification_runner,
                    affects_scheduler_health=False,
                )
            ),
            asyncio.create_task(self._run_daily_review_loop()),
        ]
        if settings.binance_live_ws_enabled:
            self._tasks.extend(self._live_collector_tasks())

    async def stop(self) -> None:
        if not self.status.running:
            return
        if self._stop_event is not None:
            self._stop_event.set()
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []
        self.status.running = False
        self.status.next_cycle_eta_seconds = None

    async def _run_periodic(
        self,
        *,
        name: str,
        interval_seconds: float,
        runner: Runner,
        records_auto_cycle: bool = False,
        affects_scheduler_health: bool = True,
    ) -> None:
        assert self._stop_event is not None
        while not self._stop_event.is_set():
            started = datetime.now(UTC)
            if records_auto_cycle:
                self._next_cycle_at = started
                self.status.next_cycle_eta_seconds = 0
            await self._run_once(
                name=name,
                runner=runner,
                affects_scheduler_health=affects_scheduler_health,
            )
            if records_auto_cycle:
                self.status.last_auto_cycle_at = datetime.now(UTC)
                self._next_cycle_at = self.status.last_auto_cycle_at
            with suppress(TimeoutError):
                await asyncio.wait_for(self._stop_event.wait(), timeout=max(interval_seconds, 0.01))
            if records_auto_cycle and self._next_cycle_at is not None:
                elapsed = (datetime.now(UTC) - self._next_cycle_at).total_seconds()
                self.status.next_cycle_eta_seconds = max(0, int(interval_seconds - elapsed))

    async def _run_once(
        self,
        *,
        name: str,
        runner: Runner,
        affects_scheduler_health: bool = True,
    ) -> None:
        try:
            result = await asyncio.to_thread(runner)
            self.status.run_counts[name] = self.status.run_counts.get(name, 0) + 1
            self.status.last_results[name] = result
            if affects_scheduler_health:
                self._scheduler_errors.pop(name, None)
        except Exception as exc:  # pragma: no cover - defensive runtime guard
            self.status.failure_counts[name] = self.status.failure_counts.get(name, 0) + 1
            self.status.last_results[name] = {"status": "error", "error": str(exc)}
            if affects_scheduler_health:
                self._scheduler_errors[name] = str(exc)
        self.status.scheduler_error = "; ".join(
            f"{task_name}: {error}"
            for task_name, error in sorted(self._scheduler_errors.items())
        ) or None

    async def _run_daily_review_loop(self) -> None:
        assert self._stop_event is not None
        while not self._stop_event.is_set():
            now = datetime.now(UTC)
            due = now.hour > settings.daily_review_hour_utc or (
                now.hour == settings.daily_review_hour_utc and now.minute >= settings.daily_review_minute_utc
            )
            if due and self._last_daily_review_date != now.date():
                await self._run_once(name="daily_review", runner=lambda: self.daily_review_runner(None))
                self._last_daily_review_date = now.date()
            with suppress(TimeoutError):
                await asyncio.wait_for(self._stop_event.wait(), timeout=max(self.daily_review_check_seconds, 0.01))

    def _live_collector_tasks(self) -> list[asyncio.Task]:
        from services.data.service import resolve_binance_live_ws_symbols

        symbols = resolve_binance_live_ws_symbols()
        return [
            asyncio.create_task(self._run_live_collector(symbol=symbol, timeframe=settings.binance_live_ws_timeframe))
            for symbol in symbols
        ]

    async def _run_live_collector(self, *, symbol: str, timeframe: str) -> None:
        from services.data import DataRepository, live_feed_bus
        from services.data.binance import (
            BinanceLiveMarketCollector,
            run_live_collector_forever,
            spot_to_usdm_perp_symbol,
        )
        from services.database import get_session_factory

        def collector_factory() -> BinanceLiveMarketCollector:
            session = get_session_factory()()

            async def publish(bar) -> None:
                await live_feed_bus.publish_candle(bar)

            return BinanceLiveMarketCollector(
                data_repo=DataRepository(session),
                on_candle=publish,
                on_close=session.close,
            )

        async def report_reconnect_error(exc: Exception) -> None:
            await live_feed_bus.set_error(symbol=symbol, timeframe=timeframe, error=str(exc))

        await run_live_collector_forever(
            collector_factory=collector_factory,
            symbol=symbol,
            perp_symbol=spot_to_usdm_perp_symbol(symbol),
            timeframe=timeframe,
            reconnect_error_handler=report_reconnect_error,
        )


_runtime_scheduler: RuntimeScheduler | None = None


def get_runtime_scheduler() -> RuntimeScheduler | None:
    return _runtime_scheduler


def set_runtime_scheduler(scheduler: RuntimeScheduler | None) -> None:
    global _runtime_scheduler
    _runtime_scheduler = scheduler


def runtime_scheduler_status() -> RuntimeSchedulerStatus:
    if _runtime_scheduler is None:
        return RuntimeSchedulerStatus(mode=settings.runtime_scheduler_mode, running=False)
    return _runtime_scheduler.status


def _default_paper_cycle_runner() -> dict:
    from services.execution.tasks import run_all_paper_runtime_cycles

    return run_all_paper_runtime_cycles.run(
        {
            "timeframe": "1m",
            "max_symbols": 20,
            "enable_decision_veto": settings.paper_runtime_enable_decision_veto,
        }
    )


def _default_heartbeat_runner() -> dict:
    from services.data.service import DEFAULT_BINANCE_TOP20
    from services.data.tasks import market_data_heartbeat

    return market_data_heartbeat.run(list(DEFAULT_BINANCE_TOP20), "1m")


def _default_news_poll_runner() -> dict:
    from services.data.tasks import poll_news_feeds

    return poll_news_feeds.run()


def _default_macro_poll_runner() -> dict:
    from services.data.tasks import poll_macro_calendar

    return poll_macro_calendar.run()


def _default_social_poll_runner() -> dict:
    from services.data.tasks import poll_social_watchlist

    return poll_social_watchlist.run()


def _default_risk_sweep_runner() -> dict:
    from services.execution.tasks import risk_profile_sweep

    return risk_profile_sweep.run()


def _default_notification_runner() -> dict:
    from services.notifications_tasks import dispatch_notification_outbox

    return dispatch_notification_outbox.run()


def _default_daily_review_runner(report_date: str | None = None) -> dict:
    from services.review.tasks import generate_daily_review

    return generate_daily_review.run(report_date)
