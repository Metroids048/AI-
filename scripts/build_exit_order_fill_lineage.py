"""Build a read-only, canonical exit-order/fill lineage and loss waterfall.

The runtime P1 parity audit already computes R0/R1/R2/R3 outcomes.  This
report joins those outcomes to the durable V2 intent, order, fill, protection,
event, and incident rows so every episode can be audited without inferring an
exchange execution from local state alone.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "docs/audits/2026-08-16-runtime-p1-parity.json"
DEFAULT_DATABASE = ROOT / ".local_paper_console.db"
DEFAULT_OUTPUT = ROOT / "docs/audits/2026-08-16-exit-order-fill-lineage.json"
DEFAULT_MARKDOWN = ROOT / "docs/audits/2026-08-16-exit-order-fill-lineage.md"


def decimal(value: Any) -> Decimal:
    return Decimal(str(value or "0"))


def signed_delta(stage_after: dict[str, Any], stage_before: dict[str, Any], field: str) -> Decimal:
    return decimal(stage_after.get(field)) - decimal(stage_before.get(field))


def classify_exit_reason(reasons: list[str]) -> str:
    if not reasons:
        return "MISSING_EXIT_FILL"
    if any(reason in {"EMERGENCY_CLOSE", "QUARANTINE", "ABNORMAL_EXIT"} for reason in reasons):
        return "ABNORMAL_EXIT"
    if any(reason in {"HARD_STOP", "STOP"} for reason in reasons):
        return "STOP"
    if any(reason in {"TAKE_PROFIT", "TARGET"} for reason in reasons):
        return "TARGET"
    return "OTHER_EXIT"


def waterfall_for_row(
    row: dict[str, Any], *, partial_fill_detected: bool, protection_event_count: int
) -> dict[str, Decimal]:
    """Partition the measured R0->R3 delta without double counting.

    These buckets intentionally follow observable stage boundaries.  Entry
    execution/model-cost is the R0->R1 delta; protection is R1->R2; exchange
    trigger/fill is the gross R2->R3 delta; commissions are the final net/gross
    delta.  The remaining diagnostic buckets are orthogonal flags and carry
    zero until their effect can be measured without inventing a price.
    """
    r0 = row["r0_static_replay"]
    r1 = row["r1_static_actual_entry"]
    r2 = row["r2_dynamic_p1"]
    r3 = row["r3_actual_exchange"]
    risk = decimal(row["risk_usdt"])
    if risk <= 0:
        raise ValueError(f"episode {row.get('position_id', '<unknown>')} has non-positive risk")
    buckets = {
        "entry_execution": (decimal(r1.get("gross_pnl_usdt")) - decimal(r0.get("net_pnl_usdt"))) / risk,
        "entry_slippage": Decimal("0"),
        "commission": (decimal(r3.get("net_pnl_usdt")) - decimal(r3.get("gross_pnl_usdt"))) / risk,
        "exit_trigger_geometry": Decimal("0"),
        "trigger_to_fill_slippage": signed_delta(r3, r2, "gross_pnl_usdt") / risk,
        "profit_protection": signed_delta(r2, r1, "gross_pnl_usdt") / risk,
        "intrabar_timing": Decimal("0"),
        "partial_fill": Decimal("0"),
        "abnormal_exits": Decimal("0"),
        "funding_attributable": Decimal("0"),
        "cohort_data_mismatch": Decimal("0"),
        "unknown_residual": Decimal("0"),
    }
    if row["r1_static_actual_entry"].get("ambiguous_intrabar") or row["r2_dynamic_p1"].get("ambiguous_intrabar"):
        buckets["intrabar_timing"] = buckets["trigger_to_fill_slippage"]
        buckets["trigger_to_fill_slippage"] = Decimal("0")
    if partial_fill_detected:
        buckets["partial_fill"] = buckets["trigger_to_fill_slippage"]
        buckets["trigger_to_fill_slippage"] = Decimal("0")
    if protection_event_count == 0 and row["r2_dynamic_p1"].get("p1_triggered"):
        buckets["unknown_residual"] += buckets["profit_protection"]
        buckets["profit_protection"] = Decimal("0")
    return buckets


def _rows(conn: sqlite3.Connection, query: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(query, params).fetchall()]


def _decode_events(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    decoded: list[dict[str, Any]] = []
    for row in rows:
        payload = row.get("event_payload")
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError:
                payload = {"raw_event_payload": payload}
        decoded.append({**row, "event_payload": payload if isinstance(payload, dict) else {}})
    return decoded


def exit_order_link(exit_fills: list[dict[str, Any]], protection_events: list[dict[str, Any]]) -> dict[str, Any]:
    """Prove every exit fill belongs to the recorded protection trigger."""
    exit_order_ids = sorted(
        {str(fill.get("exchange_order_id") or "") for fill in exit_fills if fill.get("exchange_order_id")}
    )
    triggered_order_ids = sorted(
        {
            str(event["event_payload"].get("exchange_order_id") or "")
            for event in protection_events
            if event.get("event_type") == "ProtectionTriggered"
            and event.get("event_payload", {}).get("exchange_order_id")
        }
    )
    if not exit_order_ids:
        status = "MISSING_EXIT_FILL"
    elif not triggered_order_ids:
        status = "MISSING_PROTECTION_TRIGGER_EVENT"
    elif exit_order_ids == triggered_order_ids:
        status = "MATCHED"
    else:
        status = "MISMATCHED"
    return {
        "status": status,
        "exit_order_ids": exit_order_ids,
        "triggered_order_ids": triggered_order_ids,
    }


def _active_protections(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = _rows(
        conn,
        """SELECT mp.position_id, mp.symbol, mp.direction, mp.quantity, mp.state,
                  pr.protection_id, pr.original_stop_loss_price, pr.stop_loss_price,
                  pr.take_profit_price, pr.stop_exchange_order_id, pr.tp_exchange_order_id,
                  pr.version, pr.policy
           FROM v2_managed_positions mp
           JOIN v2_protection_records pr ON pr.position_id=mp.position_id
           WHERE mp.state IN ('POSITION_PROJECTED', 'PROTECTED', 'REDUCING')
           ORDER BY mp.projected_at""",
        (),
    )
    for row in rows:
        events = _decode_events(
            _rows(
                conn,
                "SELECT event_type, event_payload, occurred_at FROM v2_execution_events WHERE aggregate_id=? ORDER BY occurred_at",
                (row["protection_id"],),
            )
        )
        row["replacement_event_count"] = sum(event["event_type"] == "ProfitProtectionStopTightened" for event in events)
        row["events"] = events
    return rows


def build_report(input_path: Path, database_path: Path) -> dict[str, Any]:
    parity = json.loads(input_path.read_text(encoding="utf-8"))
    connection = sqlite3.connect(f"file:{database_path.resolve().as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    episodes: list[dict[str, Any]] = []
    totals: dict[str, Decimal] = defaultdict(Decimal)
    reason_counts: Counter[str] = Counter()
    active_protections: list[dict[str, Any]] = []
    try:
        for source in parity.get("rows", []):
            position_id = str(source["position_id"])
            position = _rows(
                connection,
                "SELECT * FROM v2_managed_positions WHERE position_id=?",
                (position_id,),
            )
            if not position:
                episodes.append({"position_id": position_id, "status": "MISSING_POSITION"})
                continue
            position_row = position[0]
            intent_rows = _rows(
                connection,
                "SELECT * FROM v2_execution_intents WHERE intent_id=?",
                (position_row["intent_id"],),
            )
            if not intent_rows:
                episodes.append({"position_id": position_id, "status": "MISSING_INTENT"})
                continue
            intent = intent_rows[0]
            entry_orders = _rows(
                connection,
                "SELECT * FROM v2_exchange_orders WHERE order_record_id=?",
                (position_row["order_record_id"],),
            )
            entry_order = entry_orders[0] if entry_orders else {}
            entry_fills = _rows(
                connection,
                "SELECT * FROM v2_exchange_fills WHERE exchange_order_record_id=? ORDER BY exchange_event_time, trade_id",
                (position_row["order_record_id"],),
            )
            exit_fills = _rows(
                connection,
                """SELECT f.*, i.candidate_key AS exit_candidate_key
                   FROM v2_exchange_fills f
                   JOIN v2_execution_intents i ON i.intent_id=f.intent_id
                   WHERE i.candidate_key LIKE ? AND f.reduce_only=1
                   ORDER BY f.exchange_event_time, f.trade_id""",
                (f"exit:{position_id}:%",),
            )
            protection = _rows(
                connection,
                "SELECT * FROM v2_protection_records WHERE position_id=?",
                (position_id,),
            )
            protection_row = protection[0] if protection else {}
            events = _decode_events(
                _rows(
                    connection,
                    "SELECT * FROM v2_execution_events WHERE aggregate_id=? ORDER BY occurred_at",
                    (protection_row["protection_id"],),
                )
                if protection_row
                else []
            )
            incidents = _rows(
                connection,
                "SELECT * FROM v2_execution_incidents WHERE position_id=? ORDER BY created_at",
                (position_id,),
            )
            reasons = sorted({str(fill.get("exit_candidate_key") or "").rsplit(":", 1)[-1] for fill in exit_fills})
            exit_reason = classify_exit_reason(reasons)
            order_link = exit_order_link(exit_fills, events)
            exit_quantity = sum((decimal(fill.get("filled_quantity")) for fill in exit_fills), Decimal("0"))
            partial_fill_detected = bool(exit_fills) and exit_quantity != decimal(position_row.get("quantity"))
            waterfall = waterfall_for_row(
                source,
                partial_fill_detected=partial_fill_detected,
                protection_event_count=sum(
                    1 for event in events if event.get("event_type") == "ProfitProtectionStopTightened"
                ),
            )
            for key, value in waterfall.items():
                totals[key] += value
            reason_counts[exit_reason] += 1
            episodes.append(
                {
                    "position_id": position_id,
                    "symbol": source.get("symbol"),
                    "direction": source.get("direction"),
                    "candidate_key": intent.get("candidate_key"),
                    "entry": {
                        "intent_id": intent.get("intent_id"),
                        "order_record_id": entry_order.get("order_record_id"),
                        "exchange_order_id": entry_order.get("exchange_order_id"),
                        "client_order_id": entry_order.get("client_order_id"),
                        "fill_count": len(entry_fills),
                        "fills": entry_fills,
                    },
                    "protection": {
                        "protection_id": protection_row.get("protection_id"),
                        "policy": protection_row.get("policy"),
                        "state": protection_row.get("state"),
                        "stop_exchange_order_id": protection_row.get("stop_exchange_order_id"),
                        "tp_exchange_order_id": protection_row.get("tp_exchange_order_id"),
                        "event_count": len(events),
                        "replacement_event_count": sum(
                            1 for event in events if event.get("event_type") == "ProfitProtectionStopTightened"
                        ),
                        "events": events,
                    },
                    "exit": {
                        "fill_count": len(exit_fills),
                        "fills": exit_fills,
                        "filled_quantity": str(exit_quantity),
                        "partial_fill_detected": partial_fill_detected,
                        "reasons": reasons,
                        "canonical_reason": exit_reason,
                        "order_link": order_link,
                        "incidents": incidents,
                    },
                    "stage_outcomes": {
                        "r0": source.get("r0_static_replay"),
                        "r1": source.get("r1_static_actual_entry"),
                        "r2": source.get("r2_dynamic_p1"),
                        "r3": source.get("r3_actual_exchange"),
                    },
                    "waterfall_r": {key: str(value) for key, value in waterfall.items()},
                    "status": "OK"
                    if source.get("status") == "OK" and order_link["status"] == "MATCHED"
                    else order_link["status"],
                }
            )
        active_protections = _active_protections(connection)
    finally:
        connection.close()
    report = {
        "schema_version": 1,
        "status": "READ_ONLY",
        "holdout_accessed": False,
        "source_parity_audit": str(input_path.relative_to(ROOT)),
        "database": str(database_path.relative_to(ROOT)),
        "episode_count": len(episodes),
        "closed_episode_count": sum(episode.get("status") == "OK" for episode in episodes),
        "verified_exit_order_link_count": sum(
            episode.get("exit", {}).get("order_link", {}).get("status") == "MATCHED" for episode in episodes
        ),
        "exit_reason_counts": dict(reason_counts),
        "waterfall_total_r": {key: str(value) for key, value in sorted(totals.items())},
        "waterfall_definition": {
            "entry_execution": "R1 actual-entry gross minus R0 modeled net, normalized by runtime risk",
            "profit_protection": "R2 dynamic P1 gross minus R1 static actual-entry gross when a real replacement event exists",
            "trigger_to_fill_slippage": "R3 actual gross minus R2 modeled gross",
            "commission": "R3 actual net minus R3 actual gross",
            "unknown_residual": "Measured protection effect withheld when simulation triggered but no runtime replacement event exists",
            "zero_buckets": "No price effect assigned without an authoritative exchange or replay measurement",
        },
        "current_active_protections": active_protections,
        "episodes": episodes,
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--markdown", type=Path, default=DEFAULT_MARKDOWN)
    args = parser.parse_args()
    report = build_report(args.input, args.database)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    totals = report["waterfall_total_r"]
    lines = [
        "# Exit Order Fill Lineage",
        "",
        f"- Status: `{report['status']}`; episodes `{report['episode_count']}`; closed `{report['closed_episode_count']}`.",
        "- SQLite was opened read-only; no exchange or local execution state was changed.",
        "",
        "## Waterfall (R)",
        "",
        "| Loss source | Total R |",
        "| --- | ---: |",
    ]
    lines.extend(f"| {key} | {value} |" for key, value in totals.items())
    lines.extend(["", "## Exit Reasons", ""])
    lines.extend(f"- `{key}`: {value}" for key, value in sorted(report["exit_reason_counts"].items()))
    args.markdown.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "episode_count": report["episode_count"],
                "closed_episode_count": report["closed_episode_count"],
                "waterfall_total_r": totals,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
