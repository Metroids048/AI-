"""Build read-only trade-lifecycle quality evidence from filled V2 episodes.

This report is deliberately downstream of execution truth. It never creates an
intent, submits an order, updates SQLite, or changes a strategy parameter. It
answers the missing acceptance question: did a technically valid stop-out later
recover in the entry direction, and was the original geometry the 0.35% floor?
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from services.research.exit_policy_shadow.contracts import Bar
from services.research.exit_policy_shadow.loader import build_entry_context, load_bars, load_real_entries

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "docs/audits/2026-08-16-runtime-p1-parity.json"
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
        return {"primary": "WIN_TARGET", "labels": [], "confidence": "HIGH"}
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


def build_report(*, input_path: Path, database_path: Path) -> dict[str, Any]:
    input_path = input_path.resolve()
    database_path = database_path.resolve()
    parity = json.loads(input_path.read_text(encoding="utf-8"))
    decision_bars = {
        entry.position_id: entry.decision_bar_timestamp for entry in load_real_entries(database_path.resolve())
    }
    rows: list[dict[str, Any]] = []
    taxonomy_counts: Counter[str] = Counter()
    floor_counts: Counter[str] = Counter()
    for source in parity.get("rows", []):
        if source.get("status") != "OK":
            continue
        entry_price = decimal(source.get("entry_price"))
        quantity = decimal(source.get("quantity"))
        runtime_stop = decimal(source.get("runtime_initial_stop"))
        risk_per_unit = abs(entry_price - runtime_stop)
        entry_time = parse_datetime(source["entry_time"])
        exit_time = parse_datetime(source["r3_actual_exchange"]["exit_time"])
        decision_bar = decision_bars.get(source["position_id"])
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
        exit_reason = str(source["r3_actual_exchange"].get("exit_reasons", ["UNKNOWN"])[0])
        taxonomy = classify_taxonomy(
            exit_reason=exit_reason,
            floor_source=str(floor.get("source")),
            recovery=recovery,
        )
        taxonomy_counts[taxonomy["primary"]] += 1
        floor_counts[str(floor.get("source"))] += 1
        rows.append(
            {
                "position_id": source["position_id"],
                "symbol": source["symbol"],
                "direction": source["direction"],
                "entry_time": source["entry_time"],
                "exit_time": source["r3_actual_exchange"]["exit_time"],
                "entry_price": str(entry_price),
                "quantity": str(quantity),
                "exit_reason": exit_reason,
                "decision_bar": decision_bar.isoformat() if decision_bar is not None else None,
                "realized_net_pnl_usdt": source["r3_actual_exchange"].get("net_pnl_usdt"),
                "initial_stop": str(runtime_stop),
                "initial_target": source.get("runtime_initial_target"),
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
        "source_parity_audit": str(input_path.relative_to(ROOT)),
        "database": str(database_path.relative_to(ROOT)),
        "episode_count": len(rows),
        "taxonomy_counts": dict(sorted(taxonomy_counts.items())),
        "stop_floor_counts": dict(sorted(floor_counts.items())),
        "requested_sample_check": _sample_matches(rows),
        "limitations": [
            "The current SQLite cohort is authoritative only for the rows present in the parity audit.",
            "Recovery is classified only when 1m bars cover the full four-hour horizon.",
            "The 0.35% floor is evidence of geometry source, not evidence that widening it is superior.",
            "No replay variant was deployed or armed from this report.",
        ],
        "episodes": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--markdown", type=Path, default=DEFAULT_MARKDOWN)
    args = parser.parse_args()
    report = build_report(input_path=args.input, database_path=args.database)
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
