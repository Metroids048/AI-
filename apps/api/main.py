"""API entrypoint. Mounts the versioned interface clusters."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from apps.api.auth import admin_token_middleware
from apps.api.config import settings
from apps.api.routers import (
    agents,
    backtests,
    console,
    ensemble,
    ingestion,
    market,
    metrics,
    notifications,
    research_sources,
    review,
    risk,
    runs,
    strategies,
    system,
)
from services.execution.scheduler import RuntimeScheduler, set_runtime_scheduler
from services.execution.bootstrap import bootstrap_local_paper_runtime
from shared.models import ApiError


def _should_start_inprocess_scheduler() -> bool:
    if settings.runtime_scheduler_mode != "inprocess" or not settings.runtime_scheduler_autostart:
        return False
    return not (os.getenv("PYTEST_CURRENT_TEST") or ".pytest_ai_quant" in os.getenv("POSTGRES_URL", ""))


@asynccontextmanager
async def lifespan(_: FastAPI):
    scheduler = None
    bootstrap_local_paper_runtime()
    if _should_start_inprocess_scheduler():
        scheduler = RuntimeScheduler()
        set_runtime_scheduler(scheduler)
        scheduler.start()
    try:
        yield
    finally:
        if scheduler is not None:
            await scheduler.stop()
        set_runtime_scheduler(None)


app = FastAPI(title="AI Quant Research Platform", version="0.1.0", lifespan=lifespan)

# Explicit CORS whitelist (previously absent → default-deny with no declaration).
_allowed_origins = [origin.strip() for origin in settings.cors_allowed_origins.split(",") if origin.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Requested-With"],
)
app.middleware("http")(admin_token_middleware)


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(_, exc: StarletteHTTPException) -> JSONResponse:
    if isinstance(exc.detail, dict) and {"error_code", "message"}.issubset(exc.detail):
        payload = exc.detail
    else:
        payload = ApiError(
            error_code="http_error",
            message=str(exc.detail),
            detail={"status_code": exc.status_code},
        ).model_dump(mode="json")
    return JSONResponse(status_code=exc.status_code, content=payload)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_, exc: RequestValidationError) -> JSONResponse:
    payload = ApiError(
        error_code="validation_error",
        message="request validation failed",
        detail={"errors": exc.errors()},
    )
    return JSONResponse(status_code=422, content=payload.model_dump(mode="json"))


for router in (
    strategies.router,
    backtests.router,
    runs.router,
    risk.router,
    review.router,
    ingestion.router,
    agents.router,
    ensemble.router,
    market.router,
    console.router,
    system.router,
    notifications.router,
    research_sources.router,
):
    app.include_router(router, prefix="/api/v1")

app.include_router(metrics.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "phase": "phase-0", "env": settings.app_env}


@app.get("/api/v1/health")
def health_v1() -> dict[str, str]:
    return health()
