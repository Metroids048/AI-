"""Notification outbox APIs."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from apps.api.http import collection_response, not_found
from services.database import get_db_session
from services.notifications import NotificationDispatcherService
from services.strategy_library import NotificationRepository
from shared.models import CollectionResponse, NotificationDeliveryUpdate, NotificationOutboxItem

router = APIRouter(prefix="/notifications", tags=["notifications"])


def _notification_repo(db: Session) -> NotificationRepository:
    return NotificationRepository(db)


def _dispatcher(db: Session) -> NotificationDispatcherService:
    return NotificationDispatcherService(repository=_notification_repo(db))


@router.get("/outbox", response_model=CollectionResponse[NotificationOutboxItem])
def list_notification_outbox(
    delivery_status: str | None = Query(default=None),
    severity: str | None = Query(default=None),
    event_type: str | None = Query(default=None),
    channel_group: str | None = Query(default=None),
    db: Session = Depends(get_db_session),
) -> CollectionResponse[NotificationOutboxItem]:
    items = _notification_repo(db).list_notifications(
        delivery_status=delivery_status,
        severity=severity,
        event_type=event_type,
        channel_group=channel_group,
    )
    return collection_response(items)


@router.post("/outbox", response_model=NotificationOutboxItem, status_code=status.HTTP_201_CREATED)
def create_notification_outbox_item(
    body: NotificationOutboxItem,
    db: Session = Depends(get_db_session),
) -> NotificationOutboxItem:
    return _notification_repo(db).create_notification(body)


@router.post("/outbox/dispatch", status_code=status.HTTP_202_ACCEPTED)
def dispatch_notification_outbox(
    limit: int = Query(default=20, ge=1, le=200),
    notification_id: str | None = Query(default=None),
    db: Session = Depends(get_db_session),
) -> dict[str, int]:
    dispatcher = _dispatcher(db)
    if notification_id is not None:
        return {"dispatched": int(dispatcher.dispatch_notification(notification_id))}
    result = dispatcher.dispatch_due_notifications(limit=limit)
    return {"dispatched": result.dispatched}


@router.patch("/outbox/{notification_id:path}/delivery", response_model=NotificationOutboxItem)
def update_notification_delivery(
    notification_id: str,
    body: NotificationDeliveryUpdate,
    db: Session = Depends(get_db_session),
) -> NotificationOutboxItem:
    item = _notification_repo(db).update_delivery(
        notification_id,
        delivery_status=body.delivery_status,
        last_error=body.last_error,
        next_attempt_at=body.next_attempt_at,
    )
    if item is None:
        raise not_found("notification", notification_id)
    return item
