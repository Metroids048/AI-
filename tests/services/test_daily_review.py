"""Daily Review service contracts."""

from __future__ import annotations

from services.review.service import ReviewService
from services.strategy_library import ReviewRepository


def test_daily_review_is_idempotent_and_lists_all_execution_symbols(db_session) -> None:
    service = ReviewService(ReviewRepository(db_session))

    first = service.build_daily_report("2026-08-16")
    second = service.build_daily_report("2026-08-16")

    assert first.review_report_id == second.review_report_id
    assert any("BTC/USDT:" in item for item in first.deviation_analysis)
    assert any("ETH/USDT:" in item for item in first.deviation_analysis)
    assert any("SOL/USDT:" in item for item in first.deviation_analysis)
    assert any("XRP/USDT:" in item for item in first.deviation_analysis)
    assert any("BNB/USDT:" in item for item in first.deviation_analysis)


def test_daily_review_explicit_date_does_not_use_current_day(db_session) -> None:
    service = ReviewService(ReviewRepository(db_session))

    report = service.build_daily_report("2026-08-15")

    assert report.report_date == "2026-08-15"
    assert report.created_at is not None
