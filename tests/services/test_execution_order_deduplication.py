from __future__ import annotations

from datetime import UTC, datetime

from services.strategy_library import ExecutionRepository
from shared.models import OrderExecution, TradeSide


def test_repository_returns_existing_order_for_same_candle_intent(db_session) -> None:
    repo = ExecutionRepository(db_session)
    identity = {
        "strategy_id": "strategy-dedupe",
        "symbol": "BTC/USDT",
        "timeframe": "1m",
        "signal_candle_close_time": datetime(2026, 7, 22, 1, 2, tzinfo=UTC),
        "intent_type": "open",
    }

    first = repo.create_order(OrderExecution(direction=TradeSide.LONG, **identity))
    duplicate = repo.create_order(OrderExecution(direction=TradeSide.LONG, **identity))

    assert duplicate.order_execution_id == first.order_execution_id
    assert len(repo.list_orders()) == 1
