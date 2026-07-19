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

from services.data.universe import AUTO_SIMULATION_EXECUTION_SYMBOLS
from shared.config import settings

from .runtime_state import write_external_scheduler_state

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
    last_success_at: dict[str, datetime] = field(default_factory=dict)
    last_failure_at: dict[str, datetime] = field(default_factory=dict)

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
            "last_success_at": dict(self.last_success_at),
            "last_failure_at": dict(self.last_failure_at),
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
        edge_stats_refresh_seconds: float = 7 * 24 * 60 * 60,
        daily_review_check_seconds: float = 60.0,
        paper_cycle_runner: Runner | None = None,
        heartbeat_runner: Runner | None = None,
        news_poll_runner: Runner | None = None,
        macro_poll_runner: Runner | None = None,
        social_poll_runner: Runner | None = None,
        risk_sweep_runner: Runner | None = None,
        edge_stats_refresh_runner: Runner | None = None,
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
        self.edge_stats_refresh_seconds = float(edge_stats_refresh_seconds)
        self.daily_review_check_seconds = float(daily_review_check_seconds)
        self.paper_cycle_runner = paper_cycle_runner or _default_paper_cycle_runner
        self.heartbeat_runner = heartbeat_runner or _default_heartbeat_runner
        self.news_poll_runner = news_poll_runner or _default_news_poll_runner
        self.macro_poll_runner = macro_poll_runner or _default_macro_poll_runner
        self.social_poll_runner = social_poll_runner or _default_social_poll_runner
        self.risk_sweep_runner = risk_sweep_runner or _default_risk_sweep_runner
        self.edge_stats_refresh_runner = edge_stats_refresh_runner or _default_edge_stats_refresh_runner
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
                    name="exchange_info_refresh",
                    interval_seconds=max(self.heartbeat_seconds, 60.0),
                    runner=_default_exchange_info_refresh_runner,
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
                    name="refresh_signal_edge_stats",
                    interval_seconds=self.edge_stats_refresh_seconds,
                    runner=self.edge_stats_refresh_runner,
                    affects_scheduler_health=False,
                    run_immediately=False,
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
        self._publish_external_state()

    async def _run_periodic(
        self,
        *,
        name: str,
        interval_seconds: float,
        runner: Runner,
        records_auto_cycle: bool = False,
        affects_scheduler_health: bool = True,
        run_immediately: bool = True,
    ) -> None:
        assert self._stop_event is not None
        if not run_immediately:
            with suppress(TimeoutError):
                await asyncio.wait_for(self._stop_event.wait(), timeout=max(interval_seconds, 0.01))
        while not self._stop_event.is_set():
            started = datetime.now(UTC)
            if records_auto_cycle:
                self._next_cycle_at = started
                self.status.next_cycle_eta_seconds = 0
            result = await self._run_once(
                name=name,
                runner=runner,
                affects_scheduler_health=affects_scheduler_health,
            )
            if records_auto_cycle:
                self.status.last_auto_cycle_at = datetime.now(UTC)
                self._next_cycle_at = self.status.last_auto_cycle_at
            retry_after_seconds = (
                float(result.get("retry_after_seconds", 0))
                if isinstance(result, dict)
                else 0.0
            )
            wait_seconds = max(interval_seconds, retry_after_seconds, 0.01)
            with suppress(TimeoutError):
                await asyncio.wait_for(self._stop_event.wait(), timeout=wait_seconds)
            if records_auto_cycle and self._next_cycle_at is not None:
                elapsed = (datetime.now(UTC) - self._next_cycle_at).total_seconds()
                self.status.next_cycle_eta_seconds = max(0, int(interval_seconds - elapsed))

    async def _run_once(
        self,
        *,
        name: str,
        runner: Runner,
        affects_scheduler_health: bool = True,
    ) -> Any:
        try:
            result = await asyncio.to_thread(runner)
            self.status.run_counts[name] = self.status.run_counts.get(name, 0) + 1
            self.status.last_results[name] = result
            self.status.last_success_at[name] = datetime.now(UTC)
            if affects_scheduler_health:
                self._scheduler_errors.pop(name, None)
        except Exception as exc:  # pragma: no cover - defensive runtime guard
            self.status.failure_counts[name] = self.status.failure_counts.get(name, 0) + 1
            self.status.last_results[name] = {"status": "error", "error": str(exc)}
            self.status.last_failure_at[name] = datetime.now(UTC)
            if affects_scheduler_health:
                self._scheduler_errors[name] = str(exc)
        self.status.scheduler_error = "; ".join(
            f"{task_name}: {error}"
            for task_name, error in sorted(self._scheduler_errors.items())
        ) or None
        self._publish_external_state()
        return self.status.last_results[name]

    def _publish_external_state(self) -> None:
        """Persist scheduler health for the separately hosted desktop API."""
        heartbeat = self.status.last_results.get("market_data_heartbeat", {})
        checked_symbols = heartbeat.get("checked_symbols", []) if isinstance(heartbeat, dict) else []
        stale_symbols = heartbeat.get("stale_symbols", []) if isinstance(heartbeat, dict) else []
        execution_symbols = [symbol for symbol in AUTO_SIMULATION_EXECUTION_SYMBOLS if symbol in checked_symbols]
        execution_stale_symbols = set(stale_symbols) & set(AUTO_SIMULATION_EXECUTION_SYMBOLS)
        exchange_info = self.status.last_results.get("exchange_info_refresh", {})
        write_external_scheduler_state(
            {
                "running": self.status.running,
                "heartbeat_at": datetime.now(UTC).isoformat(),
                "top20_coverage_count": len(checked_symbols),
                "execution_coverage_count": len(execution_symbols),
                "execution_symbols": execution_symbols,
                "exchange_info_ready": bool(exchange_info.get("ready"))
                if isinstance(exchange_info, dict)
                else False,
                "data_fresh": len(execution_symbols) == len(AUTO_SIMULATION_EXECUTION_SYMBOLS)
                and not execution_stale_symbols,
                "last_auto_cycle_at": self.status.last_auto_cycle_at.isoformat()
                if self.status.last_auto_cycle_at
                else None,
                "scheduler_error": self.status.scheduler_error,
                "task_run_counts": self.status.run_counts,
                "task_failure_counts": self.status.failure_counts,
                "task_last_results": self.status.last_results,
            }
        )

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
    from services.data.universe import AUTO_PAPER_RESEARCH_SYMBOLS
    from services.execution.tasks import run_all_paper_runtime_cycles

    return run_all_paper_runtime_cycles.run(
        {
            "timeframe": "1m",
            "max_symbols": len(AUTO_PAPER_RESEARCH_SYMBOLS),
            "enable_decision_veto": settings.paper_runtime_enable_decision_veto,
        }
    )


