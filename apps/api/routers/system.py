"""System dependency and runtime health APIs."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from apps.api.config import settings
from services.database import get_db_session

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/health/dependencies", response_model=dict)
def get_dependency_health(db: Session = Depends(get_db_session)) -> dict:
    checks: dict[str, dict[str, str]] = {}
    try:
        db.execute(text("select 1"))
        checks["database"] = {"status": "ok", "detail": "select 1 succeeded"}
    except Exception as exc:  # pragma: no cover - exercised by deployed environment
        checks["database"] = {"status": "error", "detail": str(exc)}
    checks["redis"] = {
        "status": "configured" if settings.redis_url else "missing_config",
        "detail": settings.redis_url,
    }
    checks["celery"] = {
        "status": "configured" if settings.celery_broker_url else "missing_config",
        "detail": settings.celery_broker_url,
    }
    return {
        "status": "ok" if all(item["status"] in {"ok", "configured"} for item in checks.values()) else "degraded",
        "dependencies": checks,
    }
