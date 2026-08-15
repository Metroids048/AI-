"""Read-only cohort audit for R2 rejects versus entry intents/orders/fills."""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from services.automated_trading.application.decision_service import BarView, TimeframeView, evaluate_sampling_signal
from services.research.exit_policy_shadow.loader import load_bars

DB = Path(".local_paper_console.db").resolve()
NOW = datetime.now(UTC)
WINDOW_START = (NOW - timedelta(hours=12)).replace(tzinfo=None)


def _json(value: object) -> dict:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value or "{}"))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _dt(value: object) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed.replace(tzinfo=None) if parsed.tzinfo is None else parsed.astimezone(UTC).replace(tzinfo=None)


def main() -> int:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    since = WINDOW_START.isoformat(sep=" ")
    decisions = [
        dict(row)
        for row in con.execute(
            "SELECT decision_id, cycle_id, created_at, terminal_reason, payload "
            "FROM v2_execution_decisions WHERE created_at >= ? ORDER BY created_at",
            (since,),
        )
    ]
    r2 = [row for row in decisions if row["terminal_reason"] == "NO_TRADE_COST_INEFFICIENT"]
    r2_payloads = []
    for row in r2:
        payload = _json(row["payload"])
        r2_payloads.append(
            {
                "decision_id": row["decision_id"],
                "symbol": payload.get("symbol"),
                "created_at": row["created_at"],
                "candidate": payload.get("candidate") or payload.get("candidate_snapshot"),
                "cost_gate": payload.get("r2_cost_gate") or payload.get("cost_gate") or payload.get("r2"),
                "payload_keys": sorted(payload),
            }
        )
    excursions = []
    confirmations = []
    for item in r2_payloads:
        candidate = item["candidate"] or {}
        try:
            entry = Decimal(str(candidate["signal_reference_price"]))
            risk = Decimal(str(candidate["stop_distance"]))
            side = str(candidate["side"]).lower()
            parsed_start = _dt(candidate["signal_candle_close_time"])
            if parsed_start is None:
                continue
            start = parsed_start.replace(tzinfo=UTC)
        except (KeyError, ValueError):
            continue
        bars = load_bars(DB, symbol=str(candidate["symbol"]), timeframe="1m", start=start, end=start + timedelta(hours=24))
        if not bars:
            continue
        favorable = max((bar.high - entry if side == "long" else entry - bar.low) for bar in bars)
        adverse = max((entry - bar.low if side == "long" else bar.high - entry) for bar in bars)
        excursions.append({"mfe_r": str(favorable / risk), "mae_r": str(adverse / risk)})
        bars_15m = load_bars(DB, symbol=str(candidate["symbol"]), timeframe="15m", start=start - timedelta(hours=30), end=start + timedelta(minutes=15))
        view = TimeframeView("15m", tuple(BarView(timestamp=bar.time, open=bar.open, high=bar.high, low=bar.low, close=bar.close, volume=bar.volume) for bar in bars_15m[-120:]))
        confirmation = evaluate_sampling_signal(view)
        confirmations.append({"same_side": confirmation.side is not None and confirmation.side.value.lower() == side})
    intents = [
        dict(row)
        for row in con.execute(
            "SELECT intent_id, cycle_id, decision_id, symbol, candidate_key, state, created_at "
            "FROM v2_execution_intents WHERE execution_mode = 'BINANCE_TESTNET' AND created_at >= ? "
            "ORDER BY created_at",
            (since,),
        )
    ]
    orders = [
        dict(row)
        for row in con.execute(
            "SELECT order_record_id, intent_id, exchange_order_id, submitted_at, created_at "
            "FROM v2_exchange_orders WHERE created_at >= ? ORDER BY created_at",
            (since,),
        )
    ]
    fills = [
        dict(row)
        for row in con.execute(
            "SELECT fill_id, intent_id, exchange_order_id, reduce_only, received_at "
            "FROM v2_exchange_fills WHERE received_at >= ? ORDER BY received_at",
            (since,),
        )
    ]
    r2_decision_ids = {row["decision_id"] for row in r2}
    r2_cycle_ids = {row["cycle_id"] for row in r2}
    intent_decision_ids = {row["decision_id"] for row in intents if row["decision_id"]}
    intent_cycle_ids = {row["cycle_id"] for row in intents if row["cycle_id"]}
    linked_decisions = {row["decision_id"] for row in decisions}
    report = {
        "generated_at": NOW.isoformat(),
        "window_start_utc": WINDOW_START.replace(tzinfo=UTC).isoformat(),
        "counts": {
            "decisions": len(decisions),
            "r2_rejects": len(r2),
            "intents": len(intents),
            "orders": len(orders),
            "entry_fills": sum(not bool(row["reduce_only"]) for row in fills),
            "exit_fills": sum(bool(row["reduce_only"]) for row in fills),
        },
        "r2_reason_by_symbol": dict(Counter(row["symbol"] for row in r2 if row.get("symbol"))),
        "cohort_overlap": {
            "r2_decision_id_to_intent": sorted(r2_decision_ids & intent_decision_ids),
            "r2_cycle_id_to_intent": sorted(r2_cycle_ids & intent_cycle_ids),
            "intent_decisions_present_in_decision_window": sorted(intent_decision_ids & linked_decisions),
        },
        "r2_range": {
            "first": r2[0]["created_at"] if r2 else None,
            "last": r2[-1]["created_at"] if r2 else None,
            "decision_ids": [row["decision_id"] for row in r2],
            "cycle_ids": [row["cycle_id"] for row in r2],
        },
        "r2_payloads": r2_payloads,
        "r2_excursions_24h": excursions,
        "one_bar_confirmation": confirmations,
        "intents": intents,
        "orders": orders,
        "fills": fills,
    }
    print(json.dumps(report, indent=2, default=str))
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
