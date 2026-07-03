"""Notification outbox read APIs."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from apps.api.http import collection_response
from services.data import DataRepository
from services.database import get_db_session
from services.notifications import risk_event_notifications
from shared.models import CollectionResponse, NotificationOutboxItem

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("/outbox", response_model=CollectionResponse[NotificationOutboxItem])
def list_notification_outbox(db: Session = Depends(get_db_session)) -> CollectionResponse[NotificationOutboxItem]:
    events = DataRepository(db).list_risk_events(active_only=True)
    return collection_response(risk_event_notifications(events))
