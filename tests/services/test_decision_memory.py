from __future__ import annotations

from services.review.decision_memory import DecisionMemoryService
from services.strategy_library import DecisionMemoryRepository, StrategyRepository
from shared.models import DecisionMemoryEntry, StrategyCreate


def test_decision_memory_records_and_filters_entries(db_session) -> None:
    strategy = StrategyRepository(db_session).create_strategy(
        StrategyCreate(
            strategy_key="decision_memory_strategy",
            source="manual",
            core_thesis="decision memory should persist promotion evidence",
        )
    )

    service = DecisionMemoryService(DecisionMemoryRepository(db_session))
    created = service.record_entry(
        DecisionMemoryEntry(
            scope_type="strategy",
            scope_id=strategy.strategy_id,
            decision_type="validation_admission",
            verdict="rejected",
            summary="Rejected because randomized control failed and DSR < 1.0",
            tags=["validation", "benchmark", "dsr"],
            evidence_refs=["backtest_run:bt-1", "hypothesis:hyp-1"],
            context_payload={"failed_thresholds": ["min_deflated_sharpe", "benchmark_control_failed"]},
        )
    )

    entries = service.list_entries(scope_id=strategy.strategy_id, decision_type="validation_admission")

    assert created.decision_memory_id is not None
    assert len(entries) == 1
    assert entries[0].verdict == "rejected"
    assert "benchmark" in entries[0].tags
