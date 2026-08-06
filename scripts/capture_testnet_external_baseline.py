"""Capture the current BTC/ETH Binance Testnet position baseline read-only."""

from __future__ import annotations

import argparse
import json
from decimal import Decimal


def capture_baseline() -> dict[str, str]:
    from shared.config import settings

    if not settings.binance_use_testnet or settings.live_trading_enabled:
        raise RuntimeError("external baseline capture is Testnet-only")

    from sqlalchemy import select

    from services.automated_trading.domain.enums import V2ExecutionMode
    from services.automated_trading.infrastructure.binance_adapter import BinanceTestnetAdapter
    from services.automated_trading.infrastructure.models import V2ManagedPosition
    from services.database import get_session_factory

    snapshot = BinanceTestnetAdapter(execution_mode=V2ExecutionMode.BINANCE_TESTNET).fetch_authoritative_snapshot()
    with get_session_factory()() as session:
        managed = {
            f"{position.symbol}:{position.direction}": Decimal(str(position.quantity))
            for position in session.scalars(
                select(V2ManagedPosition).where(
                    V2ManagedPosition.execution_mode == V2ExecutionMode.BINANCE_TESTNET.value,
                    V2ManagedPosition.state.not_in(("CLOSED", "QUARANTINED")),
                )
            )
        }
    baseline: dict[str, str] = {}
    for position in snapshot.positions:
        if position.symbol not in {"BTC/USDT", "ETH/USDT"}:
            continue
        quantity = Decimal(str(position.quantity))
        key = f"{position.symbol}:{position.direction}"
        external_quantity = quantity - managed.get(key, Decimal("0"))
        if external_quantity > 0:
            baseline[key] = str(external_quantity)
    return baseline


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()
    baseline = capture_baseline()
    if args.as_json:
        print(json.dumps(baseline, separators=(",", ":")))
    else:
        print(json.dumps(baseline, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
