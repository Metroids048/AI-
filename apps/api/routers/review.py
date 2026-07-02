"""Review & Reporting API skeleton (interface cluster 7.5)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, status

from shared.models import FailureRecord, ReviewReport

router = APIRouter(tags=["review"])

_REVIEWS: dict[str, ReviewReport] = {}
_FAILURES: dict[str, FailureRecord] = {}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@router.get("/reviews", response_model=list[ReviewReport])
def list_review_reports() -> list[ReviewReport]:
    return list(_REVIEWS.values())


@router.post("/reviews", response_model=ReviewReport, status_code=status.HTTP_201_CREATED)
def create_review_report(body: ReviewReport) -> ReviewReport:
    report = body.model_copy(
        update={
            "review_report_id": body.review_report_id or str(uuid.uuid4()),
            "created_at": body.created_at or _utcnow(),
        }
    )
    _REVIEWS[report.review_report_id] = report
    return report


@router.get("/reviews/{review_report_id}", response_model=ReviewReport)
def get_review_report(review_report_id: str) -> ReviewReport:
    report = _REVIEWS.get(review_report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="review report not found")
    return report


@router.get("/failures", response_model=list[FailureRecord])
def list_failure_records() -> list[FailureRecord]:
    return list(_FAILURES.values())


@router.post("/failures", response_model=FailureRecord, status_code=status.HTTP_201_CREATED)
def create_failure_record(body: FailureRecord) -> FailureRecord:
    record = body.model_copy(
        update={
            "failure_record_id": body.failure_record_id or str(uuid.uuid4()),
            "created_at": body.created_at or _utcnow(),
        }
    )
    _FAILURES[record.failure_record_id] = record
    return record
