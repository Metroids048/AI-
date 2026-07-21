from __future__ import annotations

from scripts.audit_full_lifecycle_completion import audit_lifecycle_completion
from services.strategy_library import ExecutionRepository, StrategyRepository
from shared.models import OrderExecution, StrategyCreate, StrategyRules, TradeSide


def test_audit_uses_current_order_contract_and_pairs_completed_trade(db_session) -> None:
    strategy = StrategyRepository(db_session).create_strategy(
        StrategyCreate(
            strategy_key="lifecycle-test",
            source="test",
            core_thesis="audit current order contract",
            rules=StrategyRules(exit_rules={"time_exit_hours": 24}),
        )
    )
    repo = ExecutionRepository(db_session)
    for close_only in (False, True):
        repo.create_order(
            OrderExecution(
                strategy_id=strategy.strategy_id or "",
                symbol="BTC/USDT",
                direction=TradeSide.LONG,
                execution_status="filled",
                close_only_mode=close_only,
                stoploss_present=True,
                gateway_name="binance_usdm",
                gateway_order_id="close-1" if close_only else "open-1",
                entry_context={"strategy_lane": "directional"},
            )
        )

    result = audit_lifecycle_completion(days=1, database_url=str(db_session.get_bind().engine.url))

    assert len(result["completed"]) == 1
    assert result["in_progress"] == []
    assert result["stuck"] == []
    assert result["ledger_fork"] == []
