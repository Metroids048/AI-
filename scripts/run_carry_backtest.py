"""Submit a persisted carry backtest from the command line."""

from __future__ import annotations

import argparse
import json
from datetime import datetime

from services.data import DataRepository
from services.database import get_session_factory
from services.strategy_library import StrategyRepository, ValidationRepository
from services.validation import CarryBacktestApplicationService
from shared.models import CarryBacktestRequest


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Run persisted-data carry backtest.")
    parser.add_argument("--strategy-id", required=True)
    parser.add_argument("--spot-symbol", default="BTC/USDT")
    parser.add_argument("--perp-symbol", default="BTC/USDT:USDT")
    parser.add_argument("--timeframe", default="1h")
    parser.add_argument("--start-at", required=True)
    parser.add_argument("--end-at", required=True)
    args = parser.parse_args()

    session = get_session_factory()()
    try:
        service = CarryBacktestApplicationService(
            strategy_repo=StrategyRepository(session),
            validation_repo=ValidationRepository(session),
            data_repo=DataRepository(session),
        )
        run = service.submit(
            CarryBacktestRequest(
                strategy_id=args.strategy_id,
                spot_symbol=args.spot_symbol,
                perp_symbol=args.perp_symbol,
                timeframe=args.timeframe,
                start_at=_parse_dt(args.start_at),
                end_at=_parse_dt(args.end_at),
            )
        )
    finally:
        session.close()
    print(json.dumps(run.model_dump(mode="json"), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
