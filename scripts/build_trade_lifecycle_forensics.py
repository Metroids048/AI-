"""Build read-only trade-lifecycle quality evidence from filled V2 episodes.

This report is deliberately downstream of execution truth. It never creates an
intent, submits an order, updates SQLite, or changes a strategy parameter. It
answers the missing acceptance question: did a technically valid stop-out later
recover in the entry direction, and was the original geometry the 0.35% floor?
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from services.research.exit_policy_shadow.contracts import Bar
from services.research.exit_policy_shadow.loader import build_entry_context, load_bars

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATABASE = ROOT / ".local_paper_console.db"
DEFAULT_OUTPUT = ROOT / ".local/trade-lifecycle-forensics.json"
DEFAULT_MARKDOWN = ROOT / ".local/trade-lifecycle-forensics.md"
RECOVERY_WINDOWS = {
    "15m": timedelta(minutes=15),
    "30m": timedelta(minutes=30),
    "1h": timedelta(hours=1),
    "2h": timedelta(hours=2),
    "4h": timedelta(hours=4),
}
REQUESTED_SAMPLES: tuple[tuple[str, Decimal, Decimal], ...] = (
    ("ETH/USDT", Decimal("1903.21"), Decimal("5.354")),
    ("SOL/USDT", Decimal("76.30"), Decimal("85.09")),
)


def decimal(value: Any) -> Decimal:
    return Decimal(str(value or "0"))


def parse_datetime(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def load_authoritative_closed_episodes(database_path: Path) -> list[dict[str, Any]]:
    """Read every closed automatic V2 position from the execution facts.

    The cohort is deliberately derived from the V2 intent, fill, position and
    protection tables, rather than from a hand-maintained parity audit.  A
    historical ``exit:*:MANUAL_REDUCE_ONLY`` intent is an accounting recovery,
    not an automatic strategy episode, and is excluded.  Missing receipts stay
    in the report as incomplete evidence instead of being invented from local
    PnL or a static fixture.
    """
    uri = f"file:{database_path.resolve().as_posix()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    try:
        records = connection.execute(
            """
            WITH entry_fills AS (
                SELECT
                    intent_id,
                    MIN(exchange_event_time) AS entry_time,
                    SUM(filled_quantity) AS entry_filled_quantity,
                    SUM(filled_quantity * fill_price) / SUM(filled_quantity) AS entry_fill_price,
                    SUM(COALESCE(commission, 0)) AS entry_fill_commission
                FROM v2_exchange_fills
                WHERE reduce_only = 0
                GROUP BY intent_id
            ), exit_fills AS (
                SELECT
                    intent_id,
                    MAX(exchange_event_time) AS exit_time,
                    SUM(filled_quantity) AS exit_filled_quantity,
                    SUM(filled_quantity * fill_price) / SUM(filled_quantity) AS exit_fill_price,
                    SUM(COALESCE(commission, 0)) AS exit_fill_commission
                FROM v2_exchange_fills
                WHERE reduce_only = 1
                GROUP BY intent_id
            )
            SELECT
                mp.position_id,
                mp.symbol,
                mp.direction,
                mp.quantity,
                mp.entry_price,
                mp.entry_fee,
                mp.closed_at,
                mp.realized_pnl,
                i.candidate_key,
                i.candidate_type,
                i.decision_bar_timestamp,
                entry_fill.entry_fill_price,
                entry_fill.entry_fill_commission,
                entry_fill.entry_time,
                protection.stop_loss_price,
                protection.take_profit_price,
                protection.stop_exchange_order_id,
                protection.tp_exchange_order_id,
                json_extract(close_event.event_payload, '$.reason') AS exit_reason,
                json_extract(close_event.event_payload, '$.exchange_order_id') AS exit_exchange_order_id,
                exit_fill.exit_fill_price,
                exit_fill.exit_fill_commission,
                exit_fill.exit_time
            FROM v2_managed_positions AS mp
            JOIN v2_execution_intents AS i ON i.intent_id = mp.intent_id
            LEFT JOIN entry_fills AS entry_fill ON entry_fill.intent_id = i.intent_id
            LEFT JOIN v2_protection_records AS protection ON protection.protection_id = (
                SELECT protection_candidate.protection_id
                FROM v2_protection_records AS protection_candidate
                WHERE protection_candidate.position_id = mp.position_id
                ORDER BY protection_candidate.created_at DESC, protection_candidate.protection_id DESC
                LIMIT 1
            )
            LEFT JOIN v2_execution_events AS close_event ON close_event.event_id = (
                SELECT event_candidate.event_id
                FROM v2_execution_events AS event_candidate
                WHERE event_candidate.aggregate_id = mp.position_id
                  AND event_candidate.aggregate_type = 'POSITION'
                  AND event_candidate.event_type IN ('PositionClosed', 'QuarantinedProjectionCorrectedFromConfirmedExit')
                ORDER BY event_candidate.occurred_at DESC, event_candidate.event_id DESC
                LIMIT 1
            )
            LEFT JOIN exit_fills AS exit_fill
                ON exit_fill.intent_id = json_extract(close_event.event_payload, '$.exit_intent_id')
            WHERE mp.state = 'CLOSED'
              AND i.candidate_key NOT LIKE 'exit:%'
            ORDER BY entry_fill.entry_time ASC, mp.position_id ASC
            """
        ).fetchall()
    finally:
        connection.close()

    episodes: list[dict[str, Any]] = []
    for record in records:
        entry_time = record["entry_time"]
        exit_time = record["exit_time"]
        exit_reason = str(record["exit_reason"] or "UNKNOWN_EXIT")
        complete = entry_time is not None and exit_time is not None and record["stop_loss_price"] is not None
        episodes.append(
            {
                "position_id": record["position_id"],
                "symbol": record["symbol"],
                "direction": record["direction"],
                "quantity": str(record["quantity"]),
                "entry_price": str(record["entry_fill_price"] or record["entry_price"]),
                "entry_time": entry_time,
                "exit_time": exit_time,
                "decision_bar": record["decision_bar_timestamp"],
                "candidate_key": record["candidate_key"],
                "candidate_type": record["candidate_type"],
                "initial_stop": str(record["stop_loss_price"]) if record["stop_loss_price"] is not None else None,
                "initial_target": str(record["take_profit_price"]) if record["take_profit_price"] is not None else None,
                "exit_reason": exit_reason,
                "exit_price": str(record["exit_fill_price"]) if record["exit_fill_price"] is not None else None,
                "entry_fee_usdt": str(record["entry_fill_commission"] or record["entry_fee"] or 0),
                "exit_fee_usdt": str(record["exit_fill_commission"] or 0),
                "realized_net_pnl_usdt": str(record["realized_pnl"]) if record["realized_pnl"] is not None else None,
                "funding_usdt": None,
                "slippage_usdt": None,
                "evidence_status": "COMPLETE" if complete else "INCOMPLETE_EXECUTION_RECEIPT",
                "missing_evidence": [
                    name
                    for name, value in (
                        ("entry_fill", entry_time),
                        ("exit_fill", exit_time),
                        ("initial_stop", record["stop_loss_price"]),
                    )
                    if value is None
                ],
            }
        )
    return episodes


def stop_floor_evidence(*, entry_price: Decimal, atr14: Decimal | None, runtime_stop: Decimal) -> dict[str, Any]:
    """Identify the source term without changing or rounding live geometry."""
    if atr14 is None or atr14 <= 0:
        return {"status": "INSUFFICIENT_ATR", "source": "UNKNOWN"}
    atr_term = Decimal("1.2") * atr14
    pct_term = entry_price * Decimal("0.0035")
    distance = abs(entry_price - runtime_stop)
    source = "PCT_FLOOR_0.35%" if pct_term >= atr_term else "ATR14_TERM"
    return {
        "status": "CONFIRMED",
        "source": source,
        "atr14": str(atr14),
        "atr_term": str(atr_term),
        "pct_floor_term": str(pct_term),
        "runtime_stop_distance": str(distance),
        "runtime_stop_distance_pct": str(distance / entry_price),
        "floor_winner_margin": str(pct_term - atr_term),
    }


def excursion_metrics(
    *, entry_price: Decimal, side: str, quantity: Decimal, risk_per_unit: Decimal, bars: list[Bar]
) -> dict[str, Any]:
    """Measure excursions through the actual exit bar, direction-symmetrically."""
    favorable_values: list[Decimal] = []
    adverse_values: list[Decimal] = []
    for bar in bars:
        if side == "long":
            favorable_values.append(bar.high - entry_price)
            adverse_values.append(bar.low - entry_price)
        else:
            favorable_values.append(entry_price - bar.low)
            adverse_values.append(entry_price - bar.high)
    mfe_price = max([Decimal("0"), *favorable_values]) if favorable_values else None
    mae_price = min([Decimal("0"), *adverse_values]) if adverse_values else None
    if mfe_price is None or mae_price is None:
        return {"status": "INSUFFICIENT_BARS"}
    return {
        "status": "CONFIRMED",
        "bar_count": len(bars),
        "mfe_price": str(mfe_price),
        "mae_price": str(mae_price),
        "mfe_pct": str(mfe_price / entry_price),
        "mae_pct": str(mae_price / entry_price),
        "mfe_usdt": str(mfe_price * quantity),
        "mae_usdt": str(mae_price * quantity),
        "mfe_r": str(mfe_price / risk_per_unit) if risk_per_unit > 0 else None,
        "mae_r": str(mae_price / risk_per_unit) if risk_per_unit > 0 else None,
    }


def recovery_windows(
    *, entry_price: Decimal, side: str, risk_per_unit: Decimal, exit_time: datetime, bars: list[Bar]
) -> dict[str, dict[str, Any]]:
    """Measure only bars strictly after the exit timestamp to avoid lookahead."""
    result: dict[str, dict[str, Any]] = {}
    for label, duration in RECOVERY_WINDOWS.items():
        end = exit_time + duration
        window = [bar for bar in bars if exit_time < bar.time <= end]
        if not window:
            result[label] = {"status": "INSUFFICIENT_DATA", "bar_count": 0}
            continue
        favorable = [bar.high - entry_price if side == "long" else entry_price - bar.low for bar in window]
        adverse = [bar.low - entry_price if side == "long" else entry_price - bar.high for bar in window]
        max_favorable = max([Decimal("0"), *favorable])
        max_adverse = min([Decimal("0"), *adverse])
        complete = window[-1].time >= end - timedelta(minutes=1)
        result[label] = {
            "status": "COMPLETE" if complete else "TRUNCATED",
            "bar_count": len(window),
            "max_favorable_price": str(max_favorable),
            "max_adverse_price": str(max_adverse),
            "recovered_entry": max_favorable > 0,
            "recovered_1r": max_favorable >= risk_per_unit if risk_per_unit > 0 else False,
            "max_favorable_r": str(max_favorable / risk_per_unit) if risk_per_unit > 0 else None,
            "max_adverse_r": str(max_adverse / risk_per_unit) if risk_per_unit > 0 else None,
        }
    return result


def classify_taxonomy(*, exit_reason: str, floor_source: str, recovery: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Apply labels only where the 4h evidence is complete."""
    if exit_reason in {"TARGET", "TAKE_PROFIT"}:
        return {"primary": "GOOD_WIN", "labels": [], "confidence": "HIGH"}
    if exit_reason not in {"STOP", "HARD_STOP"}:
        return {"primary": "UNCLASSIFIED_EXIT", "labels": [], "confidence": "LOW"}
    four_hour = recovery.get("4h", {})
    if four_hour.get("status") != "COMPLETE":
        return {
            "primary": "UNCLASSIFIED_INSUFFICIENT_DATA",
            "labels": [],
            "confidence": "LOW",
        }
    if four_hour.get("recovered_entry"):
        primary = "STOP_GEOMETRY_FAILURE" if floor_source == "PCT_FLOOR_0.35%" else "ENTRY_TIMING_FAILURE"
        return {"primary": primary, "labels": ["STOPPED_THEN_RECOVERED"], "confidence": "MEDIUM"}
    if decimal(four_hour.get("max_adverse_r")) <= Decimal("-1"):
        return {"primary": "DIRECTION_FAILURE", "labels": [], "confidence": "MEDIUM"}
    if decimal(four_hour.get("max_adverse_price")) < 0:
        return {"primary": "GOOD_LOSS", "labels": [], "confidence": "MEDIUM"}
    return {"primary": "DIRECTION_FAILURE", "labels": [], "confidence": "MEDIUM"}


