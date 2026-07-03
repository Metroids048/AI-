"""Review-layer service for persisted reports and failure writeback."""

from __future__ import annotations

from datetime import UTC, datetime

from services.strategy_library import ReviewRepository
from shared.models import FailureRecord, ReviewReport


class ReviewService:
    def __init__(self, review_repo: ReviewRepository) -> None:
        self.review_repo = review_repo

    def list_reports(self) -> list[ReviewReport]:
        return self.review_repo.list_reports()

    def create_report(self, report: ReviewReport) -> ReviewReport:
        payload = report
        if payload.created_at is None:
            payload = payload.model_copy(update={"created_at": datetime.now(UTC)})
        return self.review_repo.create_report(payload)

    def list_failures(self) -> list[FailureRecord]:
        return self.review_repo.list_failures()

    def record_failure(self, record: FailureRecord) -> FailureRecord:
        payload = record
        if payload.created_at is None:
            payload = payload.model_copy(update={"created_at": datetime.now(UTC)})
        return self.review_repo.create_failure(payload)

    def build_daily_report(self, report_date: str) -> ReviewReport:
        failures = [
            failure
            for failure in self.review_repo.list_failures()
            if failure.created_at is not None and failure.created_at.date().isoformat() == report_date
        ]
        strategy_refs = sorted({failure.strategy_id for failure in failures})
        recommendations = sorted({failure.recommended_change for failure in failures if failure.recommended_change})
        report = ReviewReport(
            report_date=report_date,
            scope_type="daily",
            strategy_refs=strategy_refs,
            worst_performer_refs=strategy_refs[:3],
            failure_patterns=[failure.failure_type for failure in failures],
            deviation_analysis=[failure.failure_summary for failure in failures],
            recommendations=recommendations,
            report_status="generated",
            created_at=datetime.now(UTC),
        )
        return self.review_repo.create_report(report)
