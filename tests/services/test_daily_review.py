"""Daily Review service contracts."""

from __future__ import annotations

from datetime import UTC, datetime

from services.automated_trading.domain.enums import V2ExecutionMode
from services.automated_trading.infrastructure.repository import AutomatedTradingRepository
from services.review.service import ReviewService
from services.strategy_library import ReviewRepository


def test_daily_review_is_idempotent_and_separates_execution_and_research_symbols(db_session) -> None:
    service = ReviewService(ReviewRepository(db_session))

    first = service.build_daily_report("2026-08-16")
    second = service.build_daily_report("2026-08-16")

    assert first.review_report_id == second.review_report_id
    assert any("BTC/USDT:" in item for item in first.deviation_analysis)
    assert any("ETH/USDT:" in item for item in first.deviation_analysis)
    assert any("当日已平仓 0 笔" in item for item in first.deviation_analysis)
    assert any("自动执行绩效范围: BTC/USDT, ETH/USDT" in item for item in first.deviation_analysis)
    assert any("SOL/USDT: 研究覆盖，不计入自动执行绩效" in item for item in first.deviation_analysis)
    assert any("XRP/USDT: 研究覆盖，不计入自动执行绩效" in item for item in first.deviation_analysis)
    assert any("BNB/USDT: 研究覆盖，不计入自动执行绩效" in item for item in first.deviation_analysis)


def test_daily_review_explicit_date_does_not_use_current_day(db_session) -> None:
    service = ReviewService(ReviewRepository(db_session))

    report = service.build_daily_report("2026-08-15")

    assert report.report_date == "2026-08-15"
    assert report.created_at is not None


def test_daily_review_keeps_all_terminal_reasons_for_same_symbol(db_session) -> None:
    repo = AutomatedTradingRepository(db_session)
    for cycle_id, reason in (("cycle-review-a", "NO_SIGNAL"), ("cycle-review-b", "COST_OR_R2_BLOCK")):
        repo.create_cycle(
            cycle_id=cycle_id,
            symbol="BTC/USDT",
            timeframe="15m",
            bar_timestamp=datetime(2026, 8, 17, 12, tzinfo=UTC),
            execution_mode=V2ExecutionMode.BINANCE_TESTNET,
            fencing_token=f"fence-{cycle_id}",
        )
        repo.create_decision(
            decision_id=f"decision-{cycle_id}",
            cycle_id=cycle_id,
            terminal_reason=reason,
            payload={},
        )
    repo.commit()

    report = ReviewService(ReviewRepository(db_session)).build_daily_report("2026-08-17")

    assert "BTC/USDT: NO_SIGNAL, COST_OR_R2_BLOCK" in report.deviation_analysis