def _sample_matches(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for symbol, expected_entry_price, expected_quantity in REQUESTED_SAMPLES:
        matches = [
            row
            for row in rows
            if row.get("symbol") == symbol
            and abs(decimal(row.get("entry_price")) - expected_entry_price) <= expected_entry_price * Decimal("0.0002")
            and abs(decimal(row.get("quantity")) - expected_quantity) <= Decimal("0.0002")
        ]
        result.append(
            {
                "symbol": symbol,
                "expected_entry_price": str(expected_entry_price),
                "expected_quantity": str(expected_quantity),
                "status": "FOUND" if matches else "NOT_FOUND_IN_CURRENT_COHORT",
                "position_ids": [row.get("position_id") for row in matches],
            }
        )
    return result


def build_report(*, database_path: Path) -> dict[str, Any]:
    database_path = database_path.resolve()
    sources = load_authoritative_closed_episodes(database_path)
    rows: list[dict[str, Any]] = []
    taxonomy_counts: Counter[str] = Counter()
    floor_counts: Counter[str] = Counter()
    incomplete_position_ids: list[str] = []
    for source in sources:
        if source["evidence_status"] != "COMPLETE":
            incomplete_position_ids.append(str(source["position_id"]))
            taxonomy = {"primary": "EXECUTION_FAILURE", "labels": [], "confidence": "LOW"}
            taxonomy_counts[str(taxonomy["primary"])] += 1
            rows.append(
                {
                    **source,
                    "stop_floor": {"status": "INSUFFICIENT_EVIDENCE", "source": "UNKNOWN"},
                    "excursions": {"status": "INSUFFICIENT_EVIDENCE"},
                    "post_exit_recovery": {},
                    "taxonomy": taxonomy,
                }
            )
            continue
        entry_price = decimal(source.get("entry_price"))
        quantity = decimal(source.get("quantity"))
        runtime_stop = decimal(source.get("initial_stop"))
        risk_per_unit = abs(entry_price - runtime_stop)
        entry_time = parse_datetime(source["entry_time"])
        exit_time = parse_datetime(source["exit_time"])
        decision_bar = parse_datetime(source["decision_bar"]) if source.get("decision_bar") else None
        context = (
            build_entry_context(database_path, symbol=source["symbol"], decision_bar=decision_bar)
            if decision_bar is not None
            else None
        )
        atr14 = context.get("atr14") if context else None
        floor = stop_floor_evidence(entry_price=entry_price, atr14=atr14, runtime_stop=runtime_stop)
        bars = load_bars(
            database_path,
            symbol=source["symbol"],
            timeframe="1m",
            start=entry_time,
            end=exit_time,
        )
        post_exit_bars = load_bars(
            database_path,
            symbol=source["symbol"],
            timeframe="1m",
            start=exit_time,
            end=exit_time + RECOVERY_WINDOWS["4h"],
        )
        excursions = excursion_metrics(
            entry_price=entry_price,
            side=source["direction"],
            quantity=quantity,
            risk_per_unit=risk_per_unit,
            bars=bars,
        )
        recovery = recovery_windows(
            entry_price=entry_price,
            side=source["direction"],
            risk_per_unit=risk_per_unit,
            exit_time=exit_time,
            bars=post_exit_bars,
        )
        exit_reason = str(source["exit_reason"])
        taxonomy = classify_taxonomy(
            exit_reason=exit_reason,
            floor_source=str(floor.get("source")),
            recovery=recovery,
        )
        taxonomy_counts[str(taxonomy["primary"])] += 1
        floor_counts[str(floor.get("source"))] += 1
        rows.append(
            {
                "position_id": source["position_id"],
                "symbol": source["symbol"],
                "direction": source["direction"],
                "entry_time": source["entry_time"],
                "exit_time": source["exit_time"],
                "entry_price": str(entry_price),
                "quantity": str(quantity),
                "exit_reason": exit_reason,
                "decision_bar": decision_bar.isoformat() if decision_bar is not None else None,
                "candidate_key": source["candidate_key"],
                "candidate_type": source["candidate_type"],
                "realized_net_pnl_usdt": source["realized_net_pnl_usdt"],
                "entry_fee_usdt": source["entry_fee_usdt"],
                "exit_fee_usdt": source["exit_fee_usdt"],
                "funding_usdt": source["funding_usdt"],
                "slippage_usdt": source["slippage_usdt"],
                "initial_stop": str(runtime_stop),
                "initial_target": source.get("initial_target"),
                "stop_floor": floor,
                "excursions": excursions,
                "post_exit_recovery": recovery,
                "taxonomy": taxonomy,
            }
        )
    return {
        "schema_version": 1,
        "status": "READ_ONLY",
        "holdout_accessed": False,
        "strategy_deployment": "BLOCKED",
        "cohort": "ALL_CLOSED_V2_MANAGED_AUTOMATIC_POSITIONS",
        "database": str(database_path.relative_to(ROOT)),
        "episode_count": len(rows),
        "incomplete_position_ids": incomplete_position_ids,
        "taxonomy_counts": dict(sorted(taxonomy_counts.items())),
        "stop_floor_counts": dict(sorted(floor_counts.items())),
        "requested_sample_check": _sample_matches(rows),
        "limitations": [
            "The cohort is derived from the read-only V2 execution facts, not a static parity audit.",
            "Recovery is classified only when 1m bars cover the full four-hour horizon.",
            "The 0.35% floor is evidence of geometry source, not evidence that widening it is superior.",
            "No replay variant was deployed or armed from this report.",
        ],
        "episodes": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--markdown", type=Path, default=DEFAULT_MARKDOWN)
    args = parser.parse_args()
    report = build_report(database_path=args.database)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# Trade Lifecycle Forensics",
        "",
        f"- Status: `{report['status']}`; episodes `{report['episode_count']}`.",
        "- SQLite was opened read-only; no execution state or strategy configuration was changed.",
        f"- Taxonomy: `{report['taxonomy_counts']}`.",
        f"- Stop floor sources: `{report['stop_floor_counts']}`.",
        "",
        "## Requested ETH/SOL samples",
        "",
    ]
    lines.extend(
        f"- `{item['symbol']}` entry `{item['expected_entry_price']}` qty `{item['expected_quantity']}`: `{item['status']}`"
        for item in report["requested_sample_check"]
    )
    args.markdown.parent.mkdir(parents=True, exist_ok=True)
    args.markdown.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "status": report["status"],
                "episode_count": report["episode_count"],
                "taxonomy_counts": report["taxonomy_counts"],
                "requested_sample_check": report["requested_sample_check"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
