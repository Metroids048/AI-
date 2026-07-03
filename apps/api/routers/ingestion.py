"""Reference data ingestion API backed by the persisted ingestion repository."""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from apps.api.http import collection_response, not_found
from services.data import IngestionService
from services.database import get_db_session
from services.strategy_library import IngestionRepository
from shared.models import CollectionResponse, IngestionJob, IngestionJobRequest, TaskSubmission

router = APIRouter(prefix="/ingestion", tags=["ingestion"])


def _repo(db: Session) -> IngestionRepository:
    return IngestionRepository(db)


@router.get("/jobs", response_model=CollectionResponse[IngestionJob])
def list_ingestion_jobs(db: Session = Depends(get_db_session)) -> CollectionResponse[IngestionJob]:
    return collection_response(_repo(db).list_jobs())


@router.post("/jobs", response_model=TaskSubmission, status_code=status.HTTP_202_ACCEPTED)
def create_ingestion_job(body: IngestionJobRequest, db: Session = Depends(get_db_session)) -> TaskSubmission:
    created = _repo(db).create_job(
        IngestionService().prepare_job(
            IngestionJob(
                source_family=body.source_family,
                source_name=body.source_name,
                job_type=body.job_type,
                schedule_mode=body.schedule_mode,
                input_window=body.input_window,
                target_symbols=body.target_symbols,
                execution_summary={"submitted_via": "api"},
            )
        )
    )
    return TaskSubmission(
        task_id=body.idempotency_key or created.ingestion_job_id,
        resource_type="ingestion_job",
        resource_id=created.ingestion_job_id,
        detail={"target_symbols": created.target_symbols},
    )


@router.get("/jobs/{ingestion_job_id}", response_model=IngestionJob)
def get_ingestion_job(ingestion_job_id: str, db: Session = Depends(get_db_session)) -> IngestionJob:
    job = _repo(db).get_job(ingestion_job_id)
    if job is None:
        raise not_found("ingestion_job", ingestion_job_id)
    return job
