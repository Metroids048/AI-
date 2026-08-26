"""Read-only P0 throughput audit for the active V2 Testnet Canary lane."""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from shared.models.risk import TESTNET_CANARY_RUNTIME_CONTRACT, TESTNET_CANARY_SYMBOLS

DB = ".local_paper_console.db"
OUTPUT = Path("artifacts/runtime_throughput_recovery/LAST_24H_FUNNEL.json")


def _dt(value: Any) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=parsed.tzinfo or UTC).astimezone(UTC)


def _json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _table_exists(con: sqlite3.Connection, table: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _rows(con: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
    return list(con.execute(sql, params))


def _count_by_symbol(rows: list[sqlite3.Row], symbol_key: str = "symbol") -> dict[str, int]:
    counts = Counter(str(row[symbol_key]) for row in rows if row[symbol_key])
    return {symbol: counts.get(symbol, 0) for symbol in TESTNET_CANARY_SYMBOLS}


def _occurred_in_window(row: sqlite3.Row, field: str, since: datetime) -> bool:
    occurred = _dt(row[field])
    return occurred is not None and occurred >= since


def _best_effort_exchange_snapshot() -> dict[str, Any]:
    try:
        from services.automated_trading.domain.enums import V2ExecutionMode
        from services.automated_trading.infrastructure.binance_adapter import BinanceTestnetAdapter

        snapshot = BinanceTestnetAdapter(execution_mode=V2ExecutionMode.BINANCE_TESTNET).fetch_authoritative_snapshot()
        return {
            "available": True,
            "positions": [
                {
                    "symbol": position.symbol,
                    "direction": position.direction,
                    "quantity": str(position.quantity),
                    "entry_price": str(position.entry_price),
                    "mark_price": str(position.mark_price),
                    "unrealized_pnl": str(position.unrealized_pnl),
                }
                for position in snapshot.positions
            ],
            "open_orders": [
                {
                    "symbol": order.symbol,
                    "exchange_order_id": str(order.exchange_order_id),
                    "client_order_id": order.client_order_id,
                    "reduce_only": order.reduce_only,
                    "status": order.status,
                }
                for order in snapshot.pending_orders
            ],
        }
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "error": f"{type(exc).__name__}: {exc}"}


def collect(*, db_path: str = DB, hours: int = 24) -> dict[str, Any]:
    now = datetime.now(UTC)
    since = now - timedelta(hours=hours)
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        cycle_rows = _rows(con, "SELECT * FROM v2_execution_cycles WHERE started_at >= ?", (since.replace(tzinfo=None).isoformat(sep=" "),))
        decision_rows = _rows(con, "SELECT * FROM v2_execution_decisions WHERE created_at >= ?", (since.replace(tzinfo=None).isoformat(sep=" "),))
        intent_rows = _rows(con, "SELECT * FROM v2_execution_intents WHERE created_at >= ?", (since.replace(tzinfo=None).isoformat(sep=" "),))
        order_rows = _rows(con, "SELECT * FROM v2_exchange_orders WHERE created_at >= ?", (since.replace(tzinfo=None).isoformat(sep=" "),))
        fill_rows = _rows(con, "SELECT * FROM v2_exchange_fills WHERE received_at >= ?", (since.replace(tzinfo=None).isoformat(sep=" "),))
        position_rows = _rows(con, "SELECT * FROM v2_managed_positions")
        protection_rows = _rows(con, "SELECT * FROM v2_protection_records")
        runtime_controls = _rows(con, "SELECT * FROM v2_runtime_controls") if _table_exists(con, "v2_runtime_controls") else []
    finally:
        con.close()

    decisions: list[dict[str, Any]] = []
    reason_counts: Counter[str] = Counter()
    per_symbol_reasons: dict[str, Counter[str]] = defaultdict(Counter)
    candidate_decisions: list[dict[str, Any]] = []
    latest_sizing: dict[str, Any] | None = None
    latest_payload: dict[str, Any] | None = None
    latest_sizing_row: tuple[str, dict[str, Any]] | None = None
    for row in decision_rows:
        payload = _json(row["payload"])
        symbol = str(payload.get("symbol") or "")
        reason = str(payload.get("reason_code") or row["terminal_reason"] or "UNKNOWN")
        record = {"row": row, "payload": payload, "symbol": symbol, "reason": reason}
        decisions.append(record)
        reason_counts[reason] += 1
        per_symbol_reasons[symbol][reason] += 1
        if isinstance(payload.get("candidate"), dict) or row["candidate_key"]:
            candidate_decisions.append(record)
        if latest_payload is None or str(row["created_at"]) > str(latest_payload.get("_created_at", "")):
            payload["_created_at"] = str(row["created_at"])
            latest_payload = payload
        if isinstance(payload.get("sizing_contract"), dict):
            created_at = str(row["created_at"] or "")
            if latest_sizing_row is None or created_at > latest_sizing_row[0]:
                latest_sizing_row = (created_at, payload["sizing_contract"])
    if latest_sizing_row is not None:
        latest_sizing = latest_sizing_row[1]

    by_symbol: dict[str, dict[str, Any]] = {}
    for symbol in TESTNET_CANARY_SYMBOLS:
        symbol_decisions = [item for item in decisions if item["symbol"] == symbol]
        symbol_candidates = [item for item in candidate_decisions if item["symbol"] == symbol]
        by_symbol[symbol] = {
            "closed_bar_evaluations": sum(1 for row in cycle_rows if row["symbol"] == symbol),
            "strategy_evaluations": sum(1 for item in symbol_decisions if item["reason"] != "DUPLICATE_DECISION"),
            "signals": sum(1 for item in symbol_candidates),
            "candidates": len(symbol_candidates),
            "intents": sum(1 for row in intent_rows if row["symbol"] == symbol),
            "submitted_orders": sum(1 for row in order_rows if any(str(intent["intent_id"]) == str(row["intent_id"]) for intent in intent_rows if intent["symbol"] == symbol)),
            "fills": sum(1 for row in fill_rows if row["symbol"] == symbol),
            "protected_positions": sum(
                1 for row in position_rows if row["symbol"] == symbol and _occurred_in_window(row, "protected_at", since)
            ),
            "closed_positions": sum(
                1 for row in position_rows if row["symbol"] == symbol and _occurred_in_window(row, "closed_at", since)
            ),
            "rejection_reasons": dict(Counter(item["reason"] for item in symbol_decisions)),
        }

    eth_position = next(
        (
            row
            for row in position_rows
            if row["symbol"] == "ETH/USDT" and _occurred_in_window(row, "projected_at", since)
        ),
        None,
    )
    eth_protection = next((row for row in protection_rows if eth_position is not None and row["position_id"] == eth_position["position_id"]), None)
    exchange = _best_effort_exchange_snapshot()
    exchange_eth = next((row for row in exchange.get("positions", []) if row["symbol"].startswith("ETH/USDT")), None)
    exchange_eth_orders = [row for row in exchange.get("open_orders", []) if row["symbol"].startswith("ETH/USDT")]
    current_price = exchange_eth.get("mark_price") if exchange_eth else None

    eth_opened_at = _dt(eth_position["projected_at"]) if eth_position else None
    eth_closed_at = _dt(eth_position["closed_at"]) if eth_position else None
    post_eth_candidates = [
        item
        for item in candidate_decisions
        if eth_opened_at is not None
        and (_dt(item["row"]["created_at"]) or now) >= eth_opened_at
        and (eth_closed_at is None or (_dt(item["row"]["created_at"]) or now) <= eth_closed_at)
        and item["symbol"] != "ETH/USDT"
    ]
    post_eth_intent_cycles = {
        str(row["cycle_id"])
        for row in intent_rows
        if eth_opened_at is not None
        and (_dt(row["created_at"]) or now) >= eth_opened_at
        and (eth_closed_at is None or (_dt(row["created_at"]) or now) <= eth_closed_at)
        and row["symbol"] != "ETH/USDT"
    }
    post_eth_max_exposure = [item for item in post_eth_candidates if item["reason"] == "MAX_OPEN_EXPOSURES"]

    return {
        "generated_at": now.isoformat(),
        "window": {"hours": hours, "since": since.isoformat(), "until": now.isoformat()},
        "runtime_authority": {
            "execution_mode": (latest_payload or {}).get("execution_mode", "BINANCE_TESTNET"),
            "entry_authority": (latest_payload or {}).get("entry_authority"),
            "strategy_id": (latest_payload or {}).get("strategy_id", "testnet_sampling_v2"),
            "entry_authorized": (latest_payload or {}).get("entry_authorized"),
            "latest_sizing_contract": latest_sizing,
            "contract_reference": {
                "max_open_positions": TESTNET_CANARY_RUNTIME_CONTRACT["max_open_positions"],
                "max_total_exposure": TESTNET_CANARY_RUNTIME_CONTRACT["max_total_exposure"],
                "max_symbol_exposure": TESTNET_CANARY_RUNTIME_CONTRACT["max_symbol_exposure"],
                "max_notional_usdt": TESTNET_CANARY_RUNTIME_CONTRACT["max_notional_usdt"],
            },
            "runtime_controls": [dict(row) for row in runtime_controls],
        },
        "totals": {
            "closed_bar_evaluations": len(cycle_rows),
            "strategy_evaluations": sum(1 for item in decisions if item["reason"] != "DUPLICATE_DECISION"),
            "signals": len(candidate_decisions),
            "candidates": len(candidate_decisions),
            "intents": len(intent_rows),
            "submitted_orders": len(order_rows),
            "fills": len(fill_rows),
            "protected_positions": sum(1 for row in position_rows if _occurred_in_window(row, "protected_at", since)),
            "closed_positions": sum(1 for row in position_rows if _occurred_in_window(row, "closed_at", since)),
        },
        "by_symbol": by_symbol,
        "rejection_reasons": dict(reason_counts),
        "post_eth_position": {
            "eth_position_opened_at": eth_opened_at.isoformat() if eth_opened_at else None,
            "other_symbol_candidates": len(post_eth_candidates),
            "other_symbol_intents": len(post_eth_intent_cycles),
            "max_open_exposures_blocked_candidates": len(post_eth_max_exposure),
            "max_open_exposures_block_rate": round(len(post_eth_max_exposure) / len(post_eth_candidates), 4) if post_eth_candidates else 0.0,
            "blocked_symbols": dict(Counter(item["symbol"] for item in post_eth_max_exposure)),
        },
        "eth_audit": {
            "local_position": dict(eth_position) if eth_position else None,
            "protection": dict(eth_protection) if eth_protection else None,
            "position_age_seconds": round((now - eth_opened_at).total_seconds(), 2) if eth_opened_at else None,
            "current_price_or_mark": current_price,
            "exchange_position": exchange_eth,
            "exchange_open_orders": exchange_eth_orders,
            "exchange_local_position_match": (
                bool(eth_position and exchange_eth and str(eth_position["quantity"]) == str(exchange_eth["quantity"]))
                if eth_position and eth_position["state"] not in {"CLOSED", "QUARANTINED"}
                else bool(eth_position and not exchange_eth)
            ),
            "protection_active": bool(eth_protection and eth_protection["state"] == "PROTECTION_ACTIVE"),
            "exit_condition_observed": bool(eth_protection and eth_protection["state"] == "PROTECTION_FILLED"),
            "status": (
                "HEALTHY"
                if eth_position
                and eth_protection
                and exchange.get("available")
                and (
                    eth_protection["state"] == "PROTECTION_ACTIVE"
                    or (eth_position["state"] == "CLOSED" and eth_protection["state"] == "PROTECTION_FILLED" and not exchange_eth)
                )
                else "UNVERIFIED"
            ),
        },
        "exchange_snapshot": exchange,
        "root_cause_assessment": {
            "code": "CANARY_SINGLE_POSITION_THROUGHPUT_BOTTLENECK" if post_eth_max_exposure else "NOT_CONFIRMED",
            "candidate_level_blocker": "MAX_OPEN_EXPOSURES",
            "strategy_side_blockers": {
                reason: count
                for reason, count in reason_counts.items()
                if reason in {"MACD_DIRECTION_MISMATCH", "RSI_OUTSIDE_RANGE", "MULTI_TIMEFRAME_DISAGREEMENT"}
            },
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", default=DB)
    parser.add_argument("--hours", type=int, default=24)
    parser.add_argument("--output", default=str(OUTPUT))
    args = parser.parse_args()
    report = collect(db_path=args.database, hours=args.hours)
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    print(json.dumps(report["totals"], ensure_ascii=False))
    print(json.dumps(report["rejection_reasons"], ensure_ascii=False, sort_keys=True))
    print(json.dumps(report["post_eth_position"], ensure_ascii=False))
    print(f"wrote {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
