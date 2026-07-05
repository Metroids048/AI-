"""Risk API backed by persisted profile and risk-event storage."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from apps.api.http import collection_response, not_found
from services.data import DataRepository
from services.database import get_db_session
from services.notifications import NotificationOutboxService
from services.strategy_library import RiskProfileRepository
from shared.models import CollectionResponse, RiskEvent, RiskEventResolutionUpdate, RiskProfile

router = APIRouter(prefix="/risk", tags=["risk"])


def _profile_repo(db: Session) -> RiskProfileRepository:
    return RiskProfileRepository(db)


def _data_repo(db: Session) -> DataRepository:
    return DataRepository(db)


@router.get("/profiles", response_model=CollectionResponse[RiskProfile])
def list_risk_profiles(db: Session = Depends(get_db_session)) -> CollectionResponse[RiskProfile]:
    return collection_response(_profile_repo(db).list_profiles())


@router.post("/profiles", response_model=RiskProfile, status_code=status.HTTP_201_CREATED)
def create_risk_profile(body: RiskProfile, db: Session = Depends(get_db_session)) -> RiskProfile:
    return _profile_repo(db).create_profile(body)


@router.get("/profiles/{risk_profile_id}", response_model=RiskProfile)
def get_risk_profile(risk_profile_id: str, db: Session = Depends(get_db_session)) -> RiskProfile:
    profile = _profile_repo(db).get_profile(risk_profile_id)
    if profile is None:
        raise not_found("risk_profile", risk_profile_id)
    return profile


@router.get("/events", response_model=CollectionResponse[RiskEvent])
def list_risk_events(
    active_only: bool = Query(default=False),
    db: Session = Depends(get_db_session),
) -> CollectionResponse[RiskEvent]:
    return collection_response(_data_repo(db).list_risk_events(active_only=active_only))


@router.post("/events", response_model=RiskEvent, status_code=status.HTTP_201_CREATED)
def create_risk_event(body: RiskEvent, db: Session = Depends(get_db_session)) -> RiskEvent:
    event = _data_repo(db).store_risk_event(body)
    NotificationOutboxService(db).enqueue_risk_event_notification(event)
    return event


@router.patch("/events/{risk_event_id}/resolution", response_model=RiskEvent)
def update_risk_event_resolution(
    risk_event_id: str,
    body: RiskEventResolutionUpdate,
    db: Session = Depends(get_db_session),
) -> RiskEvent:
    event = _data_repo(db).update_risk_event_resolution(
        risk_event_id=risk_event_id,
        resolution_status=str(body.resolution_status),
    )
    if event is None:
        raise not_found("risk_event", risk_event_id)
    return event
