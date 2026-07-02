"""Risk API skeleton (interface cluster 7.4)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, status

from shared.models import RiskEvent, RiskProfile

router = APIRouter(prefix="/risk", tags=["risk"])

_RISK_PROFILES: dict[str, RiskProfile] = {}
_RISK_EVENTS: list[RiskEvent] = []


@router.get("/profiles", response_model=list[RiskProfile])
def list_risk_profiles() -> list[RiskProfile]:
    return list(_RISK_PROFILES.values())


@router.post("/profiles", response_model=RiskProfile, status_code=status.HTTP_201_CREATED)
def create_risk_profile(body: RiskProfile) -> RiskProfile:
    profile = body.model_copy(update={"risk_profile_id": body.risk_profile_id or str(uuid.uuid4())})
    _RISK_PROFILES[profile.risk_profile_id] = profile
    return profile


@router.get("/profiles/{risk_profile_id}", response_model=RiskProfile)
def get_risk_profile(risk_profile_id: str) -> RiskProfile:
    profile = _RISK_PROFILES.get(risk_profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="risk profile not found")
    return profile


@router.get("/events", response_model=list[RiskEvent])
def list_risk_events() -> list[RiskEvent]:
    return _RISK_EVENTS


@router.post("/events", response_model=RiskEvent, status_code=status.HTTP_201_CREATED)
def create_risk_event(body: RiskEvent) -> RiskEvent:
    _RISK_EVENTS.append(body)
    return body
