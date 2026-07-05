"""Celery task entrypoints for notification delivery."""

from __future__ import annotations

from celery import shared_task

from services.database import get_session_factory
from services.notifications import NotificationDispatcherService
from services.strategy_library import NotificationRepository


@shared_task(name="services.notifications_tasks.dispatch_notification_outbox", queue="ops_queue")
def dispatch_notification_outbox(*, limit: int = 20, notification_id: str | None = None) -> dict[str, int]:
    session = get_session_factory()()
    try:
        dispatcher = NotificationDispatcherService(repository=NotificationRepository(session))
        if notification_id is not None:
            return {"dispatched": int(dispatcher.dispatch_notification(notification_id))}
        result = dispatcher.dispatch_due_notifications(limit=limit)
        return {"dispatched": result.dispatched}
    finally:
        session.close()
