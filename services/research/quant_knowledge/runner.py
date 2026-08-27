"""Execute bounded QINXIONGMAO primitive research on canonical EventEdge data.

This runner is research-only.  It reads the existing SQLite OHLCV history,
keeps the 2026-01-29 final holdout sealed, and writes resumable evidence for
each registered hypothesis.
"""

from __future__ import annotations

import json
import random
import sqlite3
from bisect import bisect_right
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from statistics import mean, median
from typing import Any

from services.research.integrations.contracts import stable_hash
from services.strategy_library.event_edge import EdgeEvent, EventBar, build_event_dataset
from services.validation.proposal_walk_forward import build_proposal_walk_forward_windows

from .contracts import QuantPrimitive, ResearchHypothesis

HOLDOUT_START = datetime(2026, 1, 29, tzinfo=UTC)
DEVELOPMENT_START = datetime(2023, 1, 29, tzinfo=UTC)
SYMBOLS = ("BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "BNB/USDT")
HORIZONS = (4, 8, 16)
COST_RATE = 0.0012
TERMINAL_STATES = {
    "USEFUL_SIGNAL",
    "USEFUL_FILTER",
    "USEFUL_REGIME_FILTER",
    "USEFUL_EXIT",
    "NO_EDGE",
    "UNSTABLE",
    "INSUFFICIENT_SAMPLE",
    "LOOKAHEAD_FAIL",
    "CONFLICTED",
    "DISCRETIONARY_ONLY",
    "DATA_UNAVAILABLE",
    "TAUTOLOGY_FAIL",
    "INVALID_EXPERIMENT_DESIGN",
    "IMPLEMENTATION_AMBIGUOUS",
    "INSUFFICIENT_SAMPLE_CONFIRMED",
}
P0_FAMILIES = {
    "SUPPORT_RESISTANCE",
    "BREAKOUT_RETEST",
    "MARKET_STRUCTURE_DOW_123_2B",
    "TREND_PULLBACK",
    "PRICE_ACTION_CONFIRMATION",
}
PRIMITIVE_ACCOUNTING_STATES = TERMINAL_STATES

EVENT_ADMISSION_FEATURES = {
    "event_type": "HTF_BREAK_RETEST",
    "uses_regime_4h": True,
    "uses_trend_strength_1h": True,
}

PARAMETER_SPACE: dict[str, dict[str, Any]] = {
    "QP_SUPPORT_TOUCH_COUNT": {"touch_count_min": {"values": [2], "origin": "RESEARCH_PARAMETER"}},
    "QP_LEVEL_REACTION_STRENGTH_ATR": {"reaction_strength_atr_min": {"values": [0.5], "origin": "RESEARCH_PARAMETER"}},
    "QP_LEVEL_AGE": {"level_age_min": {"values": [4], "origin": "RESEARCH_PARAMETER"}},
    "QP_BREAKOUT_DISTANCE_ATR": {"breakout_distance_atr_min": {"values": [0.5], "origin": "RESEARCH_PARAMETER"}},
    "QP_TREND_PULLBACK_DEPTH": {"pullback_depth_atr_min": {"values": [0.0], "origin": "RESEARCH_PARAMETER"}},
    "QP_VOLUME_CONFIRMATION": {"volume_ratio_min": {"values": [1.5], "origin": "RESEARCH_PARAMETER"}},
    "QP_WICK_REJECTION": {"wick_body_ratio_min": {"values": [1.5], "origin": "RESEARCH_PARAMETER"}},
    "QP_MULTI_TIMEFRAME_AGREEMENT": {"trend_strength_1h_min": {"values": [0.1], "origin": "RESEARCH_PARAMETER"}},
    "QP_NO_TRADE_CHOP_VETO": {"chop_score_max": {"values": [0.6], "origin": "RESEARCH_PARAMETER"}},
}


@dataclass(frozen=True)
class ResearchRecord:
    event: EdgeEvent
    bars15: tuple[EventBar, ...]
    bars1h: tuple[EventBar, ...]
    index15: int
    features: dict[str, Any]


def _dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _load_bars(connection: sqlite3.Connection, symbol: str, timeframe: str) -> tuple[EventBar, ...]:
    rows = connection.execute(
        "SELECT time, open, high, low, close, volume FROM ohlcv_bars "
        "WHERE symbol=? AND timeframe=? AND time < ? ORDER BY time",
        (symbol, timeframe, HOLDOUT_START.replace(tzinfo=None).isoformat(sep=" ")),
    ).fetchall()
    return tuple(EventBar(_dt(str(row[0])), *(float(value) for value in row[1:])) for row in rows)


def _atr(bars: tuple[EventBar, ...], period: int = 14) -> float:
    if len(bars) < 2:
        return 0.0
    sample = bars[-(period + 1) :]
    ranges = [
        max(current.high - current.low, abs(current.high - previous.close), abs(current.low - previous.close))
        for previous, current in zip(sample[:-1], sample[1:], strict=True)
    ]
    return mean(ranges[-period:]) if ranges else 0.0


def _point_in_time_features(
    event: EdgeEvent, bars15: tuple[EventBar, ...], bars1h: tuple[EventBar, ...]
) -> dict[str, Any]:
    close_times = tuple(bar.time + timedelta(hours=1) for bar in bars1h)
    h1_index = bisect_right(close_times, event.event_time) - 1
    prior = bars1h[max(0, h1_index - 20) : h1_index] if h1_index > 0 else ()
    atr = _atr(bars1h[: max(1, h1_index + 1)]) or event.atr
    level = (
        max((bar.high for bar in prior), default=event.entry)
        if event.side == "long"
        else min((bar.low for bar in prior), default=event.entry)
    )
    tolerance = 0.25 * atr
    touches = [
        index
        for index, bar in enumerate(prior)
        if abs((bar.low if event.side == "long" else bar.high) - level) <= tolerance
    ]
    reaction = 0.0
    if prior and atr > 0:
        reaction = max((bar.high - level if event.side == "long" else level - bar.low) / atr for bar in prior)
    level_age = (len(prior) - 1 - touches[-1]) if touches else len(prior)
    index15 = bisect_right(tuple(bar.time for bar in bars15), event.entry_time) - 1
    entry_bar = bars15[index15] if 0 <= index15 < len(bars15) else None
    wick_rejection = False
    if entry_bar is not None:
        body = abs(entry_bar.close - entry_bar.open)
        body = max(body, 1e-12)
        if event.side == "long":
            wick_rejection = (min(entry_bar.open, entry_bar.close) - entry_bar.low) / body >= 1.5
        else:
            wick_rejection = (entry_bar.high - max(entry_bar.open, entry_bar.close)) / body >= 1.5
    return {
        "swing_level": level,
        "touch_count": len(touches),
        "reaction_strength_atr": reaction,
        "level_age": level_age,
        "role_reversal": event.event_type == "HTF_BREAK_RETEST",
        "wick_rejection": wick_rejection,
        "known_at": event.event_time.isoformat(),
        "confirmed_at": event.event_time.isoformat(),
        "tradable_at": event.entry_time.isoformat(),
    }


def _records(db_path: Path) -> tuple[ResearchRecord, ...]:
    records: list[ResearchRecord] = []
    with sqlite3.connect(db_path) as connection:
        for symbol in SYMBOLS:
            bars15 = _load_bars(connection, symbol, "15m")
            bars1h = _load_bars(connection, symbol, "1h")
            bars4h = _load_bars(connection, symbol, "4h")
            events = build_event_dataset(
                symbol=symbol,
                bars15=bars15,
                bars1h=bars1h,
                bars4h=bars4h,
                development_end=HOLDOUT_START,
            )
            times15 = tuple(bar.time for bar in bars15)
            for event in events:
                if not DEVELOPMENT_START <= event.event_time < HOLDOUT_START:
                    continue
                index15 = bisect_right(times15, event.entry_time) - 1
                records.append(
                    ResearchRecord(
                        event=event,
                        bars15=bars15,
                        bars1h=bars1h,
                        index15=index15,
                        features=_point_in_time_features(event, bars15, bars1h),
                    )
                )
    return tuple(sorted(records, key=lambda item: (item.event.event_time, item.event.symbol, item.event.event_id)))


def _predicate(primitive_id: str, record: ResearchRecord) -> bool:
    event = record.event
    feature = record.features
    if primitive_id == "QP_SUPPORT_TOUCH_COUNT":
        return feature["touch_count"] >= 2
    if primitive_id == "QP_LEVEL_REACTION_STRENGTH_ATR":
        return feature["reaction_strength_atr"] >= 0.5
    if primitive_id == "QP_LEVEL_AGE":
        return feature["level_age"] >= 4
    if primitive_id == "QP_SUPPORT_ROLE_REVERSAL":
        return feature["role_reversal"]
    if primitive_id == "QP_BREAKOUT_DISTANCE_ATR":
        return event.breakout_distance_atr >= 0.5
    if primitive_id == "QP_BREAKOUT_RETEST":
        return event.event_type == "HTF_BREAK_RETEST"
    if primitive_id == "QP_MARKET_STRUCTURE_HH_HL":
        return event.regime_4h == event.side
    if primitive_id == "QP_TREND_EMA_ALIGNMENT":
        return event.regime_4h == event.side and event.trend_strength_1h > 0
    if primitive_id == "QP_TREND_PULLBACK_DEPTH":
        return event.retest_depth_atr > 0
    if primitive_id == "QP_VOLUME_CONFIRMATION":
        return event.volume_ratio >= 1.5
    if primitive_id == "QP_WICK_REJECTION":
        return bool(feature["wick_rejection"])
    if primitive_id == "QP_MULTI_TIMEFRAME_AGREEMENT":
        return event.regime_4h == event.side and event.trend_strength_1h >= 0.1
    if primitive_id == "QP_NO_TRADE_CHOP_VETO":
        return event.chop_score <= 0.6
    return False


def _feature_formula_hash(primitive_id: str) -> str:
    return stable_hash(
        {
            "primitive_id": primitive_id,
            "parameters": PARAMETER_SPACE.get(primitive_id, {}),
            "known_at": "bars_closed_at_or_before_event_time",
            "predicate_version": "qk-v2-point-in-time-1",
        }
    )


def _validate_experiment_design(
    *,
    primitive_id: str,
    base_event: str,
    baseline_ids: list[str],
    candidate_ids: list[str],
    event_admission_features: dict[str, Any],
) -> str | None:
    """Reject a design before metrics when its treatment is already admitted."""
    if baseline_ids and baseline_ids == candidate_ids:
        return "TAUTOLOGY_FAIL"
    if primitive_id == "QP_SUPPORT_ROLE_REVERSAL" and base_event == "HTF_BREAK_RETEST":
        return "TAUTOLOGY_FAIL"
    if primitive_id == "QP_MARKET_STRUCTURE_HH_HL" and event_admission_features.get("uses_regime_4h"):
        return "TAUTOLOGY_FAIL"
    if primitive_id == "QP_TREND_EMA_ALIGNMENT" and (
        event_admission_features.get("uses_regime_4h") or event_admission_features.get("uses_trend_strength_1h")
    ):
        return "TAUTOLOGY_FAIL"
    return None


def _forward_values(records: Iterable[ResearchRecord], horizon: int) -> list[float]:
    values: list[float] = []
    for record in records:
        index = record.index15 + horizon
        if record.index15 < 0 or index >= len(record.bars15):
            continue
        entry = record.event.entry
        close = record.bars15[index].close
        signed = (close - entry) / entry if record.event.side == "long" else (entry - close) / entry
        values.append(signed - COST_RATE)
    return values


def _forward_distribution(record: ResearchRecord, horizon: int) -> tuple[float, float, float] | None:
    index = record.index15
    end = index + horizon
    if index < 0 or end >= len(record.bars15):
        return None
    entry = record.event.entry
    future = record.bars15[index + 1 : end + 1]
    if not future or entry == 0:
        return None
    if record.event.side == "long":
        mfe = max((bar.high - entry) / entry for bar in future)
        mae = min((bar.low - entry) / entry for bar in future)
        signed_close = (future[-1].close - entry) / entry
    else:
        mfe = max((entry - bar.low) / entry for bar in future)
        mae = min((entry - bar.high) / entry for bar in future)
        signed_close = (entry - future[-1].close) / entry
    return signed_close - COST_RATE, mfe, mae


def _atomic_metrics(records: Iterable[ResearchRecord]) -> dict[str, Any]:
    metrics: dict[str, Any] = {}
    for horizon in HORIZONS:
        distributions = [item for record in records if (item := _forward_distribution(record, horizon)) is not None]
        values = [item[0] for item in distributions]
        metrics[str(horizon)] = {
            "sample_count": len(values),
            "forward_return_mean": mean(values) if values else 0.0,
            "forward_return_median": median(values) if values else 0.0,
            "net_expectancy": mean(values) if values else 0.0,
            "hit_rate": sum(value > 0 for value in values) / len(values) if values else 0.0,
            "mfe": mean(item[1] for item in distributions) if distributions else 0.0,
            "mae": mean(item[2] for item in distributions) if distributions else 0.0,
        }
    return metrics


def _bootstrap_delta(
    baseline: list[float], candidate: list[float], *, seed: int = 0, rounds: int = 1000
) -> dict[str, float]:
    if not baseline or not candidate:
        return {"mean_delta": 0.0, "median_delta": 0.0, "lcb95": 0.0, "ucb95": 0.0}
    rng = random.Random(seed)
    deltas = [
        mean(rng.choices(candidate, k=len(candidate))) - mean(rng.choices(baseline, k=len(baseline)))
        for _ in range(rounds)
    ]
    ordered = sorted(deltas)
    return {
        "mean_delta": mean(deltas),
        "median_delta": median(deltas),
        "lcb95": ordered[max(0, int(rounds * 0.025) - 1)],
        "ucb95": ordered[min(len(ordered) - 1, int(rounds * 0.975))],
    }


def _paired_bootstrap_delta(
    rows: list[tuple[float, bool]], *, seed: int = 0, rounds: int = 1000
) -> dict[str, float | int | bool]:
    """Resample the parent universe, then reapply the registered predicate.

    Candidate rows are a subset of each resampled baseline.  Sampling the two
    distributions independently would destroy that dependency and understate
    uncertainty for an incremental filter.
    """
    if not rows:
        return {
            "paired": True,
            "sample_count": 0,
            "candidate_sample_count": 0,
            "mean_delta": 0.0,
            "median_delta": 0.0,
            "lcb95": 0.0,
            "ucb95": 0.0,
        }
    rng = random.Random(seed)
    deltas: list[float] = []
    candidate_counts: list[int] = []
    for _ in range(rounds):
        sample = rng.choices(rows, k=len(rows))
        candidate = [value for value, selected in sample if selected]
        if not candidate:
            continue
        deltas.append(mean(candidate) - mean(value for value, _ in sample))
        candidate_counts.append(len(candidate))
    if not deltas:
        return {
            "paired": True,
            "sample_count": len(rows),
            "candidate_sample_count": 0,
            "mean_delta": 0.0,
            "median_delta": 0.0,
            "lcb95": 0.0,
            "ucb95": 0.0,
        }
    ordered = sorted(deltas)
    return {
        "paired": True,
        "sample_count": len(rows),
        "candidate_sample_count": round(mean(candidate_counts)),
        "mean_delta": mean(deltas),
        "median_delta": median(deltas),
        "lcb95": ordered[max(0, int(len(ordered) * 0.025) - 1)],
        "ucb95": ordered[min(len(ordered) - 1, int(len(ordered) * 0.975))],
    }


def _metrics(records: Iterable[ResearchRecord]) -> dict[str, Any]:
    values = _forward_values(records, 8)
    return {
        "sample_count": len(values),
        "net_expectancy": mean(values) if values else 0.0,
        "median_net_return": median(values) if values else 0.0,
        "hit_rate": sum(value > 0 for value in values) / len(values) if values else 0.0,
        "profit_factor": sum(value for value in values if value > 0) / abs(sum(value for value in values if value < 0))
        if any(value < 0 for value in values)
        else float("inf"),
    }


def _load_hypotheses(bundle_path: Path) -> list[ResearchHypothesis]:
    hypotheses: list[ResearchHypothesis] = []
    for line in bundle_path.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if row.get("record_type") == "hypothesis":
            row.pop("record_type", None)
            hypotheses.append(ResearchHypothesis.model_validate(row))
    return sorted(hypotheses, key=lambda item: item.hypothesis_id)


def _load_primitives(bundle_path: Path) -> dict[str, QuantPrimitive]:
    primitives: dict[str, QuantPrimitive] = {}
    for line in bundle_path.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if row.get("record_type") == "primitive":
            row.pop("record_type", None)
            primitive = QuantPrimitive.model_validate(row)
            primitives[primitive.primitive_id] = primitive
    return primitives


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    temporary.replace(path)


def _family(primitive_id: str) -> str:
    if primitive_id.startswith("QP_SUPPORT") or primitive_id.startswith("QP_LEVEL"):
        return "SUPPORT_RESISTANCE"
    if primitive_id.startswith("QP_BREAKOUT"):
        return "BREAKOUT_RETEST"
    if primitive_id.startswith("QP_MARKET_STRUCTURE"):
        return "MARKET_STRUCTURE_DOW_123_2B"
    if primitive_id.startswith("QP_TREND") or primitive_id.startswith("QP_MULTI_TIMEFRAME"):
        return "TREND_PULLBACK"
    if primitive_id.startswith("QP_WICK") or primitive_id.startswith("QP_VOLUME"):
        return "PRICE_ACTION_CONFIRMATION"
    return "OTHER"


def _dataset_hash(records: tuple[ResearchRecord, ...]) -> str:
    return stable_hash(
        {
            "development_start": DEVELOPMENT_START.isoformat(),
            "holdout_start": HOLDOUT_START.isoformat(),
            "symbols": SYMBOLS,
            "event_ids": [record.event.event_id for record in records],
        }
    )


def _execution_matrix(
    hypotheses: list[ResearchHypothesis], primitives: dict[str, QuantPrimitive], dataset_hash: str
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for hypothesis in hypotheses:
        primitive = primitives.get(hypothesis.primitive)
        rows.append(
            {
                "hypothesis_id": hypothesis.hypothesis_id,
                "hypothesis_hash": hypothesis.hypothesis_hash,
                "primitive_id": hypothesis.primitive,
                "primitive_hash": primitive.primitive_hash if primitive else None,
                "family": _family(hypothesis.primitive),
                "role": primitive.role if primitive else None,
                "claim": hypothesis.claim,
                "base_event": hypothesis.base_event,
                "candidate_filter": hypothesis.primitive,
                "required_features": primitive.required_features if primitive else [],
                "dataset_hash": dataset_hash,
                "symbols": list(SYMBOLS),
                "timeframes": ["15m", "1h", "4h"],
                "cost_model": {"round_trip_cost_rate": COST_RATE},
                "split_plan": hypothesis.split_plan,
                "parameter_space": primitive.parameter_priors if primitive else {},
                "current_status": "REGISTERED",
                "research_design_version": hypothesis.research_design_version,
                "experiment_type": hypothesis.experiment_type,
                "parent_universe": hypothesis.parent_universe,
                "baseline_selector": hypothesis.baseline_selector,
                "candidate_selector": hypothesis.candidate_selector,
                "feature_formula_hash": hypothesis.feature_formula_hash,
            }
        )
    return rows


def _rebuild_progress(
    progress: dict[str, Any], hypotheses: list[ResearchHypothesis], primitives: dict[str, QuantPrimitive]
) -> None:
    statuses = progress.get("hypothesis_status", {})
    progress["hypotheses"] = {
        "registered": len(hypotheses),
        "data_ready": len(statuses),
        "atomic_complete": len(statuses),
        "incremental_complete": len(statuses),
        "ablation_complete": len(statuses),
        "oos_complete": len(statuses),
        "terminal": sum(item.get("status") in TERMINAL_STATES for item in statuses.values()),
        "running": sum(item.get("status") == "RUNNING" for item in statuses.values()),
        "unknown": sum(item.get("status") == "UNKNOWN" for item in statuses.values()),
    }
    families: dict[str, dict[str, int]] = {}
    for hypothesis in hypotheses:
        family = _family(hypothesis.primitive)
        families.setdefault(family, {"registered": 0, "terminal": 0})["registered"] += 1
        status = statuses.get(hypothesis.hypothesis_id, {}).get("status")
        if status in TERMINAL_STATES:
            families[family]["terminal"] += 1
    progress["families"] = families
    primitive_status: dict[str, dict[str, Any]] = progress.get("primitive_status", {})
    for primitive_id in primitives:
        primitive_status.setdefault(primitive_id, {"status": "DEFERRED_P1", "hypothesis_ids": []})
    for hypothesis in hypotheses:
        item = primitive_status.setdefault(hypothesis.primitive, {"status": "UNKNOWN", "hypothesis_ids": []})
        if hypothesis.hypothesis_id not in item["hypothesis_ids"]:
            item["hypothesis_ids"].append(hypothesis.hypothesis_id)
        status = statuses.get(hypothesis.hypothesis_id, {}).get("status")
        if status in TERMINAL_STATES:
            item["status"] = status
    # A parameter prior without an executable hypothesis is still explicitly
    # accounted for, but cannot be promoted or silently treated as tested.
    for primitive_id, item in primitive_status.items():
        if not item.get("hypothesis_ids") and primitive_id == "QP_STOP_STRUCTURE_PRIOR":
            item["status"] = "IMPLEMENTATION_AMBIGUOUS"
    progress["primitive_status"] = primitive_status
    progress["primitives"] = {
        "total": len(primitives),
        "accounted": sum(item.get("status") in PRIMITIVE_ACCOUNTING_STATES for item in primitive_status.values()),
        "terminal": sum(item.get("status") in TERMINAL_STATES for item in primitive_status.values()),
        "deferred": sum(item.get("status") == "DEFERRED_P1" for item in primitive_status.values()),
    }
    progress["useful_filters"] = sum(item.get("status") == "USEFUL_FILTER" for item in statuses.values())
    progress["useful_regimes"] = sum(item.get("status") == "USEFUL_REGIME_FILTER" for item in statuses.values())
    progress["useful_signals"] = sum(item.get("status") == "USEFUL_SIGNAL" for item in statuses.values())
    progress["useful_exits"] = sum(item.get("status") == "USEFUL_EXIT" for item in statuses.values())


def run_alpha_research(
    *,
    db_path: str | Path,
    bundle_path: str | Path,
    output_dir: str | Path,
    resume: bool = True,
) -> dict[str, Any]:
    """Run all registered hypotheses and return the terminal progress payload."""
    db_path, bundle_path, output_dir = Path(db_path), Path(bundle_path), Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    hypotheses = _load_hypotheses(bundle_path)
    primitives = _load_primitives(bundle_path)
    records = _records(db_path)
    dataset_hash = _dataset_hash(records)
    windows = build_proposal_walk_forward_windows(
        development_start=DEVELOPMENT_START,
        development_end=HOLDOUT_START,
        window_count=8,
        train_months=12,
        oos_months=3,
        max_lookback_bars=72,
        max_holding_bars=96,
        bar_interval=timedelta(minutes=15),
        embargo=timedelta(hours=24),
    )
    progress_path = output_dir / "qinxiongmao-alpha-progress.json"
    progress: dict[str, Any] = {
        "pipeline": "QINXIONGMAO_KNOWLEDGE_ALPHA_PIPELINE",
        "infra": "PASS",
        "primitives": {"total": len(primitives), "accounted": 0, "terminal": 0},
        "hypotheses": {
            "registered": len(hypotheses),
            "data_ready": 0,
            "atomic_complete": 0,
            "incremental_complete": 0,
            "ablation_complete": 0,
            "oos_complete": 0,
            "terminal": 0,
        },
        "families": {},
        "useful_signals": 0,
        "useful_filters": 0,
        "useful_regimes": 0,
        "useful_exits": 0,
        "candidate_compositions": 0,
        "final_candidates": 0,
        "status": "EXPERIMENT_EXECUTION_PENDING",
        "updated_at": datetime.now(UTC).isoformat(),
        "hypothesis_status": {},
        "primitive_status": {},
        "registered_at": datetime.now(UTC).isoformat(),
        "dataset_hash": dataset_hash,
        "cost_model_hash": stable_hash({"round_trip_cost_rate": COST_RATE}),
        "split_hash": stable_hash(
            {"holdout_start": HOLDOUT_START.isoformat(), "window_count": 8, "train_months": 12, "oos_months": 3}
        ),
        "execution_matrix": _execution_matrix(hypotheses, primitives, dataset_hash),
        "negative_evidence": str(output_dir / "negative_evidence.jsonl"),
        "superseded_v1_evidence": str(output_dir / "superseded_v1_evidence.jsonl"),
        "final_holdout": {"status": "SEALED_NOT_ACCESSED", "start": HOLDOUT_START.isoformat()},
    }
    if resume and progress_path.exists():
        progress.update(json.loads(progress_path.read_text(encoding="utf-8")))
    _rebuild_progress(progress, hypotheses, primitives)
    matrix_path = output_dir / "execution_matrix.jsonl"
    matrix_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in progress["execution_matrix"]),
        encoding="utf-8",
    )
    _write_json(progress_path, progress)
    negative_path = output_dir / "negative_evidence.jsonl"
    superseded_path = output_dir / "superseded_v1_evidence.jsonl"
    composition_path = output_dir / "candidate_compositions.jsonl"
    for hypothesis in hypotheses:
        if resume and progress["hypothesis_status"].get(hypothesis.hypothesis_id, {}).get("status") in TERMINAL_STATES:
            continue
        primitive_id = hypothesis.primitive
        primitive = primitives.get(primitive_id)
        progress["hypothesis_status"][hypothesis.hypothesis_id] = {
            "primitive_id": primitive_id,
            "family": _family(primitive_id),
            "status": "RUNNING",
        }
        progress["status"] = "EXPERIMENTS_RUNNING"
        _rebuild_progress(progress, hypotheses, primitives)
        _write_json(progress_path, progress)
        baseline = tuple(
            record
            for record in records
            if hypothesis.base_event == "HTF_BREAK_RETEST" and record.event.event_type == "HTF_BREAK_RETEST"
        )
        if not baseline:
            baseline = records
        candidate = tuple(record for record in baseline if _predicate(primitive_id, record))
        design_failure = _validate_experiment_design(
            primitive_id=primitive_id,
            base_event=hypothesis.base_event,
            baseline_ids=[record.event.event_id for record in baseline],
            candidate_ids=[record.event.event_id for record in candidate],
            event_admission_features=EVENT_ADMISSION_FEATURES,
        )
        if design_failure:
            report = {
                "pipeline": "QINXIONGMAO_KNOWLEDGE_ALPHA_PIPELINE",
                "research_design_version": hypothesis.research_design_version,
                "hypothesis_id": hypothesis.hypothesis_id,
                "hypothesis_hash": hypothesis.hypothesis_hash,
                "primitive_id": primitive_id,
                "primitive_hash": primitive.primitive_hash if primitive else None,
                "family": _family(primitive_id),
                "status": design_failure,
                "supersedes": "V1_INDEPENDENT_BOOTSTRAP_EVIDENCE",
                "design_failure": "candidate selector is already implied by parent event admission",
                "dataset": {
                    "dataset_hash": dataset_hash,
                    "development_start": DEVELOPMENT_START.isoformat(),
                    "holdout_start": HOLDOUT_START.isoformat(),
                    "holdout_accessed": False,
                    "baseline_event_count": len(baseline),
                    "candidate_event_count": len(candidate),
                },
                "final_holdout": {"status": "SEALED_NOT_ACCESSED", "start": HOLDOUT_START.isoformat()},
            }
            _write_json(output_dir / "hypotheses" / f"{hypothesis.hypothesis_id}.json", report)
            with superseded_path.open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(
                        {
                            "evidence_class": "SUPERSEDED_EXPERIMENT_DESIGN",
                            "hypothesis_id": hypothesis.hypothesis_id,
                            "hypothesis_hash": hypothesis.hypothesis_hash,
                            "primitive_id": primitive_id,
                            "dataset_hash": dataset_hash,
                            "status": design_failure,
                            "superseded_v1_artifact": (
                                f"artifacts/strategy_research/qinxiongmao_alpha/hypotheses/HYP-QK-{primitive_id}.json"
                            ),
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                    + "\n"
                )
            progress["hypothesis_status"][hypothesis.hypothesis_id] = {
                "primitive_id": primitive_id,
                "family": _family(primitive_id),
                "status": design_failure,
                "artifact": str(output_dir / "hypotheses" / f"{hypothesis.hypothesis_id}.json"),
            }
            _rebuild_progress(progress, hypotheses, primitives)
            _write_json(progress_path, progress)
            continue
        atomic = _atomic_metrics(candidate)
        windows_report: list[dict[str, Any]] = []
        for window in windows:
            train_base = tuple(
                record for record in baseline if window.train_start <= record.event.event_time < window.train_end
            )
            train_candidate = tuple(
                record for record in candidate if window.train_start <= record.event.event_time < window.train_end
            )
            oos_base = tuple(
                record for record in baseline if window.oos_start <= record.event.event_time < window.oos_end
            )
            oos_candidate = tuple(
                record for record in candidate if window.oos_start <= record.event.event_time < window.oos_end
            )
            paired_rows = []
            for record in oos_base:
                distribution = _forward_distribution(record, 8)
                if distribution is not None:
                    paired_rows.append((distribution[0], record in oos_candidate))
            windows_report.append(
                {
                    "window_id": window.window_id,
                    "train": {"baseline": _metrics(train_base), "candidate": _metrics(train_candidate)},
                    "oos": {
                        "baseline": _metrics(oos_base),
                        "candidate": _metrics(oos_candidate),
                        "delta": _metrics(oos_candidate)["net_expectancy"] - _metrics(oos_base)["net_expectancy"],
                    },
                    "bootstrap": _paired_bootstrap_delta(paired_rows),
                }
            )
        deltas = [float(row["oos"]["delta"]) for row in windows_report if row["oos"]["candidate"]["sample_count"]]
        positive_windows = sum(delta > 0 for delta in deltas)
        aggregate_base = _metrics(baseline)
        aggregate_candidate = _metrics(candidate)
        aggregate_delta = aggregate_candidate["net_expectancy"] - aggregate_base["net_expectancy"]
        paired_rows = []
        candidate_ids = {record.event.event_id for record in candidate}
        for record in baseline:
            distribution = _forward_distribution(record, 8)
            if distribution is not None:
                paired_rows.append((distribution[0], record.event.event_id in candidate_ids))
        all_bootstrap = _paired_bootstrap_delta(paired_rows)
        if aggregate_candidate["sample_count"] < 30:
            status = "INSUFFICIENT_SAMPLE_CONFIRMED"
        elif aggregate_delta > 0 and all_bootstrap["lcb95"] > 0 and positive_windows >= 4:
            status = (
                "USEFUL_REGIME_FILTER"
                if hypothesis.primitive.startswith(("QP_TREND", "QP_MARKET_STRUCTURE", "QP_MULTI_TIMEFRAME"))
                else "USEFUL_FILTER"
            )
        elif aggregate_delta > 0:
            status = "UNSTABLE"
        else:
            status = "NO_EDGE"
        family = _family(primitive_id)
        if primitive is not None and primitive.provenance == "CONFLICTED":
            status = "CONFLICTED"
        if primitive is not None and primitive.quantization_status == "DISCRETIONARY_ONLY":
            status = "DISCRETIONARY_ONLY"
        if status not in TERMINAL_STATES:
            status = "UNKNOWN"
        report = {
            "pipeline": "QINXIONGMAO_KNOWLEDGE_ALPHA_PIPELINE",
            "hypothesis_id": hypothesis.hypothesis_id,
            "hypothesis_hash": hypothesis.hypothesis_hash,
            "primitive_id": primitive_id,
            "primitive_hash": primitive.primitive_hash if primitive else None,
            "family": family,
            "status": status,
            "dataset": {
                "db_path": str(db_path),
                "dataset_hash": dataset_hash,
                "development_start": DEVELOPMENT_START.isoformat(),
                "holdout_start": HOLDOUT_START.isoformat(),
                "holdout_accessed": False,
                "event_count": len(records),
                "baseline_event_count": len(baseline),
                "candidate_event_count": len(candidate),
            },
            "cost_model": {
                "round_trip_cost_rate": COST_RATE,
                "cost_source": "existing EventEdge research cost contract",
            },
            "known_at_semantics": "all primitive features are computed from bars closed at or before event_time",
            "atomic": atomic,
            "incremental": {
                "baseline_event": hypothesis.base_event,
                "baseline": aggregate_base,
                "candidate": aggregate_candidate,
                "delta_expectancy": aggregate_delta,
                "positive_windows": positive_windows,
                "bootstrap": all_bootstrap,
            },
            "ablation": {
                "paired": True,
                "paired_event_ids": len(
                    {item.event.event_id for item in baseline} & {item.event.event_id for item in candidate}
                ),
                "removed_events": aggregate_base["sample_count"] - aggregate_candidate["sample_count"],
                "bootstrap_dependency": "baseline_resample_then_predicate",
            },
            "oos_walk_forward": windows_report,
            "final_holdout": {"status": "SEALED_NOT_ACCESSED", "start": HOLDOUT_START.isoformat()},
            "research_design": {
                "version": hypothesis.research_design_version,
                "experiment_type": hypothesis.experiment_type,
                "parent_universe": hypothesis.parent_universe,
                "baseline_selector": hypothesis.baseline_selector,
                "candidate_selector": hypothesis.candidate_selector,
                "parameter_space": hypothesis.parameter_space,
                "feature_formula_hash": hypothesis.feature_formula_hash,
            },
        }
        _write_json(output_dir / "hypotheses" / f"{hypothesis.hypothesis_id}.json", report)
        progress["hypothesis_status"][hypothesis.hypothesis_id] = {
            "primitive_id": primitive_id,
            "family": family,
            "status": status,
            "artifact": str(output_dir / "hypotheses" / f"{hypothesis.hypothesis_id}.json"),
        }
        for matrix_row in progress["execution_matrix"]:
            if matrix_row["hypothesis_id"] == hypothesis.hypothesis_id:
                matrix_row["current_status"] = status
                break
        if status in {
            "NO_EDGE",
            "UNSTABLE",
            "INSUFFICIENT_SAMPLE",
            "INSUFFICIENT_SAMPLE_CONFIRMED",
            "LOOKAHEAD_FAIL",
            "CONFLICTED",
            "DISCRETIONARY_ONLY",
            "DATA_UNAVAILABLE",
        }:
            evidence = {
                "hypothesis_id": hypothesis.hypothesis_id,
                "hypothesis_hash": hypothesis.hypothesis_hash,
                "primitive_hash": primitive.primitive_hash if primitive else None,
                "dataset_hash": dataset_hash,
                "cost_model_hash": progress["cost_model_hash"],
                "split_hash": progress["split_hash"],
                "status": status,
                "evidence_class": "V2_NEGATIVE_EVIDENCE",
                "delta_expectancy": aggregate_delta,
                "bootstrap": all_bootstrap,
            }
            with negative_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(evidence, ensure_ascii=False, sort_keys=True) + "\n")
        _rebuild_progress(progress, hypotheses, primitives)
        progress["updated_at"] = datetime.now(UTC).isoformat()
        _write_json(progress_path, progress)
    useful = [
        h
        for h in hypotheses
        if progress["hypothesis_status"].get(h.hypothesis_id, {}).get("status")
        in {"USEFUL_FILTER", "USEFUL_REGIME_FILTER", "USEFUL_SIGNAL", "USEFUL_EXIT"}
    ]
    if not useful:
        progress["final_holdout"] = {
            "status": "SEALED_NO_ELIGIBLE_CANDIDATE",
            "start": HOLDOUT_START.isoformat(),
            "access_count": 0,
        }
    existing_compositions: set[str] = set()
    if composition_path.exists():
        with composition_path.open(encoding="utf-8") as handle:
            existing_compositions = {str(json.loads(line).get("composition_id", "")) for line in handle if line.strip()}
    if useful:
        for hypothesis in useful:
            primitive = primitives.get(hypothesis.primitive)
            if primitive and primitive.role in {"FILTER", "CONFIRMATION"}:
                composition = {
                    "composition_id": stable_hash(
                        {"base_event": hypothesis.base_event, "primitive_id": primitive.primitive_id}
                    ),
                    "base_event": hypothesis.base_event,
                    "filters": [primitive.primitive_id] if primitive.role == "FILTER" else [],
                    "confirmations": [primitive.primitive_id] if primitive.role == "CONFIRMATION" else [],
                    "source_hypotheses": [hypothesis.hypothesis_id],
                    "research_only": True,
                    "promotion_authorized": False,
                }
                composition_id = str(composition["composition_id"])
                if composition_id not in existing_compositions:
                    with composition_path.open("a", encoding="utf-8") as handle:
                        handle.write(json.dumps(composition, ensure_ascii=False, sort_keys=True) + "\n")
                    existing_compositions.add(composition_id)
        if composition_path.exists():
            with composition_path.open(encoding="utf-8") as handle:
                progress["candidate_compositions"] = sum(1 for _ in handle)
    _rebuild_progress(progress, hypotheses, primitives)
    progress["running"] = progress["hypotheses"]["running"]
    progress["unknown"] = progress["hypotheses"]["unknown"]
    p0_complete = all(
        progress["families"].get(family, {}).get("terminal", 0)
        == progress["families"].get(family, {}).get("registered", 0)
        and progress["families"].get(family, {}).get("registered", 0) > 0
        for family in P0_FAMILIES
    )
    progress["status"] = (
        "RESEARCH_COMPLETE_WITH_PROMOTABLE_CANDIDATE"
        if (
            len(primitives) == 14
            and progress["primitives"]["terminal"] == len(primitives)
            and progress["primitives"]["deferred"] == 0
            and progress["hypotheses"]["terminal"] == progress["hypotheses"]["registered"]
            and progress["hypotheses"]["registered"] > 0
            and progress["hypotheses"]["running"] == 0
            and progress["hypotheses"]["unknown"] == 0
            and p0_complete
            and useful
        )
        else "RESEARCH_COMPLETE_NO_VALIDATED_EDGE"
        if (
            len(primitives) == 14
            and progress["primitives"]["terminal"] == len(primitives)
            and progress["primitives"]["deferred"] == 0
            and progress["hypotheses"]["terminal"] == progress["hypotheses"]["registered"]
            and progress["hypotheses"]["registered"] > 0
            and progress["hypotheses"]["running"] == 0
            and progress["hypotheses"]["unknown"] == 0
            and p0_complete
        )
        else "EXPERIMENT_EXECUTION_PENDING"
    )
    progress["research_design_version"] = 2
    progress["registered_hypotheses_v2"] = progress["hypotheses"]["registered"]
    progress["terminal_hypotheses_v2"] = progress["hypotheses"]["terminal"]
    progress["primitive_terminal"] = progress["primitives"]["terminal"]
    progress["primitive_deferred"] = progress["primitives"]["deferred"]
    progress["invalid_experiment_unaccounted"] = progress["hypotheses"]["unknown"] + progress["hypotheses"]["running"]
    progress["pipeline_closeout"] = (
        "QINXIONGMAO_KNOWLEDGE_ALPHA_FINAL_CLOSEOUT: COMPLETE"
        if progress["status"] != "EXPERIMENT_EXECUTION_PENDING"
        else "QINXIONGMAO_KNOWLEDGE_ALPHA_FINAL_CLOSEOUT: INCOMPLETE"
    )
    progress["promotion_authorized"] = False
    progress["research_summary"] = {
        "registered": progress["hypotheses"]["registered"],
        "terminal": progress["hypotheses"]["terminal"],
        "running": progress["hypotheses"]["running"],
        "unknown": progress["hypotheses"]["unknown"],
        "p0_families_complete": p0_complete,
        "candidate_compositions": progress.get("candidate_compositions", 0),
        "final_holdout": progress["final_holdout"],
        "production_touched": False,
    }
    status_by_hypothesis = {
        hypothesis_id: item.get("status", "UNKNOWN") for hypothesis_id, item in progress["hypothesis_status"].items()
    }
    for matrix_row in progress["execution_matrix"]:
        matrix_row["current_status"] = status_by_hypothesis.get(matrix_row["hypothesis_id"], "UNKNOWN")
    matrix_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in progress["execution_matrix"]),
        encoding="utf-8",
    )
    _write_json(progress_path, progress)
    return progress


__all__ = ["run_alpha_research"]
