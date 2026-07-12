"""Celery application factory.

Phase-0 seam: no tasks registered yet (autodiscover is empty), but the worker /
beat / flower services boot cleanly. Backtest tasks will use a dedicated
`backtest_queue` to avoid blocking daily data sync (PDF risk table 6.2).
"""

from __future__ import annotations

from celery import Celery
from celery.schedules import crontab
from kombu import Queue

import services.notifications_tasks  # noqa: F401
from apps.api.config import settings

celery_app = Celery(
    "ai_quant",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)
celery_app.conf.task_default_queue = "default"
celery_app.conf.task_queues = (
    Queue("default"),
    Queue("ingestion_queue"),
    Queue("backtest_queue"),
    Queue("paper_queue"),
    Queue("ops_queue"),
)
celery_app.conf.task_routes = {
    "services.data.tasks.enqueue_binance_ingestion": {"queue": "ingestion_queue"},
    "services.data.tasks.poll_news_feeds": {"queue": "ingestion_queue"},
    "services.data.tasks.poll_macro_calendar": {"queue": "ingestion_queue"},
    "services.data.tasks.poll_social_watchlist": {"queue": "ingestion_queue"},
    "services.data.tasks.market_data_heartbeat": {"queue": "ops_queue"},
    "services.validation.tasks.enqueue_backtest_run": {"queue": "backtest_queue"},
    "services.validation.tasks.enqueue_carry_backtest": {"queue": "backtest_queue"},
    "services.execution.tasks.enqueue_paper_run": {"queue": "paper_queue"},
    "services.execution.tasks.run_all_paper_runtime_cycles": {"queue": "paper_queue"},
    "services.execution.tasks.run_paper_runtime_cycle": {"queue": "paper_queue"},
    "services.execution.tasks.risk_profile_sweep": {"queue": "ops_queue"},
    "services.notifications_tasks.dispatch_notification_outbox": {"queue": "ops_queue"},
    "services.review.tasks.generate_daily_review": {"queue": "ops_queue"},
}
celery_app.conf.timezone = "UTC"
# Worker tuning (previously unconfigured → tasks could block workers
# indefinitely with no timeout protection).
celery_app.conf.worker_concurrency = settings.celery_worker_concurrency
celery_app.conf.task_time_limit = settings.celery_task_time_limit_seconds
celery_app.conf.task_soft_time_limit = settings.celery_task_soft_time_limit_seconds
celery_app.conf.worker_prefetch_count = settings.celery_worker_prefetch_count
celery_app.conf.broker_connection_retry_on_startup = True
celery_app.conf.task_acks_late = True
celery_app.conf.task_reject_on_worker_lost = True
celery_app.conf.task_track_started = True
celery_app.conf.result_expires = 86_400
celery_app.conf.beat_schedule = {
    "paper-runtime-cycle-every-5-minutes": {
        "task": "services.execution.tasks.run_all_paper_runtime_cycles",
        "schedule": float(settings.paper_runtime_cycle_seconds),
        "kwargs": {
            "request_payload": {
                "timeframe": "1m",
                "enable_decision_veto": settings.paper_runtime_enable_decision_veto,
            }
        },
    },
    "market-data-heartbeat-every-minute": {
        "task": "services.data.tasks.market_data_heartbeat",
        "schedule": float(settings.market_data_heartbeat_seconds),
        "kwargs": {"symbols": ["BTC/USDT"], "timeframe": "1m"},
    },
    "risk-profile-sweep-every-minute": {
        "task": "services.execution.tasks.risk_profile_sweep",
        "schedule": 60.0,
    },
    "notification-dispatch-every-minute": {
        "task": "services.notifications_tasks.dispatch_notification_outbox",
        "schedule": float(settings.notification_dispatch_seconds),
    },
    "poll-news-feeds-every-3-minutes": {
        "task": "services.data.tasks.poll_news_feeds",
        "schedule": 180.0,
    },
    "poll-macro-calendar-every-15-minutes": {
        "task": "services.data.tasks.poll_macro_calendar",
        "schedule": 900.0,
    },
    "poll-social-watchlist-every-5-minutes": {
        "task": "services.data.tasks.poll_social_watchlist",
        "schedule": 300.0,
    },
    "daily-review-generation": {
        "task": "services.review.tasks.generate_daily_review",
        "schedule": crontab(hour=settings.daily_review_hour_utc, minute=settings.daily_review_minute_utc),
    },
    "volatility-asset-risk-tiers-weekly": {
        "task": "services.execution.tasks.refresh_volatility_asset_risk_tiers",
        "schedule": crontab(hour=3, minute=15, day_of_week="sun"),
        "kwargs": {"lookback_days": 30},
    },
}
celery_app.autodiscover_tasks(
    [
        "services.data",
        "services.validation",
        "services.execution",
        "services.review",
        "services.agents",
    ]
)
