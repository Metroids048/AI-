"""Operator-facing, code-backed Strategy Library playbook contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field, model_validator

from .base import PlatformModel

RoadmapStatus = Literal["pending", "in_progress", "done"]


class PlaybookMetadata(PlatformModel):
    verified_on: str
    verified_commit: str
    source_documents: list[str]
    disclaimer: str


class StrategyChannel(PlatformModel):
    channel_id: str
    name: str
    positioning: str
    core_assumption: str
    maturity: str


class DecisionStage(PlatformModel):
    stage_id: str
    name: str
    description: str


class TechnicalSignalDefinition(PlatformModel):
    signal_id: str
    name: str
    parameters: dict[str, Any]
    trigger: str
    role: str


class ExitRule(PlatformModel):
    rule_id: str
    name: str
    priority: int
    description: str


class ScopedDefault(PlatformModel):
    key: str
    value: float
    scope: str
    source_ref: str


class PositionSizingPolicy(PlatformModel):
    formula: str
    defaults: list[ScopedDefault]
    limitations: list[str]


class LlmRagBoundary(PlatformModel):
    allowed: list[str]
    forbidden: list[str]
    retrieval_mode: str
    provider_chain: list[str]
    limitations: list[str]


class ExternalStrategySource(PlatformModel):
    source_id: str
    name: str
    repo_url: str
    license: str
    license_policy: str
    absorbable_content: str
    platform_mapping: str
    implementation_status: str


class RoadmapAuditEntry(PlatformModel):
    status: RoadmapStatus
    note: str | None = None
    updated_by: str
    updated_at: datetime


class OptimizationRoadmapItem(PlatformModel):
    item_id: str
    title: str
    priority: Literal["P0", "P1", "P2"]
    status: RoadmapStatus = "pending"
    description: str
    optimization_target: str
    note: str | None = None
    updated_by: str | None = None
    updated_at: datetime | None = None
    audit_history: list[RoadmapAuditEntry] = Field(default_factory=list)


class RoadmapUpdate(PlatformModel):
    status: RoadmapStatus | None = None
    note: str | None = None
    updated_by: str | None = None

    @model_validator(mode="after")
    def reject_empty_update(self) -> RoadmapUpdate:
        if self.status is None and self.note is None:
            raise ValueError("status or note is required")
        return self


class StrategyPlaybook(PlatformModel):
    metadata: PlaybookMetadata
    channels: list[StrategyChannel]
    decision_stages: list[DecisionStage]
    technical_signals: list[TechnicalSignalDefinition]
    exit_rules: list[ExitRule]
    position_sizing: PositionSizingPolicy
    llm_rag: LlmRagBoundary
    external_sources: list[ExternalStrategySource]
    roadmap: list[OptimizationRoadmapItem]
