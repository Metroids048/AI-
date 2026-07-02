"""Reference data ingestion API backed by the ingestion repository."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from services.data import IngestionService
from services.database import get_db_session
from services.strategy_library import IngestionRepository
from shared.models import IngestionJob

router = APIRouter(prefix="/ingestion", tags=["ingestion"])


def _repo(db: Session) -> IngestionRepository:
    return IngestionRepository(db)


@router.get("/jobs", response_model=list[IngestionJob])
def list_ingestion_jobs(db: Session = Depends(get_db_session)) -> list[IngestionJob]:
    return _repo(db).list_jobs()


@router.post("/jobs", response_model=IngestionJob, status_code=status.HTTP_201_CREATED)
def create_ingestion_job(
    body: IngestionJob, db: Session = Depends(get_db_session)
) -> IngestionJob:
    return _repo(db).create_job(IngestionService().prepare_job(body))


@router.get("/jobs/{ingestion_job_id}", response_model=IngestionJob)
def get_ingestion_job(
    ingestion_job_id: str, db: Session = Depends(get_db_session)
) -> IngestionJob:
    job = _repo(db).get_job(ingestion_job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="ingestion job not found")
    return job
