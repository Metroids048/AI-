"""Review-layer service for persisted reports and failure writeback."""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta

from sqlalchemy import select

from services.automated_trading.infrastructure.models import (
    V2ExecutionCycle,
    V2ExecutionDecision,
    V2ManagedPosition,
)
from services.strategy_library import ReviewRepository
from services.strategy_library import models as strategy_models
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

    def list_failures(
        self,
        *,
        strategy_id: str | None = None,
        idea_id: str | None = None,
        failure_type: str | None = None,
        limit: int = 50,
    ) -> list[FailureRecord]:
        return self.review_repo.list_failures(
            strategy_id=strategy_id,
            idea_id=idea_id,
            failure_type=failure_type,
            limit=limit,
        )

    def record_failure(self, record: FailureRecord) -> FailureRecord:
        payload = record
        if payload.created_at is None:
            payload = payload.model_copy(update={"created_at": datetime.now(UTC)})
        return self.review_repo.create_failure(payload)

    def build_daily_report(self, report_date: str) -> ReviewReport:
        """Build one idempotent, Chinese-first report for a complete UTC day."""
        existing = self.review_repo.session.scalar(
            select(strategy_models.ReviewReport)
            .where(
                strategy_models.ReviewReport.report_date == report_date,
                strategy_models.ReviewReport.scope_type == "daily",
            )
            .order_by(strategy_models.ReviewReport.created_at.desc())
        )
        if existing is not None:
            return self.review_repo.get_report(existing.review_report_id) or self.review_repo.create_report(
                ReviewReport(report_date=report_date, scope_type="daily")
            )

        day = datetime.fromisoformat(report_date).date()
        start = datetime.combine(day, time.min)
        end = start + timedelta(days=1)
        session = self.review_repo.session
        positions = tuple(
            session.scalars(
                select(V2ManagedPosition).where(
                    V2ManagedPosition.closed_at.is_not(None),
                    V2ManagedPosition.closed_at >= start,
                    V2ManagedPosition.closed_at < end,
                )
            )
        )
        decisions = tuple(
            session.execute(
                select(V2ExecutionDecision, V2ExecutionCycle.symbol)
                .join(V2ExecutionCycle, V2ExecutionCycle.cycle_id == V2ExecutionDecision.cycle_id)
                .where(
                    # A daily strategy report is indexed by the evaluated
                    # closed bar, not wall-clock persistence time.  Delayed
                    # writes/recovery must remain attributable to their
                    # original decision day.
                    V2ExecutionCycle.bar_timestamp >= start,
                    V2ExecutionCycle.bar_timestamp < end,
                )
            )
        )
        failures = [
            failure
            for failure in self.review_repo.list_failures()
            if failure.created_at is not None and failure.created_at.date().isoformat() == report_date
        ]
        strategy_refs = sorted({failure.strategy_id for failure in failures if failure.strategy_id is not None})
        symbol_pnl = {
            symbol: sum(
                (position.realized_pnl or 0 for position in positions if position.symbol == symbol),
                0,
            )
            for symbol in ("BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "BNB/USDT")
        }
        terminal_reasons_by_symbol: dict[str, list[str]] = {}
        for decision, symbol in decisions:
            if decision.terminal_reason:
                terminal_reasons_by_symbol.setdefault(symbol, []).append(decision.terminal_reason)
        no_trade_explanations = [
            f"{symbol}: {', '.join(terminal_reasons_by_symbol.get(symbol, ['UNKNOWN_NO_DECISION_EVIDENCE']))}"
            for symbol, pnl in symbol_pnl.items()
            if pnl == 0
        ]
        total_pnl = sum((position.realized_pnl or 0 for position in positions), 0)
        recommendations = sorted({failure.recommended_change for failure in failures if failure.recommended_change})
        report = ReviewReport(
            report_date=report_date,
            scope_type="daily",
            strategy_refs=strategy_refs,
            worst_performer_refs=[symbol for symbol, _ in sorted(symbol_pnl.items(), key=lambda item: item[1])[:3]],
            failure_patterns=[failure.failure_type for failure in failures],
            deviation_analysis=[
                f"当日已平仓 {len(positions)} 笔，已实现净损益 {total_pnl}",
                *[f"{symbol}: 已实现损益 {pnl}" for symbol, pnl in symbol_pnl.items()],
                *no_trade_explanations,
                *[failure.failure_summary for failure in failures],
            ],
            recommendations=recommendations,
            report_status="generated",
            created_at=datetime.now(UTC),
        )
        return self.review_repo.create_report(report)
