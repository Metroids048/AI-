"""Review API backed by persisted review and failure repositories."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from apps.api.http import collection_response, not_found
from services.database import get_db_session
from services.review import ReviewService
from services.strategy_library import ReviewRepository
from shared.models import CollectionResponse, FailureRecord, ReviewReport

router = APIRouter(tags=["review"])


def _service(db: Session) -> ReviewService:
    return ReviewService(ReviewRepository(db))


@router.get("/reviews", response_model=CollectionResponse[ReviewReport])
def list_review_reports(db: Session = Depends(get_db_session)) -> CollectionResponse[ReviewReport]:
    return collection_response(_service(db).list_reports())


@router.post("/reviews", response_model=ReviewReport, status_code=status.HTTP_201_CREATED)
def create_review_report(body: ReviewReport, db: Session = Depends(get_db_session)) -> ReviewReport:
    return _service(db).create_report(body)


@router.post("/reviews/daily/{report_date}", response_model=ReviewReport, status_code=status.HTTP_201_CREATED)
def generate_daily_review_report(
    report_date: str, db: Session = Depends(get_db_session)
) -> ReviewReport:
    datetime.strptime(report_date, "%Y-%m-%d")
    return _service(db).build_daily_report(report_date)


@router.get("/reviews/{review_report_id}", response_model=ReviewReport)
def get_review_report(review_report_id: str, db: Session = Depends(get_db_session)) -> ReviewReport:
    report = ReviewRepository(db).get_report(review_report_id)
    if report is None:
        raise not_found("review_report", review_report_id)
    return report


@router.get("/failures", response_model=CollectionResponse[FailureRecord])
def list_failure_records(db: Session = Depends(get_db_session)) -> CollectionResponse[FailureRecord]:
    return collection_response(_service(db).list_failures())


@router.post("/failures", response_model=FailureRecord, status_code=status.HTTP_201_CREATED)
def create_failure_record(
    body: FailureRecord, db: Session = Depends(get_db_session)
) -> FailureRecord:
    return _service(db).record_failure(body)
