"""Single-tenant API authentication for the admin surface."""

from __future__ import annotations

import secrets

from fastapi import Request, status
from fastapi.responses import JSONResponse

from apps.api.config import settings
from shared.models import ApiError

PUBLIC_PATHS = {"/health", "/api/v1/health"}
LOCAL_ENVIRONMENTS = {"development", "dev", "test"}
DEFAULT_ADMIN_TOKEN = "dev-admin-token"


def _error_response(*, status_code: int, error_code: str, message: str) -> JSONResponse:
    payload = ApiError(error_code=error_code, message=message, detail={}).model_dump(mode="json")
    return JSONResponse(status_code=status_code, content=payload)


def _extract_bearer_token(request: Request) -> str | None:
    authorization = request.headers.get("Authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    return token.strip()


def _default_token_used_outside_local_env() -> bool:
    return (
        settings.app_env.lower() not in LOCAL_ENVIRONMENTS
        and settings.admin_api_token == DEFAULT_ADMIN_TOKEN
    )


async def admin_token_middleware(request: Request, call_next):
    path = request.url.path
    if not path.startswith("/api/v1") or path in PUBLIC_PATHS:
        return await call_next(request)

    if _default_token_used_outside_local_env():
        return _error_response(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            error_code="auth_misconfigured",
            message="ADMIN_API_TOKEN must be changed outside local development and test environments",
        )

    token = _extract_bearer_token(request)
    if token is None:
        return _error_response(
            status_code=status.HTTP_401_UNAUTHORIZED,
            error_code="auth_required",
            message="bearer token required",
        )
    if not secrets.compare_digest(token, settings.admin_api_token):
        return _error_response(
            status_code=status.HTTP_403_FORBIDDEN,
            error_code="auth_invalid_token",
            message="bearer token is invalid",
        )
    return await call_next(request)
