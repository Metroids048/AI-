"""Run local Paper automation outside the desktop API process."""

from __future__ import annotations

import argparse
import asyncio
import os


def configure_scheduler_environment(database_url: str, engine: str = "v2_shadow") -> None:
    """Configure the isolated scheduler with an explicit V2 engine mode."""
    if engine not in {"v2_shadow", "v2_active"}:
        raise ValueError("engine must be v2_shadow or v2_active")
    os.environ["POSTGRES_URL"] = database_url
    os.environ["APP_ENV"] = "development"
    # Keep the safe default visible in source and only opt into active mode
    # when the caller explicitly requests it.
    os.environ["AUTOMATED_TRADING_ENGINE"] = "v2_shadow"
    if engine == "v2_active":
        os.environ["AUTOMATED_TRADING_ENGINE"] = engine
    os.environ["BINANCE_USE_TESTNET"] = "true"
    os.environ["LIVE_TRADING_ENABLED"] = "false"
    if not os.environ.get("BINANCE_HTTPS_PROXY") and not os.environ.get("BINANCE_HTTP_PROXY"):
        os.environ["BINANCE_LIVE_WS_ENABLED"] = "false"


async def run_scheduler(database_url: str, engine: str = "v2_shadow") -> None:
    configure_scheduler_environment(database_url, engine)

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
    parser.add_argument("--engine", choices=("v2_shadow", "v2_active"), default="v2_shadow")
    args = parser.parse_args()
    asyncio.run(run_scheduler(args.database_url, args.engine))


if __name__ == "__main__":
    main()
