"""Open-source research source APIs."""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from apps.api.http import collection_response, not_found
from research_source.open_source_strategy_library import OpenSourceStrategyExtractor, OpenSourceStrategyLibrary
from services.database import get_db_session
from services.strategy_library import StrategyRepository
from shared.models import (
    CollectionResponse,
    ResearchSourceAsset,
    ResearchSourceIdeaExtractionRequest,
    ResearchSourceImportRequest,
    ResearchSourceImportResult,
    StrategyIdea,
    StrategySourceManifest,
)

router = APIRouter(prefix="/research-sources", tags=["research-sources"])


def _library() -> OpenSourceStrategyLibrary:
    return OpenSourceStrategyLibrary()


@router.get("", response_model=CollectionResponse[StrategySourceManifest])
def list_research_sources() -> CollectionResponse[StrategySourceManifest]:
    return collection_response(_library().list_sources())


@router.get("/{source_id}", response_model=StrategySourceManifest)
def get_research_source(source_id: str) -> StrategySourceManifest:
    source = _library().get_source(source_id)
    if source is None:
        raise not_found("research_source", source_id)
    return source


@router.get("/{source_id}/assets", response_model=CollectionResponse[ResearchSourceAsset])
def list_research_source_assets(source_id: str) -> CollectionResponse[ResearchSourceAsset]:
    source = _library().get_source(source_id)
    if source is None:
        raise not_found("research_source", source_id)
    return collection_response(_library().list_assets(source_id))


@router.post("/import", response_model=ResearchSourceImportResult, status_code=status.HTTP_202_ACCEPTED)
def import_research_sources(body: ResearchSourceImportRequest) -> ResearchSourceImportResult:
    return _library().import_sources(
        source_ids=body.source_ids,
        refresh_assets=body.refresh_assets,
        fetch_remote=body.fetch_remote,
    )


@router.post(
    "/{source_id}/refresh-assets",
    response_model=ResearchSourceImportResult,
    status_code=status.HTTP_202_ACCEPTED,
)
def refresh_research_source_assets(source_id: str) -> ResearchSourceImportResult:
    source = _library().get_source(source_id)
    if source is None:
        raise not_found("research_source", source_id)
    return _library().import_sources(source_ids=[source_id], refresh_assets=True, fetch_remote=True)


@router.post("/{source_id}/extract-ideas", response_model=CollectionResponse[StrategyIdea])
def extract_research_source_ideas(
    source_id: str,
    body: ResearchSourceIdeaExtractionRequest,
    db: Session = Depends(get_db_session),
) -> CollectionResponse[StrategyIdea]:
    source = _library().get_source(source_id)
    if source is None:
        raise not_found("research_source", source_id)
    ideas = OpenSourceStrategyExtractor().extract_ideas(source, max_ideas=body.max_ideas)
    if body.persist_ideas:
        repo = StrategyRepository(db)
        ideas = [repo.create_idea(idea) for idea in ideas]
    return collection_response(ideas)
