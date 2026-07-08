"""Strategy lifecycle API backed by the persisted strategy repository."""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from apps.api.http import collection_response, not_found
from services.database import get_db_session
from services.strategy_library import StrategyRepository
from shared.models import (
    CollectionResponse,
    StrategyCreate,
    StrategyDraft,
    StrategyIdea,
    StrategyRead,
    StrategyUpdate,
    StrategyVersion,
)

router = APIRouter(prefix="/strategies", tags=["strategies"])


def _repo(db: Session) -> StrategyRepository:
    return StrategyRepository(db)


@router.get("/ideas", response_model=CollectionResponse[StrategyIdea])
def list_strategy_ideas(db: Session = Depends(get_db_session)) -> CollectionResponse[StrategyIdea]:
    return collection_response(_repo(db).list_ideas())


@router.post("/ideas", response_model=StrategyIdea, status_code=status.HTTP_201_CREATED)
def create_strategy_idea(body: StrategyIdea, db: Session = Depends(get_db_session)) -> StrategyIdea:
    return _repo(db).create_idea(body)


@router.post(
    "/ideas/{idea_id}/drafts",
    response_model=StrategyDraft,
    status_code=status.HTTP_201_CREATED,
)
def promote_idea_to_draft(idea_id: str, db: Session = Depends(get_db_session)) -> StrategyDraft:
    draft = _repo(db).promote_idea_to_draft(idea_id)
    if draft is None:
        raise not_found("strategy_idea", idea_id)
    return draft


@router.get("/drafts", response_model=CollectionResponse[StrategyDraft])
def list_strategy_drafts(db: Session = Depends(get_db_session)) -> CollectionResponse[StrategyDraft]:
    return collection_response(_repo(db).list_drafts())


@router.post("/drafts", response_model=StrategyDraft, status_code=status.HTTP_201_CREATED)
def create_strategy_draft(body: StrategyDraft, db: Session = Depends(get_db_session)) -> StrategyDraft:
    return _repo(db).create_draft(body)


@router.post("/{draft_id}/materialize", response_model=StrategyRead, status_code=status.HTTP_201_CREATED)
def materialize_strategy_from_draft(draft_id: str, db: Session = Depends(get_db_session)) -> StrategyRead:
    strategy = _repo(db).materialize_strategy_from_draft(draft_id)
    if strategy is None:
        raise not_found("strategy_draft", draft_id)
    return strategy


@router.get("/versions", response_model=CollectionResponse[StrategyVersion])
def list_strategy_versions(
    strategy_id: str | None = None,
    db: Session = Depends(get_db_session),
) -> CollectionResponse[StrategyVersion]:
    return collection_response(_repo(db).list_versions(strategy_id=strategy_id))


@router.post("/versions", response_model=StrategyVersion, status_code=status.HTTP_201_CREATED)
def create_strategy_version(body: StrategyVersion, db: Session = Depends(get_db_session)) -> StrategyVersion:
    return _repo(db).create_version(body)


@router.get("", response_model=CollectionResponse[StrategyRead])
def list_strategies(db: Session = Depends(get_db_session)) -> CollectionResponse[StrategyRead]:
    return collection_response(_repo(db).list_strategies())


@router.post("", response_model=StrategyRead, status_code=status.HTTP_201_CREATED)
def create_strategy(body: StrategyCreate, db: Session = Depends(get_db_session)) -> StrategyRead:
    return _repo(db).create_strategy(body)


@router.get("/{strategy_id}", response_model=StrategyRead)
def get_strategy(strategy_id: str, db: Session = Depends(get_db_session)) -> StrategyRead:
    strategy = _repo(db).get_strategy(strategy_id)
    if strategy is None:
        raise not_found("strategy", strategy_id)
    return strategy


@router.put("/{strategy_id}", response_model=StrategyRead)
def update_strategy(strategy_id: str, body: StrategyUpdate, db: Session = Depends(get_db_session)) -> StrategyRead:
    strategy = _repo(db).update_strategy(strategy_id, body)
    if strategy is None:
        raise not_found("strategy", strategy_id)
    return strategy


@router.delete(
    "/{strategy_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
)
def delete_strategy(strategy_id: str, db: Session = Depends(get_db_session)) -> None:
    if not _repo(db).delete_strategy(strategy_id):
        raise not_found("strategy", strategy_id)
