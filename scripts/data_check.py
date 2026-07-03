"""Check persisted OHLCV gaps for the configured database."""

from __future__ import annotations

import argparse
import json
from datetime import datetime

from services.data import DataRepository
from services.database import get_session_factory


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Check persisted OHLCV gaps.")
    parser.add_argument("--symbol", default="BTC/USDT")
    parser.add_argument("--timeframe", default="1h")
    parser.add_argument("--start-at", required=True)
    parser.add_argument("--end-at", required=True)
    args = parser.parse_args()

    session = get_session_factory()()
    try:
        result = DataRepository(session).check_gaps(
            symbol=args.symbol,
            timeframe=args.timeframe,
            start_at=_parse_dt(args.start_at),
            end_at=_parse_dt(args.end_at),
        )
    finally:
        session.close()
    print(json.dumps(result, default=str, ensure_ascii=False, indent=2))
    return 1 if result.get("has_gaps") else 0


if __name__ == "__main__":
    raise SystemExit(main())
