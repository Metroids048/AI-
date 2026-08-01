"""Review-layer decision memory contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from .base import PlatformModel


class DecisionMemoryEntry(PlatformModel):
    decision_memory_id: str | None = None
    scope_type: str = Field(description="strategy / backtest / paper_run / live_run / agent_task")
    scope_id: str
    decision_type: str = Field(description="validation_admission / promotion_gate / gateway_reconcile / llm_agent")
    verdict: str = Field(description="accepted / rejected / warning / recovered")
    summary: str
    tags: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    context_payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = None
