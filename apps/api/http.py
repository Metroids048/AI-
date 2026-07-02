"""Shared API helpers for versioned envelopes and error payloads."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status

from shared.models import ApiError, CollectionResponse


def collection_response(items: list[Any]) -> CollectionResponse[Any]:
    return CollectionResponse(items=items, total=len(items))


def api_error(
    *,
    status_code: int,
    error_code: str,
    message: str,
    detail: dict[str, Any] | None = None,
) -> HTTPException:
    payload = ApiError(error_code=error_code, message=message, detail=detail)
    return HTTPException(status_code=status_code, detail=payload.model_dump(mode="json"))


def not_found(resource: str, resource_id: str) -> HTTPException:
    return api_error(
        status_code=status.HTTP_404_NOT_FOUND,
        error_code=f"{resource}_not_found",
        message=f"{resource.replace('_', ' ')} not found",
        detail={"resource_id": resource_id},
    )
