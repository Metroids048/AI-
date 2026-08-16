"""Run the sealed-holdout-safe Event -> Outcome -> Quality Gate experiment."""

from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from services.strategy_library.event_edge import (
    EdgeEvent,
    EventBar,
    GateMetrics,
    QualityGate,
    build_candidate_event_dataset,
    build_event_dataset,
    discover_quality_gate,
    gate_metrics,
)
from services.validation.proposal_walk_forward import build_proposal_walk_forward_windows

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / ".strategy_refactor_history.db"
HOLDOUT_START = datetime(2026, 1, 29, tzinfo=UTC)
SYMBOLS = ("BTC/USDT", "ETH/USDT")


def _dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _bars(connection: sqlite3.Connection, symbol: str, timeframe: str) -> tuple[EventBar, ...]:
    rows = connection.execute(
        "SELECT time, open, high, low, close, volume FROM ohlcv_bars WHERE symbol=? AND timeframe=? AND time < ? ORDER BY time",
        (symbol, timeframe, HOLDOUT_START.replace(tzinfo=None).isoformat(sep=" ")),
    ).fetchall()
    return tuple(EventBar(_dt(str(row[0])), *(float(value) for value in row[1:])) for row in rows)


def _metric_record(metrics: GateMetrics) -> dict[str, float | int]:
    return dict(asdict(metrics))


def run() -> dict[str, Any]:
    development_start = datetime(2023, 1, 29, tzinfo=UTC)
    windows = build_proposal_walk_forward_windows(
        development_start=development_start,
        development_end=HOLDOUT_START,
        window_count=8,
        train_months=12,
        oos_months=3,
        max_lookback_bars=72,
        max_holding_bars=96,
        bar_interval=timedelta(minutes=15),
        embargo=timedelta(hours=24),
    )
    with sqlite3.connect(DB_PATH) as connection:
        datasets = {
            symbol: build_event_dataset(
                symbol=symbol,
                bars15=_bars(connection, symbol, "15m"),
                bars1h=_bars(connection, symbol, "1h"),
                bars4h=_bars(connection, symbol, "4h"),
                development_end=HOLDOUT_START,
            )
            for symbol in SYMBOLS
        }
    all_events = tuple(
        event for events in datasets.values() for event in events if event.event_time >= development_start
    )
    windows_report: list[dict[str, Any]] = []
    for window in windows:
        train = tuple(event for event in all_events if window.train_start <= event.event_time < window.train_end)
        oos = tuple(
            event
            for event in all_events
            if window.oos_start <= event.event_time < window.oos_end and event.outcome_time < window.oos_end
        )
        gate, train_metrics = discover_quality_gate(train)
        oos_metrics = gate_metrics(oos, gate) if gate else gate_metrics(())
        windows_report.append(
            {
                "window_id": window.window_id,
                "train_events": len(train),
                "oos_events": len(oos),
                "gate": asdict(gate) if gate else None,
                "train": _metric_record(train_metrics),
                "oos": _metric_record(oos_metrics),
                "cost_stress": {
                    str(mult): _metric_record(gate_metrics(oos, gate, cost_multiple=mult) if gate else gate_metrics(()))
                    for mult in (1.0, 1.5, 2.0)
                },
            }
        )
    # Aggregate only the selected events from each OOS gate, preserving per-window freezing.
    selected: list[EdgeEvent] = []
    windows_by_id = {window.window_id: window for window in windows}
    for row in windows_report:
        if not row["gate"]:
            continue
        window = windows_by_id[row["window_id"]]
        gate = QualityGate(**row["gate"])
        selected.extend(
            event
            for event in all_events
            if window.oos_start <= event.event_time < window.oos_end
            and event.outcome_time < window.oos_end
            and gate.accepts(event)
        )
    aggregate = gate_metrics(selected)
    passed = (
        aggregate.trades >= 80
        and aggregate.win_rate >= 0.55
        and aggregate.payoff >= 1.15
        and aggregate.profit_factor >= 1.40
        and aggregate.expectancy >= 0.10
        and aggregate.expectancy_lcb95 > 0
        and aggregate.max_drawdown_pct <= 0.20
        and sum(row["oos"]["expectancy"] > 0 for row in windows_report) >= 6
        and all(
            row["cost_stress"]["1.5"]["expectancy"] > 0
            and row["cost_stress"]["1.5"]["profit_factor"] > 1.10
            and row["cost_stress"]["2.0"]["expectancy"] > 0
            and row["cost_stress"]["2.0"]["profit_factor"] > 1.10
            for row in windows_report
            if row["oos"]["trades"]
        )
    )
    return {
        "status": "NEW_STRATEGY_OOS_PASS" if passed else "STRATEGY_EDGE_NOT_FOUND",
        "holdout_start": HOLDOUT_START.isoformat(),
        "holdout_accessed": False,
        "event_definitions": ["HTF_STRUCTURE_BREAK", "HTF_BREAK_RETEST"],
        "symbols": SYMBOLS,
        "events": len(all_events),
        "aggregate_oos": _metric_record(aggregate),
        "positive_windows": sum(row["oos"]["expectancy"] > 0 for row in windows_report),
        "windows": windows_report,
        "dataset": [asdict(event) for event in all_events],
    }


