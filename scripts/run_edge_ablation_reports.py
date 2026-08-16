"""Run the two requested research-only mechanism ablations."""

from __future__ import annotations

import argparse
import inspect
import json
import sqlite3
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from services.automated_trading.application.decision_service import evaluate_sampling_signal
from services.strategy_library.event_edge import (
    EventBar,
    build_candidate_event_dataset,
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


def _windows() -> tuple[Any, ...]:
    return build_proposal_walk_forward_windows(
        development_start=datetime(2023, 1, 29, tzinfo=UTC),
        development_end=HOLDOUT_START,
        window_count=8,
        train_months=12,
        oos_months=3,
        max_lookback_bars=72,
        max_holding_bars=96,
        bar_interval=timedelta(minutes=15),
        embargo=timedelta(hours=24),
    )


def _candidate_metrics(candidate_id: str, weights: tuple[float, float, float]) -> dict[str, Any]:
    windows = _windows()
    with sqlite3.connect(DB_PATH) as connection:
        events = tuple(
            event
            for symbol in SYMBOLS
            for event in build_candidate_event_dataset(
                candidate_id=candidate_id,
                symbol=symbol,
                bars15=_bars(connection, symbol, "15m"),
                bars1h=_bars(connection, symbol, "1h"),
                bars4h=_bars(connection, symbol, "4h"),
                development_end=HOLDOUT_START,
                direction_weights=weights,
            )
        )
    rows = []
    for window in windows:
        train = tuple(event for event in events if window.train_start <= event.event_time < window.train_end)
        oos = tuple(
            event
            for event in events
            if window.oos_start <= event.event_time < window.oos_end and event.outcome_time < window.oos_end
        )
        gate, train_metrics = discover_quality_gate(train, event_types=("ANY", candidate_id))
        rows.append(
            {
                "window_id": window.window_id,
                "train_events": len(train),
                "oos_events": len(oos),
                "train": asdict(train_metrics),
                "oos_all_events": asdict(gate_metrics(oos)),
                "gate": asdict(gate) if gate else None,
                "oos_gated": asdict(gate_metrics(oos, gate)) if gate else asdict(gate_metrics(())),
            }
        )
    return {
        "candidate_id": candidate_id,
        "direction_weights_15m_1h_4h": weights,
        "events": len(events),
        "windows": rows,
        "holdout_accessed": False,
    }


def run_regime(output: Path) -> None:
    variants = {
        "legacy_reported_bug": (0.50, 0.30, 0.20),
        "current_code": (0.20, 0.35, 0.45),
        "requested_4h_dominant": (0.20, 0.30, 0.50),
    }
    report = {
        "status": "READ_ONLY_ABLATION",
        "ablation": "RegimeScorerV2 direction weights",
        "holdout_start": HOLDOUT_START.isoformat(),
        "holdout_accessed": False,
        "variants": {
            name: {candidate: _candidate_metrics(candidate, weights) for candidate in ("failed_breakout_reversal_v1", "range_sweep_reversion_v1")}
            for name, weights in variants.items()
        },
        "trend_candidate_note": "The EventEdge continuation builder has no RegimeScorerV2 dependency; no trend-arm delta is claimed here.",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def run_bollinger(output: Path) -> None:
    source = inspect.getsource(evaluate_sampling_signal)
    report = {
        "status": "NOT_APPLICABLE_ACTIVE_V2_PATH",
        "ablation": "remove Bollinger entry signal",
        "holdout_start": HOLDOUT_START.isoformat(),
        "holdout_accessed": False,
        "active_v2_signal_path": "services/automated_trading/application/decision_service.py:evaluate_sampling_signal",
        "bollinger_referenced_by_active_v2_signal": "bollinger" in source.lower(),
        "with_bollinger": {"trades": None, "expectancy": None, "note": "signal function has no Bollinger branch"},
        "without_bollinger": {"trades": None, "expectancy": None, "note": "identical to active path; no Bollinger signal to remove"},
        "evidence": "[事实] active V2 sampling evaluator only computes EMA50, MACD histogram, RSI14 and ATR14; the Bollinger branch exists in legacy services/execution/decision_pipeline.py, not this frozen V2 lane.",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kind", choices=("regime", "bollinger"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.kind == "regime":
        run_regime(args.output)
    else:
        run_bollinger(args.output)
    print(json.dumps({"kind": args.kind, "output": str(args.output), "holdout_accessed": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
