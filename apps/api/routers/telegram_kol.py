from __future__ import annotations

from fastapi import APIRouter

from services.agents.telegram_kol.runtime import runtime

router = APIRouter(prefix="/telegram-kol", tags=["telegram-kol"])


@router.get("/health")
def telegram_kol_health() -> dict:
    return runtime.snapshot()


@router.get("/sources")
def telegram_kol_sources() -> dict:
    return {"sources": list(runtime.sources), "count": len(runtime.sources)}
