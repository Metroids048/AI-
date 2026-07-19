"""Backfill the Top10 offline technical-research universe without arming execution."""

from __future__ import annotations

import argparse
import os
from datetime import UTC, datetime, timedelta

from services.data import DataRepository
from services.data.binance import BinanceCcxtClient
from services.data.universe import TECHNICAL_RESEARCH_SYMBOLS
from services.database import get_session_factory


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--days", type=int, default=365)
    args = parser.parse_args()
    os.environ["POSTGRES_URL"] = args.database_url
    end_at = datetime.now(UTC)
    start_at = end_at - timedelta(days=args.days)
    client = BinanceCcxtClient()
    written = 0
    with get_session_factory()() as session:
        repository = DataRepository(session)
        for symbol in TECHNICAL_RESEARCH_SYMBOLS:
            for timeframe in ("15m", "1h", "4h"):
                bars = client.fetch_ohlcv_history(
                    symbol=f"{symbol}:USDT",
                    timeframe=timeframe,
                    start_at=start_at,
                    end_at=end_at,
                )
                written += repository.store_ohlcv_bars([bar.model_copy(update={"symbol": symbol}) for bar in bars])
            session.commit()
    print(f"stored={written} symbols={len(TECHNICAL_RESEARCH_SYMBOLS)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