def run_candidate(candidate_id: str) -> dict[str, Any]:
    """Run one research-only reversal candidate through the frozen gate."""
    development_start = datetime(2023, 1, 29, tzinfo=UTC)
    windows = build_proposal_walk_forward_windows(
        development_start=development_start,
        development_end=HOLDOUT_START,
        window_count=8,
        train_months=12,
        oos_months=3,
        max_lookback_bars=72,
        max_holding_bars=96,
        bar_interval=timedelta(minutes=15),
        embargo=timedelta(hours=24),
    )
    with sqlite3.connect(DB_PATH) as connection:
        datasets = {
            symbol: build_candidate_event_dataset(
                candidate_id=candidate_id,
                symbol=symbol,
                bars15=_bars(connection, symbol, "15m"),
                bars1h=_bars(connection, symbol, "1h"),
                bars4h=_bars(connection, symbol, "4h"),
                development_end=HOLDOUT_START,
                max_holding_bars=96,
            )
            for symbol in SYMBOLS
        }
    all_events = tuple(
        event for events in datasets.values() for event in events if event.event_time >= development_start
    )
    windows_report: list[dict[str, Any]] = []
    for window in windows:
        train = tuple(event for event in all_events if window.train_start <= event.event_time < window.train_end)
        oos = tuple(
            event
            for event in all_events
            if window.oos_start <= event.event_time < window.oos_end and event.outcome_time < window.oos_end
        )
        gate, train_metrics = discover_quality_gate(train, event_types=("ANY", candidate_id))
        oos_metrics = gate_metrics(oos, gate) if gate else gate_metrics(())
        windows_report.append(
            {
                "window_id": window.window_id,
                "train_events": len(train),
                "oos_events": len(oos),
                "gate": asdict(gate) if gate else None,
                "train": _metric_record(train_metrics),
                "oos": _metric_record(oos_metrics),
                "cost_stress": {
                    str(mult): _metric_record(gate_metrics(oos, gate, cost_multiple=mult) if gate else gate_metrics(()))
                    for mult in (1.0, 1.5, 2.0)
                },
            }
        )
    selected: list[EdgeEvent] = []
    windows_by_id = {window.window_id: window for window in windows}
    for row in windows_report:
        if not row["gate"]:
            continue
        window = windows_by_id[row["window_id"]]
        gate = QualityGate(**row["gate"])
        selected.extend(
            event
            for event in all_events
            if window.oos_start <= event.event_time < window.oos_end
            and event.outcome_time < window.oos_end
            and gate.accepts(event)
        )
    aggregate = gate_metrics(selected)
    if len(all_events) < 30:
        conclusion = "INSUFFICIENT_DATA"
    elif (
        aggregate.profit_factor >= 1.40
        and aggregate.expectancy >= 0.10
        and aggregate.expectancy_lcb95 > 0
    ):
        conclusion = "ACCEPTED_FOR_SHADOW_PROMOTION"
    else:
        conclusion = "REJECTED_WITH_EVIDENCE"
    return {
        "status": conclusion,
        "candidate_id": candidate_id,
        "holdout_start": HOLDOUT_START.isoformat(),
        "holdout_accessed": False,
        "window_parameters": {
            "window_count": 8,
            "train_months": 12,
            "oos_months": 3,
            "max_lookback_bars": 72,
            "max_holding_bars": 96,
            "bar_interval": "15m",
            "embargo": "24h",
        },
        "gate_thresholds": {
            "min_trades": 30,
            "profit_factor": 1.40,
            "expectancy_r": 0.10,
            "expectancy_lcb95": ">0",
        },
        "geometry_mapping": (
            "single_target_proxy=failed_breakout 2R runner; range_sweep opposite boundary; "
            "candidate ladder is not promoted by this adapter"
        ),
        "events": len(all_events),
        "aggregate_oos": _metric_record(aggregate),
        "positive_windows": sum(row["oos"]["expectancy"] > 0 for row in windows_report),
        "windows": windows_report,
        "dataset": [asdict(event) for event in all_events],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--candidate",
        choices=("baseline", "failed_breakout_reversal_v1", "range_sweep_reversion_v1"),
        default="baseline",
    )
    parser.add_argument(
        "--output", type=Path, default=ROOT / "artifacts/trading_audit/reports/edge_first_event_research.json"
    )
    parser.add_argument(
        "--dataset-output", type=Path, default=ROOT / "artifacts/trading_audit/canonical/edge_events.jsonl"
    )
    args = parser.parse_args()
    report = run() if args.candidate == "baseline" else run_candidate(args.candidate)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps({key: value for key, value in report.items() if key != "dataset"}, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    args.dataset_output.parent.mkdir(parents=True, exist_ok=True)
    args.dataset_output.write_text(
        "\n".join(json.dumps(row, default=str) for row in report["dataset"]) + "\n", encoding="utf-8"
    )
    print(json.dumps({key: value for key, value in report.items() if key != "dataset"}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
