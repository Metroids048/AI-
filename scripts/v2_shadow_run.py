#!/usr/bin/env python3
"""Shadow run script (plan section 15).

Runs the V2 cycle service in SHADOW activation so the full decision funnel
executes against real market data and a real read-only Binance snapshot, but
``submit_market_order`` / ``submit_protection`` are never called.

This is the pre-activation observation tool: it answers "what would V2 have
done?" without placing a single order.

Usage:
    python scripts/v2_shadow_run.py --cycles 3 --symbols BTC/USDT,ETH/USDT

Exit codes:
    0 = all cycles ran, no errors, no submission attempted
    1 = a cycle raised, or a submission was attempted (contract violation)
    2 = cycles ran but reconciliation was never HEALTHY
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from decimal import Decimal

from services.automated_trading.application.cycle_service import (
    CycleRequest,
    run_automated_trading_cycle,
)
from services.automated_trading.application.decision_service import BarView, TimeframeView
from services.automated_trading.domain.enums import V2ExecutionMode
from services.automated_trading.infrastructure.runtime_lock import EngineActivation


class SubmissionGuardAdapter:
    """Wraps a real adapter and hard-fails if a write method is reached.

    Shadow mode is a claim about behaviour, not an intention. This wrapper turns
    that claim into an enforced invariant: any submit attempt raises instead of
    reaching the network, and the counter is reported at the end of the run.
    """

    def __init__(self, inner) -> None:
        self._inner = inner
        self.submit_attempts = 0

    # --- read paths pass through ---
    def fetch_authoritative_snapshot(self, *a, **kw):
        return self._inner.fetch_authoritative_snapshot(*a, **kw)

    def fetch_market_snapshot(self, *a, **kw):
        return self._inner.fetch_market_snapshot(*a, **kw)

    def fetch_fills(self, *a, **kw):
        return self._inner.fetch_fills(*a, **kw)

    def query_order_by_client_id(self, *a, **kw):
        return self._inner.query_order_by_client_id(*a, **kw)

    # --- write paths are forbidden in shadow ---
    def submit_market_order(self, *a, **kw):
        self.submit_attempts += 1
        raise AssertionError("SHADOW contract violation: submit_market_order was called")

    def submit_protection(self, *a, **kw):
        self.submit_attempts += 1
        raise AssertionError("SHADOW contract violation: submit_protection was called")

    def submit_reduce_only_exit(self, *a, **kw):
        self.submit_attempts += 1
        raise AssertionError("SHADOW contract violation: submit_reduce_only_exit was called")

    def cancel_order(self, *a, **kw):
        self.submit_attempts += 1
        raise AssertionError("SHADOW contract violation: cancel_order was called")


def _build_adapter():
    """Construct the real Binance Testnet adapter for read-only use."""
    from services.automated_trading.infrastructure.binance_adapter import BinanceTestnetAdapter

    return BinanceTestnetAdapter()


def _load_timeframe(symbol: str, timeframe: str, limit: int = 200) -> TimeframeView:
    """Load closed bars for *symbol* from the local OHLCV store."""
    from services.data.repository import fetch_recent_ohlcv

    rows = fetch_recent_ohlcv(symbol=symbol, timeframe=timeframe, limit=limit)
    bars = tuple(
        BarView(
            timestamp=row.timestamp,
            open=Decimal(str(row.open)),
            high=Decimal(str(row.high)),
            low=Decimal(str(row.low)),
            close=Decimal(str(row.close)),
            volume=Decimal(str(row.volume)),
        )
        for row in rows
    )
    return TimeframeView(timeframe=timeframe, bars=bars)


def shadow_run(symbols: list[str], cycles: int, timeframe: str = "15m") -> int:
    """Run *cycles* shadow cycles over *symbols*.

    Returns an exit code (0 ok, 1 error/violation, 2 never healthy).
    """
    adapter = SubmissionGuardAdapter(_build_adapter())

    print(f"[{datetime.now(UTC).isoformat()}] V2 shadow run starting")
    print(f"  symbols   : {symbols}")
    print(f"  cycles    : {cycles}")
    print(f"  timeframe : {timeframe}")
    print("  activation: SHADOW (submission guarded)\n")

    errors: list[str] = []
    saw_healthy = False

    for index in range(1, cycles + 1):
        for symbol in symbols:
            cycle_id = f"shadow-{index}-{symbol.replace('/', '').lower()}"
            print(f"[cycle {index}/{cycles}] {symbol}")

            try:
                entry_timeframe = _load_timeframe(symbol, timeframe)
            except Exception as exc:  # noqa: BLE001
                print(f"  ! market data unavailable: {exc}")
                errors.append(f"{symbol}: market data unavailable: {exc}")
                continue

            request = CycleRequest(
                cycle_id=cycle_id,
                symbol=symbol,
                timeframe=timeframe,
                entry_timeframe=entry_timeframe,
                execution_mode=V2ExecutionMode.BINANCE_TESTNET,
                engine_activation=EngineActivation.SHADOW,
                fencing_token=f"shadow@{cycle_id}",
                now=datetime.now(UTC),
            )

            try:
                result = run_automated_trading_cycle(request, adapter)
            except Exception as exc:  # noqa: BLE001
                print(f"  ! cycle raised: {exc}")
                errors.append(f"{symbol}: cycle raised: {exc}")
                continue

            status = result.reconciliation_status.value
            if status == "HEALTHY":
                saw_healthy = True

            funnel = result.funnel_payload or {}
            print(f"  reconciliation : {status}")
            print(f"  terminal stage : {funnel.get('terminal_stage', '—')}")
            print(f"  reason code    : {funnel.get('reason_code', '—')}")
            print(f"  entry submitted: {result.entry_submitted}")
            for err in result.errors:
                print(f"  error: {err}")
                errors.append(f"{symbol}: {err}")
            print()

    print(f"[{datetime.now(UTC).isoformat()}] shadow run complete")
    print(f"  submit attempts : {adapter.submit_attempts}")
    print(f"  errors          : {len(errors)}")

    if adapter.submit_attempts:
        print("\nFAIL: shadow mode attempted a submission")
        return 1
    if errors:
        print("\nFAIL: cycles reported errors")
        for err in errors:
            print(f"  - {err}")
        return 1
    if not saw_healthy:
        print("\nWARN: reconciliation was never HEALTHY")
        return 2

    print("\nOK: shadow run clean, zero submissions")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="V2 shadow run (no submission)")
    parser.add_argument("--symbols", default="BTC/USDT,ETH/USDT")
    parser.add_argument("--cycles", type=int, default=3)
    parser.add_argument("--timeframe", default="15m")
    args = parser.parse_args()

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    return shadow_run(symbols=symbols, cycles=args.cycles, timeframe=args.timeframe)


if __name__ == "__main__":
    sys.exit(main())
