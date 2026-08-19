"""Independent research worker; it has no exchange credentials or writer role."""

from __future__ import annotations

import argparse
import os
import time


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval-seconds", type=float, default=15.0)
    args = parser.parse_args()
    os.environ["POSTGRES_URL"] = args.database_url
    os.environ["RESEARCH_ONLY_MODE"] = "true"
    for key in tuple(os.environ):
        if any(token in key.upper() for token in ("BINANCE", "API_KEY", "API_SECRET", "SECRET_KEY")):
            os.environ.pop(key, None)
    from services.database import get_session_factory
    from services.research.integrations.orchestrator import ResearchOrchestrator

    while True:
        with get_session_factory(args.database_url)() as session:
            ResearchOrchestrator().process_queued(session=session)
        if args.once:
            return
        time.sleep(args.interval_seconds)


if __name__ == "__main__":
    main()
