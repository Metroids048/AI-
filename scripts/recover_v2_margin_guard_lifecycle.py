#!/usr/bin/env python3
"""Recover one exact Binance Testnet margin-guard lifecycle.

The command is deliberately explicit and does not submit, cancel, or alter an
exchange order.  It reads the named reduce-only guard fills from Binance, then
projects the missing local V2 facts only when ``--apply`` is supplied.
"""

from __future__ import annotations

import argparse
import json
from decimal import Decimal

from services.automated_trading.application.fact_persistence import recover_confirmed_margin_guard_lifecycle
from services.automated_trading.domain.enums import V2ExecutionMode
from services.automated_trading.infrastructure.binance_adapter import BinanceTestnetAdapter
from services.automated_trading.infrastructure.models import V2ExecutionIntent
from services.database import get_session_factory


def expected_guard_side(direction: str) -> str:
    """Return the only side that can reduce the persisted entry direction."""
    if direction == "long":
        return "sell"
    if direction == "short":
        return "buy"
    raise ValueError(f"margin-guard recovery does not support entry direction {direction!r}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--intent-id", required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--guard-exchange-order-id", required=True)
    parser.add_argument("--guard-client-order-id", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    with get_session_factory()() as session:
        intent = session.get(V2ExecutionIntent, args.intent_id)
        if intent is None or intent.execution_mode != V2ExecutionMode.BINANCE_TESTNET.value:
            raise SystemExit("margin-guard recovery requires an existing BINANCE_TESTNET entry intent")
        expected_side = expected_guard_side(intent.direction)

    adapter = BinanceTestnetAdapter(V2ExecutionMode.BINANCE_TESTNET)
    guard_order = adapter.query_order_by_client_id(args.symbol, args.guard_client_order_id)
    if guard_order is None or guard_order.exchange_order_id != args.guard_exchange_order_id:
        raise SystemExit("guard order identity was not confirmed by Binance Testnet")
    if guard_order.side not in {"buy", "sell"}:
        raise SystemExit("guard order side is invalid")
    if guard_order.side != expected_side:
        raise SystemExit("guard order side does not reduce the persisted entry direction")
    if not guard_order.reduce_only:
        raise SystemExit("guard order is not confirmed reduce-only by Binance Testnet")
    if guard_order.status not in {"closed", "filled"}:
        raise SystemExit("guard order is not terminally filled at Binance Testnet")
    fills = adapter.fetch_fills(args.symbol, args.guard_exchange_order_id)
    total_quantity = sum((fill.filled_quantity for fill in fills), Decimal("0"))
    payload = {
        "execution_mode": V2ExecutionMode.BINANCE_TESTNET.value,
        "intent_id": args.intent_id,
        "guard_exchange_order_id": args.guard_exchange_order_id,
        "guard_client_order_id": args.guard_client_order_id,
        "guard_status": guard_order.status,
        "guard_client_identity_confirmed": True,
        "guard_reduce_only_confirmed": True,
        "guard_side": guard_order.side,
        "fill_count": len(fills),
        "filled_quantity": str(total_quantity),
        "applied": bool(args.apply),
    }
    if not args.apply:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    position_id = recover_confirmed_margin_guard_lifecycle(
        intent_id=args.intent_id,
        guard_exchange_order_id=args.guard_exchange_order_id,
        guard_client_order_id=args.guard_client_order_id,
        guard_fills=fills,
    )
    print(json.dumps({**payload, "position_id": position_id}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