def _default_heartbeat_runner() -> dict:
    from services.data.tasks import market_data_heartbeat
    from services.data.universe import AUTO_PAPER_RESEARCH_SYMBOLS

    return market_data_heartbeat.run(list(AUTO_PAPER_RESEARCH_SYMBOLS), "1m")


def _default_exchange_info_refresh_runner() -> dict:
    from services.data.binance import fetch_usdm_exchange_info_symbols, resolve_usdm_public_rest_base
    from services.data.universe import fixed_top20_assets
    from services.execution.bootstrap import refresh_fixed_top20_runtime_universe

    base_url = resolve_usdm_public_rest_base()
    symbols = fetch_usdm_exchange_info_symbols()
    assets = fixed_top20_assets(symbols)
    ready = bool(assets) and all(
        asset.tradable_status == "trading" and asset.precision and asset.min_notional is not None
        for asset in assets
    )
    updated_runs = refresh_fixed_top20_runtime_universe(symbols) if ready else 0
    return {
        "ready": ready,
        "base_url": base_url,
        "symbol_count": len(symbols),
        "updated_runs": updated_runs,
        "assets": [asset.model_dump(mode="json") for asset in assets],
    }


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


def _default_edge_stats_refresh_runner() -> dict:
    """Keep the local scheduler aligned with Celery's weekly edge-stat refresh."""
    from services.execution.tasks import refresh_signal_edge_stats

    return refresh_signal_edge_stats.run()


def _default_notification_runner() -> dict:
    from services.notifications_tasks import dispatch_notification_outbox

    return dispatch_notification_outbox.run()


def _default_daily_review_runner(report_date: str | None = None) -> dict:
    from services.review.tasks import generate_daily_review

    return generate_daily_review.run(report_date)
