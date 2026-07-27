#!/usr/bin/env python3
"""Capture a read-only Testnet/local/runtime truth bundle without secrets."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from services.execution.gateway import BinanceUsdtPerpetualGateway
from services.execution.runtime_state import load_external_scheduler_state
from shared.config import settings

ROOT = Path(__file__).resolve().parents[1]


def _rows(connection: sqlite3.Connection, query: str, limit: int = 200) -> list[dict[str, Any]]:
    try:
        cursor = connection.execute(f"{query} LIMIT ?", (limit,))
    except sqlite3.Error as exc:
        return [{"status": "unavailable", "error": str(exc)}]
    columns = [item[0] for item in cursor.description or []]
    return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


def _local_evidence(database: Path) -> dict[str, Any]:
    if not database.is_file():
        return {"status": "unavailable", "error": "database_not_found"}
    connection = sqlite3.connect(database)
    try:
        return {
            "status": "available",
            "orders": _rows(
                connection,
                "SELECT order_execution_id, symbol, execution_status, gateway_name, "
                "gateway_order_id, gateway_status, created_at FROM order_executions "
                "ORDER BY created_at DESC",
            ),
            "exchange_orders": _rows(
                connection,
                "SELECT exchange_order_record_id, client_order_id, exchange_order_id, symbol, "
                "state, reduce_only, created_at FROM exchange_orders ORDER BY created_at DESC",
            ),
            "fill_receipts": _rows(
                connection,
                "SELECT receipt_id, exchange_order_id, client_order_id, trade_ids, symbol, "
                "cumulative_filled_quantity, average_fill_price, commissions, event_time "
                "FROM exchange_fill_receipts ORDER BY event_time DESC",
            ),
            "positions": _rows(
                connection,
                "SELECT position_record_id, symbol, position_side, quantity, management_status, "
                "execution_mode, entry_fill_receipt_id, position_group_id, updated_at "
                "FROM position_records ORDER BY updated_at DESC",
            ),
            "protections": _rows(
                connection,
                "SELECT protection_record_id, position_record_id, status, stop_exchange_order_id, "
                "take_profit_exchange_order_id, updated_at FROM protection_records ORDER BY updated_at DESC",
            ),
            "decisions": _rows(
                connection,
                "SELECT event_id, paper_run_id, cycle_id, decision_id, event_type, symbol, "
                "timeframe, candle_close_time, reason_code, created_at "
                "FROM decision_events ORDER BY created_at DESC",
            ),
            "llm_invocations": _rows(
                connection,
                "SELECT invocation_id, cycle_id, called, skip_reason, provider, model, status, "
                "total_tokens, created_at FROM llm_invocations ORDER BY created_at DESC",
            ),
        }
    finally:
        connection.close()


def capture(*, database: Path) -> tuple[dict[str, Any], bool]:
    observed_at = datetime.now(UTC)
    scheduler = load_external_scheduler_state(now=observed_at)
    exchange: dict[str, Any]
    exchange_available = False
    try:
        if not settings.binance_use_testnet or settings.live_trading_enabled:
            raise RuntimeError("Testnet safety boundary is not active")
        gateway = BinanceUsdtPerpetualGateway()
        snapshot = gateway.reconcile(live_run_id=f"truth-bundle:{observed_at.isoformat()}")
        balance = gateway.client.fetch_balance()
        recent_orders: list[dict[str, Any]] = []
        recent_trades: list[dict[str, Any]] = []
        get_orders = getattr(gateway.client, "fapiPrivateGetAllOrders", None)
        get_trades = getattr(gateway.client, "fapiPrivateGetUserTrades", None)
        for market_id in ("BTCUSDT", "ETHUSDT"):
            if callable(get_orders):
                for item in (get_orders({"symbol": market_id, "limit": 20}) or [])[-20:]:
                    recent_orders.append(
                        {
                            "order_id": str(item.get("orderId") or ""),
                            "client_order_id": str(item.get("clientOrderId") or ""),
                            "symbol": market_id,
                            "side": item.get("side"),
                            "type": item.get("type"),
                            "status": item.get("status"),
                            "quantity": item.get("origQty"),
                            "executed_quantity": item.get("executedQty"),
                            "average_price": item.get("avgPrice"),
                            "reduce_only": item.get("reduceOnly"),
                            "update_time": item.get("updateTime"),
                        }
                    )
            if callable(get_trades):
                for item in (get_trades({"symbol": market_id, "limit": 20}) or [])[-20:]:
                    recent_trades.append(
                        {
                            "trade_id": str(item.get("id") or ""),
                            "order_id": str(item.get("orderId") or ""),
                            "symbol": market_id,
                            "side": item.get("side"),
                            "quantity": item.get("qty"),
                            "price": item.get("price"),
                            "commission": item.get("commission"),
                            "commission_asset": item.get("commissionAsset"),
                            "realized_pnl": item.get("realizedPnl"),
                            "time": item.get("time"),
                        }
                    )
        exchange = {
            "status": "available",
            "source": "BINANCE_USDT_M_TESTNET",
            "wallet": {
                "USDT": {
                    "total": (balance.get("USDT") or {}).get("total"),
                    "free": (balance.get("USDT") or {}).get("free"),
                }
            },
            "positions": snapshot.get("open_positions"),
            "open_orders": snapshot.get("open_orders"),
            "recent_orders": recent_orders,
            "recent_trades": recent_trades,
            "reconciliation_status": snapshot.get("reconciliation_status"),
            "notes": snapshot.get("notes"),
        }
        exchange_available = "open_positions" in snapshot
    except Exception as exc:  # noqa: BLE001 - evidence must preserve an unavailable state
        exchange = {
            "status": "unavailable",
            "source": "BINANCE_USDT_M_TESTNET",
            "error": str(exc),
        }
    return (
        {
            "schema_version": "testnet-truth-bundle/v1",
            "observed_at": observed_at.isoformat(),
            "safety": {
                "binance_use_testnet": settings.binance_use_testnet,
                "live_trading_enabled": settings.live_trading_enabled,
            },
            "scheduler": {
                **scheduler.__dict__,
                "heartbeat_at": scheduler.heartbeat_at.isoformat() if scheduler.heartbeat_at else None,
                "last_auto_cycle_at": (
                    scheduler.last_auto_cycle_at.isoformat() if scheduler.last_auto_cycle_at else None
                ),
            },
            "exchange": exchange,
            "local": _local_evidence(database),
        },
        exchange_available,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=ROOT / ".local_paper_console.db")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload, exchange_available = capture(database=args.database)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(args.output.resolve())
    return 0 if exchange_available else 2


if __name__ == "__main__":
    sys.exit(main())
