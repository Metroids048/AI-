"""Research-source contracts for E-level strategy knowledge intake."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from .base import PlatformModel


class StrategySourceManifest(PlatformModel):
    """Manifest for an open-source project used as research material only."""

    source_id: str
    name: str
    repo_url: str
    license: str
    project_role: str = Field(
        examples=["crypto_strategy_shapes", "research_framework", "llm_research_workflow"]
    )
    asset_categories: list[str] = Field(default_factory=list)
    crypto_relevance: str = Field(examples=["high", "medium", "low"])
    ingestion_status: str = "registered"
    rag_asset_refs: list[str] = Field(default_factory=list)
    strategy_idea_refs: list[str] = Field(default_factory=list)
    license_notes: str | None = None
    license_policy: str = "research_reference"
    asset_allowlist: list[dict[str, Any]] = Field(default_factory=list)
    asset_denylist: list[str] = Field(default_factory=list)
    strategy_extraction_targets: list[str] = Field(default_factory=list)
    priority: int = 100
    source_notes: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    last_scanned_at: datetime | None = None


class ResearchSourceAsset(PlatformModel):
    """Local RAG-ready asset distilled from an external research source."""

    asset_id: str
    source_id: str
    asset_type: str
    origin_url: str
    origin_ref: str
    license: str
    local_path: str
    sha256: str
    bytes: int
    ingestion_status: str = "imported"
    extraction_tags: list[str] = Field(default_factory=list)
    summary: str | None = None
    created_at: datetime | None = None


class ResearchSourceImportRequest(PlatformModel):
    source_ids: list[str] = Field(default_factory=list)
    refresh_assets: bool = True
    fetch_remote: bool = False


class ResearchSourceIdeaExtractionRequest(PlatformModel):
    persist_ideas: bool = True
    max_ideas: int | None = None


class ResearchSourceImportResult(PlatformModel):
    imported: list[StrategySourceManifest] = Field(default_factory=list)
    failed: list[StrategySourceManifest] = Field(default_factory=list)
    imported_assets: list[ResearchSourceAsset] = Field(default_factory=list)
    failed_assets: list[ResearchSourceAsset] = Field(default_factory=list)
