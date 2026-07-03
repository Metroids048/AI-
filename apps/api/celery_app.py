"""Celery application factory.

Phase-0 seam: no tasks registered yet (autodiscover is empty), but the worker /
beat / flower services boot cleanly. Backtest tasks will use a dedicated
`backtest_queue` to avoid blocking daily data sync (PDF risk table 6.2).
"""

from __future__ import annotations

from celery import Celery
from kombu import Queue

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
)
celery_app.conf.task_routes = {
    "services.data.tasks.enqueue_binance_ingestion": {"queue": "ingestion_queue"},
    "services.validation.tasks.enqueue_backtest_run": {"queue": "backtest_queue"},
    "services.validation.tasks.enqueue_carry_backtest": {"queue": "backtest_queue"},
    "services.execution.tasks.enqueue_paper_run": {"queue": "paper_queue"},
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
