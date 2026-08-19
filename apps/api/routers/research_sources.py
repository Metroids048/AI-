"""Open-source research source APIs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from apps.api.http import collection_response, not_found
from research_source.open_source_strategy_library import OpenSourceStrategyExtractor, OpenSourceStrategyLibrary
from services.database import get_db_session
from services.research.integrations import FreqtradeValidationAdapter, VectorbtScreenAdapter
from services.strategy_library import OptimizationRepository, StrategyRepository, ValidationRepository
from shared.models import (
    BacktestRun,
    CollectionResponse,
    OptimizationRun,
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


def _engine_pins() -> dict:
    path = (
        Path(__file__).resolve().parents[3]
        / "research_source"
        / "open_source_strategy_library"
        / "manifests"
        / "engine_pins.json"
    )
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"schema_version": 1, "engines": {}}


@router.get("", response_model=CollectionResponse[StrategySourceManifest])
def list_research_sources() -> CollectionResponse[StrategySourceManifest]:
    return collection_response(_library().list_sources())


@router.get("/runtime", response_model=dict)
def research_runtime_health(db: Session = Depends(get_db_session)) -> dict:
    """Research control-plane truth; never reports production authorization."""
    backtests = ValidationRepository(db).list_backtest_runs()
    optimizations = OptimizationRepository(db).list_runs()
    statuses = [run.run_status for run in backtests] + [run.run_status for run in optimizations]
    research_truth = _research_truth(backtests, optimizations)
    return {
        "research_only": True,
        "production_authorization": "PENDING",
        "validation_conclusion": "NO_VALIDATED_EDGE",
        "engines": {
            "vectorbt": VectorbtScreenAdapter().health(),
            "freqtrade": FreqtradeValidationAdapter().health(),
        },
        "pinned_sources": _engine_pins(),
        "runs": {
            "queued": statuses.count("queued"),
            "running": statuses.count("running"),
            "completed": statuses.count("completed"),
            "failed": statuses.count("failed"),
            "recent": [
                {
                    "id": run.backtest_run_id,
                    "type": "backtest",
                    "engine": run.execution_engine,
                    "status": run.run_status,
                }
                for run in backtests[-10:]
            ]
            + [
                {"id": run.optimization_run_id, "type": "optimization", "engine": "research", "status": run.run_status}
                for run in optimizations[-10:]
            ],
        },
        "research_truth": research_truth,
    }


def _research_truth(backtests: list[BacktestRun], optimizations: list[OptimizationRun]) -> dict:
    """Expose research evidence without treating it as production authorization."""
    runs: list[dict[str, Any]] = []
    for backtest in backtests:
        result = (backtest.validation_methodology or {}).get("research_result") or {}
        if result:
            runs.append({"run_id": backtest.backtest_run_id, "status": backtest.run_status, **result})
    for optimization in optimizations:
        result = (optimization.best_candidate_summary or {}).get("research_result") or {}
        if result:
            runs.append({"run_id": optimization.optimization_run_id, "status": optimization.run_status, **result})
    candidates: list[dict[str, Any]] = []
    for research_run in runs:
        vectorbt = research_run.get("vectorbt") or {}
        plateau = vectorbt.get("parameter_plateau") or {}
        for candidate in plateau.get("top_candidates") or []:
            candidates.append({"run_id": research_run["run_id"], "stage": research_run.get("stage"), **candidate})
    candidates.sort(key=lambda item: float(item.get("expectancy_net_r") or float("-inf")), reverse=True)
    return {
        "top_candidates": candidates[:10],
        "plateaus": [
            {
                "run_id": research_run["run_id"],
                "plateau": ((research_run.get("vectorbt") or {}).get("parameter_plateau") or {}),
            }
            for research_run in runs
        ],
        "bias_gates": [
            {
                "run_id": research_run["run_id"],
                "lookahead": ((research_run.get("freqtrade") or {}).get("lookahead_status") or "NOT_RUN"),
                "recursive": ((research_run.get("freqtrade") or {}).get("recursive_status") or "NOT_RUN"),
            }
            for research_run in runs
        ],
        "native_oos": [
            {
                "run_id": research_run["run_id"],
                "stage": research_run.get("stage"),
                "status": ((research_run.get("native_oos") or {}).get("status") or "NOT_RUN"),
            }
            for research_run in runs
        ],
        "council": [
            {
                "run_id": research_run["run_id"],
                "verdict": ((research_run.get("council") or {}).get("verdict") or "NOT_RUN"),
            }
            for research_run in runs
        ],
    }


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
