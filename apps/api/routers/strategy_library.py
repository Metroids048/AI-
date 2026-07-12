"""Read-only strategy playbook and controlled roadmap state updates."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from apps.api.http import not_found
from services.database import get_db_session
from services.strategy_library.playbook import build_playbook, update_roadmap_item
from shared.models import OptimizationRoadmapItem, RoadmapUpdate, StrategyPlaybook

router = APIRouter(prefix="/strategy-library", tags=["strategy-library"])


@router.get("/playbook", response_model=StrategyPlaybook)
def get_strategy_playbook(db: Session = Depends(get_db_session)) -> StrategyPlaybook:
    return build_playbook(db)


@router.patch("/roadmap-items/{item_id}", response_model=OptimizationRoadmapItem)
def patch_roadmap_item(
    item_id: str,
    body: RoadmapUpdate,
    db: Session = Depends(get_db_session),
) -> OptimizationRoadmapItem:
    item = update_roadmap_item(db, item_id, body)
    if item is None:
        raise not_found("strategy_roadmap_item", item_id)
    return item
