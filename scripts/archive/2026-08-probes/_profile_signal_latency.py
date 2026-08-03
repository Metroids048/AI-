"""Profile decision latency for ETH/BTC primary+sampling path."""

from __future__ import annotations

import time
from datetime import UTC, datetime

from services.data import DataRepository
from services.database import get_session_factory
from services.execution.paper_signal import PaperSignalGenerator
from services.strategy_library import PaperRunRepository, StrategyRepository
from shared.models import PaperRunStepRequest


def main() -> None:
    session = get_session_factory()()
    try:
        paper = PaperRunRepository(session).get_paper_run("78ba69a7-2bfb-457e-9a97-934aaf418e00")
        assert paper is not None
        strategy = StrategyRepository(session).get_strategy(paper.strategy_id)
        assert strategy is not None
        data_repo = DataRepository(session)
        gen = PaperSignalGenerator(data_repo=data_repo)
        for symbol in ("BTC/USDT", "ETH/USDT"):
            t0 = time.perf_counter()
            decision = gen._decision_for_strategy(
                strategy=strategy,
                symbol=symbol,
                timeframe="15m",
                request=PaperRunStepRequest(
                    symbol=symbol,
                    timeframe="15m",
                    decision_time=datetime.now(UTC),
                    enable_decision_veto=False,
                ),
                paper_run=paper,
            )
            dt = time.perf_counter() - t0
            print(
                {
                    "symbol": symbol,
                    "seconds": round(dt, 3),
                    "reason": decision.reason,
                    "should_trade": decision.should_trade,
                    "sampling": decision.trace.get("sampling_fallback_rejection_reason")
                    or decision.trace.get("pipeline_status"),
                }
            )
    finally:
        session.close()


if __name__ == "__main__":
    main()
