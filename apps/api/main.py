"""API entrypoint. Mounts the versioned interface clusters."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from apps.api.config import settings
from apps.api.routers import agents, backtests, ingestion, review, risk, runs, strategies
from shared.models import ApiError

app = FastAPI(title="AI Quant Research Platform", version="0.1.0")


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
):
    app.include_router(router, prefix="/api/v1")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "phase": "phase-0", "env": settings.app_env}


@app.get("/api/v1/health")
def health_v1() -> dict[str, str]:
    return health()
