"""Paper and live run endpoints for the current execution slice."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from services.database import get_db_session
from services.execution import PaperOrchestrationService
from services.strategy_library import PaperRunRepository
from shared.models import LiveRun, PaperRun

router = APIRouter(tags=["execution"])

_LIVE_RUNS: dict[str, LiveRun] = {}


def _repo(db: Session) -> PaperRunRepository:
    return PaperRunRepository(db)


@router.get("/paper-runs", response_model=list[PaperRun])
def list_paper_runs(db: Session = Depends(get_db_session)) -> list[PaperRun]:
    return _repo(db).list_paper_runs()


@router.post("/paper-runs", response_model=PaperRun, status_code=status.HTTP_201_CREATED)
def create_paper_run(body: PaperRun, db: Session = Depends(get_db_session)) -> PaperRun:
    prepared = PaperOrchestrationService().prepare_run(body)
    return _repo(db).create_paper_run(prepared)


@router.get("/paper-runs/{paper_run_id}", response_model=PaperRun)
def get_paper_run(paper_run_id: str, db: Session = Depends(get_db_session)) -> PaperRun:
    run = _repo(db).get_paper_run(paper_run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="paper run not found")
    return run


@router.get("/live-runs", response_model=list[LiveRun])
def list_live_runs() -> list[LiveRun]:
    return list(_LIVE_RUNS.values())
