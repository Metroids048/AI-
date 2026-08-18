"""Retire terminal Testnet protection projections after an authoritative read.

The command is bounded and local-only: it never submits, cancels, or modifies
an exchange order. It only applies the existing reconciliation transition when
the exchange snapshot proves the protection order IDs are not open.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", required=True)
    args = parser.parse_args()
    os.environ["POSTGRES_URL"] = args.database_url
    os.environ["BINANCE_USE_TESTNET"] = "true"
    os.environ["LIVE_TRADING_ENABLED"] = "false"

    from services.automated_trading.application.fact_persistence import reconcile_closed_position_protections
    from services.automated_trading.domain.enums import V2ExecutionMode
    from services.automated_trading.infrastructure.binance_adapter import BinanceTestnetAdapter
    from services.data.universe import AUTO_SIMULATION_EXECUTION_SYMBOLS

    adapter = BinanceTestnetAdapter(execution_mode=V2ExecutionMode.BINANCE_TESTNET)
    snapshot = adapter.fetch_authoritative_snapshot()
    open_order_ids = frozenset(str(order.exchange_order_id) for order in snapshot.pending_orders)
    observed_at = snapshot.snapshot_timestamp or datetime.now(UTC)
    cleaned: dict[str, int] = {}
    for symbol in AUTO_SIMULATION_EXECUTION_SYMBOLS:
        changed = reconcile_closed_position_protections(
            execution_mode=V2ExecutionMode.BINANCE_TESTNET,
            symbol=symbol,
            exchange_open_order_ids=open_order_ids,
            observed_at=observed_at,
        )
        if changed:
            cleaned[symbol] = changed

    print(
        json.dumps(
            {
                "execution_mode": "BINANCE_TESTNET",
                "observed_at": observed_at.isoformat(),
                "exchange_position_count": len(snapshot.positions),
                "exchange_pending_order_count": len(snapshot.pending_orders),
                "retired_terminal_protections": cleaned,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
