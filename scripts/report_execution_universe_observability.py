"""Report execution-universe data, exchange rules, and rejection reasons.

The report is deliberately read-only.  It uses the exact active execution
symbols, the same 1h/61-bar correlation input as the Gatekeeper, persisted
decision snapshots, and Binance Testnet market metadata.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

REJECTION_REASONS = (
    "technical_signals_insufficient",
    "portfolio_correlation_unavailable",
    "correlated_exposure_limit_exceeded",
    "high_correlation_peer_count",
)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _bar_report(bars: list[Any], *, now: datetime) -> dict[str, Any]:
    timestamps = [bar.timestamp for bar in bars]
    gaps = [
        (current - previous).total_seconds() / 3600.0
        for previous, current in zip(timestamps, timestamps[1:], strict=False)
    ]
    latest = _aware(timestamps[-1]) if timestamps else None
    return {
        "count": len(bars),
        "minimum_required": 61,
        "meets_minimum": len(bars) >= 61,
        "earliest": timestamps[0].isoformat() if timestamps else None,
        "latest": timestamps[-1].isoformat() if timestamps else None,
        "stale_hours": round((now - latest).total_seconds() / 3600.0, 3) if latest else None,
        "max_gap_hours": round(max(gaps, default=0.0), 3),
        "has_abnormal_gap": max(gaps, default=0.0) > 1.5,
    }


def _rejection_report(symbol: str, execution_repo: Any, snapshot_repo: Any, since: datetime) -> dict[str, Any]:
    counts = dict.fromkeys(REJECTION_REASONS, 0)
    total_samples = 0
    for order in execution_repo.list_orders():
        created_at = _aware(order.created_at)
        if order.symbol != symbol or created_at is None or created_at < since:
            continue
        total_samples += 1
        for code in order.rejection_codes:
            if code in counts:
                counts[code] += 1
    try:
        snapshots = snapshot_repo.list_snapshots(symbol=symbol, since=since)
    except Exception as exc:  # noqa: BLE001 - old databases may lack migration 0008
        if "decision_snapshots" in str(exc):
            snapshots = []
        else:
            raise
    total_samples += len(snapshots)
    for snapshot in snapshots:
        if snapshot.pipeline_status in counts:
            counts[snapshot.pipeline_status] += 1
    return {
        "lookback_since": since.isoformat(),
        "total_samples": total_samples,
        "counts": counts,
        "rates": {
            reason: round(value / total_samples, 6) if total_samples else 0.0
            for reason, value in counts.items()
        },
    }


def _exchange_report(symbols: list[str]) -> dict[str, Any]:
    from services.automated_trading.domain.enums import V2ExecutionMode
    from services.automated_trading.infrastructure.binance_adapter import BinanceTestnetAdapter

    adapter = BinanceTestnetAdapter(execution_mode=V2ExecutionMode.BINANCE_TESTNET)
    client = adapter._ensure_gateway()  # noqa: SLF001 - read-only observability probe
    markets = client.load_markets()
    result: dict[str, Any] = {"symbols": {}, "account": {}}
    for symbol in symbols:
        market = markets.get(symbol) or markets.get(f"{symbol}:USDT")
        item: dict[str, Any] = {"requested_symbol": symbol, "market_found": market is not None}
        if market is not None:
            item.update(
                {
                    "exchange_symbol": market.get("id"),
                    "active": market.get("active"),
                    "contract": market.get("contract"),
                    "swap": market.get("swap"),
                    "linear": market.get("linear"),
                    "precision": market.get("precision"),
                    "min_notional": ((market.get("limits") or {}).get("cost") or {}).get("min"),
                }
            )
        result["symbols"][symbol] = item
    snapshot = adapter.fetch_authoritative_snapshot()
    result["account"] = {
        "observed_at": snapshot.snapshot_timestamp.isoformat(),
        "positions": [
            {
                "symbol": position.symbol,
                "direction": position.direction,
                "quantity": str(position.quantity),
                "entry_price": str(position.entry_price),
            }
            for position in snapshot.positions
        ],
        "pending_orders": [
            {
                "symbol": order.symbol,
                "exchange_order_id": str(order.exchange_order_id),
                "client_order_id": order.client_order_id,
                "status": order.status,
            }
            for order in snapshot.pending_orders
        ],
    }
    return result


def build_report(*, database_url: str, lookback_days: int, symbols_override: list[str] | None = None) -> dict[str, Any]:
    os.environ["POSTGRES_URL"] = database_url
    from services.data import DataRepository
    from services.data.universe import AUTO_SIMULATION_EXECUTION_SYMBOLS, execution_scope_hash
    from services.database import get_session_factory
    from services.strategy_library import DecisionSnapshotRepository, ExecutionRepository

    symbols = symbols_override or list(AUTO_SIMULATION_EXECUTION_SYMBOLS)
    now = datetime.now(UTC)
    since = now - timedelta(days=lookback_days)
    with get_session_factory()() as session:
        data_repo = DataRepository(session)
        execution_repo = ExecutionRepository(session)
        snapshot_repo = DecisionSnapshotRepository(session)
        per_symbol = {}
        for symbol in symbols:
            bars = data_repo.list_ohlcv_bars(symbol=symbol, timeframe="1h", limit=61)
            per_symbol[symbol] = {
                "ohlcv_1h": _bar_report(bars, now=now),
                "rejections": _rejection_report(symbol, execution_repo, snapshot_repo, since),
            }
    try:
        exchange = _exchange_report(symbols)
    except Exception as exc:  # noqa: BLE001 - preserve a useful local report on network failure
        exchange = {"error": f"{type(exc).__name__}: {exc}"}
    return {
        "generated_at": now.isoformat(),
        "database_url": database_url,
        "execution_symbols": symbols,
        "execution_scope_hash": execution_scope_hash(symbols),
        "symbols": per_symbol,
        "exchange_testnet": exchange,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default="sqlite:///.local_paper_console.db")
    parser.add_argument("--lookback-days", type=int, default=7)
    parser.add_argument("--symbols", default=None, help="comma-separated symbols; defaults to active execution scope")
    parser.add_argument("--output", default=None)
    args = parser.parse_args()
    symbols_override = [item.strip() for item in args.symbols.split(",") if item.strip()] if args.symbols else None
    report = build_report(
        database_url=args.database_url,
        lookback_days=args.lookback_days,
        symbols_override=symbols_override,
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2, default=str)
    print(rendered)
    if args.output:
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rendered + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
