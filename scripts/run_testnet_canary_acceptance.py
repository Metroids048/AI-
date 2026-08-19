"""Explicitly run one non-promotable Binance Testnet execution-continuity cycle.

This command is intentionally separate from the one-click RuntimeScheduler.
It can exercise the existing V2 chain only when an operator deliberately
supplies the confirmation flag; it never changes the Production Manifest or
counts as Strategy acceptance.
"""

from __future__ import annotations

import argparse
import json

from services.data.universe import AUTO_SIMULATION_EXECUTION_SYMBOLS
from services.execution.v2_scheduler_entry import execute_explicit_testnet_canary_acceptance


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--confirm-testnet-canary",
        action="store_true",
        help="required acknowledgement that this is a non-promotable Testnet execution check",
    )
    parser.add_argument("--symbol", choices=AUTO_SIMULATION_EXECUTION_SYMBOLS, action="append")
    args = parser.parse_args()
    if not args.confirm_testnet_canary:
        parser.error("--confirm-testnet-canary is required; standard runtime must not invoke the Canary")

    result = execute_explicit_testnet_canary_acceptance(
        symbols=args.symbol or list(AUTO_SIMULATION_EXECUTION_SYMBOLS),
    )
    print(json.dumps(result, default=str, ensure_ascii=True))
    return 0 if result.get("status") not in {"error", "skipped"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
