"""Stable local Uvicorn entrypoint for Windows desktop launches."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

import uvicorn


def selector_loop_factory(*, use_subprocess: bool = False) -> asyncio.AbstractEventLoop:
    del use_subprocess
    return asyncio.SelectorEventLoop()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--log-level", default="warning")
    parser.add_argument("--local-console", action="store_true")
    args = parser.parse_args()

    if args.local_console:
        os.environ["PAPER_CONSOLE_API_ONLY"] = "true"
        os.environ["RUNTIME_SCHEDULER_AUTOSTART"] = "false"
        os.environ["BINANCE_LIVE_WS_ENABLED"] = "false"

    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    uvicorn.run(
        "apps.api.main:app",
        host=args.host,
        port=args.port,
        access_log=False,
        log_level=args.log_level,
        loop="apps.api.local_server:selector_loop_factory",
    )


if __name__ == "__main__":
    main()
