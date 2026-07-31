"""Run local Paper automation outside the desktop API process."""

from __future__ import annotations

import argparse
import asyncio
import os


def configure_scheduler_environment(database_url: str) -> None:
    """Configure the isolated scheduler as the V2 Testnet writer."""
    os.environ["POSTGRES_URL"] = database_url
    os.environ["APP_ENV"] = "development"
    os.environ["AUTOMATED_TRADING_ENGINE"] = "v2_active"
    os.environ["BINANCE_USE_TESTNET"] = "true"
    os.environ["LIVE_TRADING_ENABLED"] = "false"
    if not os.environ.get("BINANCE_HTTPS_PROXY") and not os.environ.get("BINANCE_HTTP_PROXY"):
        os.environ["BINANCE_LIVE_WS_ENABLED"] = "false"


async def run_scheduler(database_url: str) -> None:
    configure_scheduler_environment(database_url)

    from services.execution.bootstrap import bootstrap_local_paper_runtime
    from services.execution.scheduler import RuntimeScheduler

    bootstrap_local_paper_runtime(seed_ohlcv=False)
    scheduler = RuntimeScheduler()
    scheduler.start()
    scheduler._publish_external_state()
    try:
        await asyncio.Event().wait()
    finally:
        await scheduler.stop()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", required=True)
    args = parser.parse_args()
    asyncio.run(run_scheduler(args.database_url))


if __name__ == "__main__":
    main()
