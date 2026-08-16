from __future__ import annotations

import argparse
import logging
import os

from services.database import create_local_runtime_schema, get_session_factory
from services.microstructure.collector import MicrostructureCollector
from services.microstructure.retention import apply_retention


def main() -> int:
    parser = argparse.ArgumentParser(description="Independent Binance Testnet order-book collector")
    parser.add_argument("--database-url", default=os.getenv("POSTGRES_URL", "sqlite:///.local_paper_console.db"))
    parser.add_argument("--interval-seconds", type=float, default=1.0)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    create_local_runtime_schema(args.database_url)
    session = get_session_factory(args.database_url)()
    try:
        apply_retention(session)
    finally:
        session.close()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    collector = MicrostructureCollector(args.database_url)
    if args.once:
        import json

        print(
            json.dumps([collector.collect_once(symbol) for symbol in collector.symbols], ensure_ascii=False, indent=2)
        )
    else:
        collector.run_forever(args.interval_seconds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
