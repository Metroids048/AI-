"""Common API envelopes and submission acknowledgements."""

from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import Field

from .base import PlatformModel

T = TypeVar("T")


class CollectionResponse(PlatformModel, Generic[T]):
    items: list[T] = Field(default_factory=list)
    total: int = 0


class TaskSubmission(PlatformModel):
    task_id: str | None = None
    resource_type: str
    resource_id: str | None = None
    status: str = "accepted"
    detail: dict[str, Any] = Field(default_factory=dict)


class ApiError(PlatformModel):
    error_code: str
    message: str
    detail: dict[str, Any] | None = None
