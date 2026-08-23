"""API entrypoint. Mounts the versioned interface clusters."""

from __future__ import annotations

import asyncio
import contextlib
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
    automated_trading,
    backtests,
    console,
    ensemble,
    ingestion,
    market,
    market_intelligence,
    metrics,
    notifications,
    research_sources,
    review,
    risk,
    runs,
    runtime,
    strategies,
    strategy_library,
    system,
    telegram_kol,
)
from services.agents.telegram_kol.runtime import runtime as telegram_kol_runtime
from services.execution.bootstrap import (
    bootstrap_local_paper_runtime,
    bootstrap_poll_information_sources,
    bootstrap_seed_multi_timeframe_ohlcv,
)
from services.execution.scheduler import RuntimeScheduler, set_runtime_scheduler
from shared.models import ApiError


def _should_start_inprocess_scheduler() -> bool:
    if settings.runtime_scheduler_mode != "inprocess" or not settings.runtime_scheduler_autostart:
        return False
    return not (os.getenv("PYTEST_CURRENT_TEST") or ".pytest_ai_quant" in os.getenv("POSTGRES_URL", ""))


def _is_local_console_api_only() -> bool:
    """Keep the desktop console responsive; scheduled work remains operator-triggered."""
    return os.getenv("PAPER_CONSOLE_API_ONLY", "false").lower() == "true"


@asynccontextmanager
async def lifespan(_: FastAPI):
    scheduler = None
    seed_task: asyncio.Task[int] | None = None
    ingest_task: asyncio.Task[dict] | None = None
    telegram_task: asyncio.Task[None] | None = None
    # The one-click desktop console must not consume its request workers with
    # a Top20 cycle or outbound feeds before the operator opens the workspace.
    local_api_only = _is_local_console_api_only()
    if not local_api_only:
        bootstrap_local_paper_runtime(seed_ohlcv=False)
    if not local_api_only and _should_start_inprocess_scheduler():
        scheduler = RuntimeScheduler()
        set_runtime_scheduler(scheduler)
        scheduler.start()
        if os.getenv("PAPER_CONSOLE_SKIP_BACKGROUND_BOOTSTRAP", "false").lower() != "true":
            seed_task = asyncio.create_task(asyncio.to_thread(bootstrap_seed_multi_timeframe_ohlcv))
            ingest_task = asyncio.create_task(asyncio.to_thread(bootstrap_poll_information_sources))
    if not local_api_only and settings.telegram_collector_enabled:
        telegram_task = asyncio.create_task(telegram_kol_runtime.start())
    try:
        yield
    finally:
        if seed_task is not None:
            seed_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await seed_task
        if ingest_task is not None:
            ingest_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await ingest_task
        if telegram_task is not None:
            telegram_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await telegram_task
        await telegram_kol_runtime.stop()
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
    serializable_errors = [{key: value for key, value in error.items() if key != "ctx"} for error in exc.errors()]
    payload = ApiError(
        error_code="validation_error",
        message="request validation failed",
        detail={"errors": serializable_errors},
    )
    return JSONResponse(status_code=422, content=payload.model_dump(mode="json"))


for router in (
    strategies.router,
    strategy_library.router,
    backtests.router,
    runs.router,
    risk.router,
    review.router,
    ingestion.router,
    agents.router,
    ensemble.router,
    market.router,
    market_intelligence.router,
    console.router,
    system.router,
    notifications.router,
    research_sources.router,
    runtime.router,
    telegram_kol.router,
):
    app.include_router(router, prefix="/api/v1")

app.include_router(metrics.router)
app.include_router(automated_trading.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "phase": "phase-0", "env": settings.app_env}


@app.get("/api/v1/health")
def health_v1() -> dict[str, str]:
    return health()
