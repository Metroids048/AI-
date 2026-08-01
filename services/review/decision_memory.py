"""Decision memory service for reusable validation/execution/agent evidence."""

from __future__ import annotations

from datetime import UTC, datetime

from services.strategy_library import DecisionMemoryRepository
from shared.models import DecisionMemoryEntry


class DecisionMemoryService:
    def __init__(self, repository: DecisionMemoryRepository) -> None:
        self.repository = repository

    def list_entries(
        self,
        *,
        scope_type: str | None = None,
        scope_id: str | None = None,
        decision_type: str | None = None,
        verdict: str | None = None,
    ) -> list[DecisionMemoryEntry]:
        return self.repository.list_entries(
            scope_type=scope_type,
            scope_id=scope_id,
            decision_type=decision_type,
            verdict=verdict,
        )

    def record_entry(self, entry: DecisionMemoryEntry) -> DecisionMemoryEntry:
        payload = entry
        if payload.created_at is None:
            payload = payload.model_copy(update={"created_at": datetime.now(UTC)})
        return self.repository.create_entry(payload)
