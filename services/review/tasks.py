"""Celery task entrypoints for Review Layer automation."""

from __future__ import annotations

from datetime import UTC, datetime

from celery import shared_task

from services.database import get_session_factory
from services.review import ReviewService
from services.strategy_library import ReviewRepository


@shared_task(name="services.review.tasks.generate_daily_review", queue="ops_queue")
def generate_daily_review(report_date: str | None = None) -> dict:
    session = get_session_factory()()
    try:
        date_value = report_date or datetime.now(UTC).date().isoformat()
        report = ReviewService(ReviewRepository(session)).build_daily_report(date_value)
        return report.model_dump(mode="json")
    finally:
        session.close()
