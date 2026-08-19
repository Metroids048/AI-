"""Fincept-inspired point-in-time research context, without a second data store."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import Field

from shared.models import PlatformModel


class ResearchContextItem(PlatformModel):
    family: Literal["market", "news", "macro", "social", "derivatives"]
    source: str
    observed_at: datetime | None = None
    available_at: datetime | None = None
    freshness_seconds: float | None = None
    status: Literal["available", "stale", "missing"] = "available"
    value: Any | None = None
    missing_reason: str | None = None


class ResearchContextBundle(PlatformModel):
    decision_time: datetime
    symbol: str | None = None
    items: list[ResearchContextItem] = Field(default_factory=list)
    bundle_hash: str | None = None

    def model_post_init(self, __context: Any) -> None:
        if self.bundle_hash is None:
            import hashlib
            import json

            payload = json.dumps(
                [item.model_dump(mode="json") for item in self.items], sort_keys=True, separators=(",", ":")
            ).encode()
            object.__setattr__(self, "bundle_hash", hashlib.sha256(payload).hexdigest())

    @classmethod
    def from_records(
        cls,
        records: list[dict[str, Any]],
        *,
        decision_time: datetime | None = None,
        symbol: str | None = None,
    ) -> ResearchContextBundle:
        decision_time = decision_time or datetime.now(UTC)
        items = [ResearchContextItem(**record) for record in records]
        return cls(decision_time=decision_time, symbol=symbol, items=items)

    @property
    def has_unavailable_data(self) -> bool:
        return any(item.status != "available" for item in self.items)
