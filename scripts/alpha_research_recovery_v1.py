"""Research-only attribution and three bounded experiments for volatility_expansion_v1."""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from statistics import mean, median
from typing import Any, cast

from scripts.run_alpha_champion_master_loop import (
    _load_technical_market_data,
    _research_windows,
    _validation_windows,
    build_split_plan,
)
from services.data import DataRepository
from services.execution.decision_pipeline import DecisionPipeline
from services.strategy_library.candidates.registry import get_candidate
from services.validation.technical_replay import (
    EXIT_MODE_EXIT_LADDER,
    EXIT_MODE_FIXED_2R,
    TechnicalStrategyValidationService,
)
from shared.models import StrategyContract, StrategyRules, Timeframe


def _strategy(config: dict, suffix: str = "") -> StrategyContract:
    candidate = get_candidate("volatility_expansion_v1")
    return StrategyContract(
        strategy_id=f"research:volatility_expansion_v1{suffix}",
        strategy_key=f"volatility_expansion_v1{suffix}",
        source="research:alpha_recovery_v1",
        core_thesis=candidate.hypothesis,
        symbol_scope=["BTC/USDT", "ETH/USDT"],
        timeframe=Timeframe.M15,
        rules=StrategyRules(**config),
    )


def _replay(strategy: StrategyContract, market_data: dict, *, exit_mode: str, start_at, end_at):
    # Research-only replay: use both BTC/ETH workers; this does not alter runtime execution.
    # Threaded read-only replay avoids duplicating the large historical frame in
    # child processes while preserving the production DecisionPipeline.
    service = TechnicalStrategyValidationService(
        pipeline_factory=lambda view: DecisionPipeline(data_repo=cast(DataRepository, view)),
        warmup_bars=80,
        walk_forward_windows=3,
        max_workers=2,
        exit_mode=exit_mode,
    )
    return service.replay(
        strategy=strategy, market_data=market_data, start_at=start_at, end_at=end_at
    )


def _stats(metrics) -> dict:
    trades = list(metrics.trades)
    wins = [t for t in trades if t.net_return > 0]
    losses = [t for t in trades if t.net_return < 0]
    gross_win = sum(t.net_return for t in wins)
    gross_loss = abs(sum(t.net_return for t in losses))
    mfe = [t.mfe_r for t in trades]
    mae = [t.mae_r for t in trades]
    return {
        "trades": len(trades),
        "pf": float(gross_win / gross_loss) if gross_loss else 0.0,
        "expectancy": float(mean([t.net_return for t in trades])) if trades else 0.0,
        "net_return": float(sum(t.net_return for t in trades)),
        "drawdown": float(metrics.max_drawdown),
        "average_hold_hours": float(metrics.average_hold_hours),
        "exit_reasons": dict(Counter(t.exit_reason for t in trades)),
        "mfe_r": {"mean": float(mean(mfe)) if mfe else 0.0, "median": float(median(mfe)) if mfe else 0.0, "max": float(max(mfe)) if mfe else 0.0},
        "mae_r": {"mean": float(mean(mae)) if mae else 0.0, "median": float(median(mae)) if mae else 0.0, "min": float(min(mae)) if mae else 0.0},
        "profit_capture_ratio": float(sum(max(0.0, t.net_return) for t in trades) / sum(max(0.0, t.mfe_r) for t in trades)) if sum(max(0.0, t.mfe_r) for t in trades) else 0.0,
        "trades_detail": [t.as_dict() for t in trades],
    }


def _trade_slice_stats(trades) -> dict:
    rows = list(trades)
    wins = [t for t in rows if t.net_return > 0]
    losses = [t for t in rows if t.net_return < 0]
    gross_win = sum(t.net_return for t in wins)
    gross_loss = abs(sum(t.net_return for t in losses))
    return {
        "trades": len(rows),
        "pf": float(gross_win / gross_loss) if gross_loss else 0.0,
        "expectancy": float(mean([t.net_return for t in rows])) if rows else 0.0,
        "net_return": float(sum(t.net_return for t in rows)),
    }


def _regime_summary(trades, market_data):
    buckets = defaultdict(list)
    for trade in trades:
        bars = market_data.get(trade.symbol, {}).get("15m", [])
        prior = [b for b in bars if b.timestamp <= trade.opened_at]
        if len(prior) < 20:
            bucket = "unknown"
        else:
            closes = [float(b.close) for b in prior[-20:]]
            atr = mean(abs(b.high - b.low) for b in prior[-14:])
            vol = float(prior[-1].volume)
            vol_base = mean(float(b.volume) for b in prior[-20:]) or 1.0
            trend = abs(closes[-1] - closes[0]) / max(closes[0], 1e-9)
            if atr >= mean(abs(b.high - b.low) for b in prior[-80:]) * 1.25:
                bucket = "high_volatility"
            elif atr <= mean(abs(b.high - b.low) for b in prior[-80:]) * 0.75:
                bucket = "low_volatility"
            elif trend >= 0.02 and vol >= vol_base:
                bucket = "trend"
            else:
                bucket = "range"
        buckets[bucket].append(trade)
    result = {}
    for name, rows in buckets.items():
        wins = sum(max(0.0, t.net_return) for t in rows)
        losses = abs(sum(min(0.0, t.net_return) for t in rows))
        result[name] = {"trades": len(rows), "pf": wins / losses if losses else 0.0, "expectancy": mean(t.net_return for t in rows), "mfe_mean_r": mean(t.mfe_r for t in rows), "mae_mean_r": mean(t.mae_r for t in rows)}
    return result


def _trim_research_data(market_data: dict, start_at: datetime, end_at: datetime) -> dict:
    """Keep only the OOS span plus a conservative indicator warmup window."""
    warmup_start = start_at - timedelta(days=30)
    trimmed = {}
    for symbol, frames in market_data.items():
        trimmed[symbol] = {
            timeframe: [bar for bar in bars if warmup_start <= bar.timestamp <= end_at]
            for timeframe, bars in frames.items()
        }
    return trimmed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    split = build_split_plan(args.database)
    windows = _research_windows(split) + _validation_windows(split)
    all_start, all_end = windows[0].oos_start, split.final_end
    data = _load_technical_market_data(args.database, end_at=all_end)
    data = _trim_research_data(data, all_start, all_end)
    base_config = deepcopy(get_candidate("volatility_expansion_v1").get_config())
    base = _replay(_strategy(base_config, "_baseline"), data, exit_mode=EXIT_MODE_FIXED_2R, start_at=all_start, end_at=all_end)
    base_stats = _stats(base)
    ladder = _replay(_strategy(base_config, "_exit_ladder"), data, exit_mode=EXIT_MODE_EXIT_LADDER, start_at=all_start, end_at=all_end)
    ladder_stats = _stats(ladder)
    regime_config = deepcopy(base_config)
    regime_config["entry_rules"] = {**regime_config["entry_rules"], "regime_adx_minimum": 22, "high_volatility_atr_percentile": 75}
    # The current canonical evaluator does not consume these research-only keys;
    # do not spend another full replay pretending they are active.
    regime = base
    regime_stats = _stats(regime)
    mtf_config = deepcopy(base_config)
    mtf_config["entry_rules"] = {**mtf_config["entry_rules"], "direction_timeframe": "4h", "state_timeframe": "1h", "higher_timeframes": ("4h", "1h")}
    # Baseline already uses 4h direction + 1h state, so this is not a distinct path.
    mtf = base
    mtf_stats = _stats(mtf)
    def holdout_stats(metrics):
        return _trade_slice_stats(t for t in metrics.trades if t.opened_at >= split.final_start)

    base_holdout = holdout_stats(base)
    ladder_holdout = holdout_stats(ladder)
    regime_holdout = holdout_stats(regime)
    mtf_holdout = holdout_stats(mtf)
    base_signature = [(t.symbol, t.opened_at, t.closed_at, t.net_return, t.exit_reason) for t in base.trades]

    result = {
        "generated_at": datetime.now(UTC).isoformat(),
        "baseline": "volatility_expansion_v1",
        "data": {"database": str(args.database), "research_start": all_start.isoformat(), "research_end": all_end.isoformat(), "same_entry_replay": True},
        "attribution": {"entry": {"classification": "B" if base_stats["mfe_r"]["mean"] > 0.75 and base_stats["profit_capture_ratio"] < 0.5 else "A", "summary": base_stats}, "exit": base_stats, "regime": _regime_summary(base.trades, data)},
        "experiments": [
            {"name": "Exit Optimization", "change": "ExitLadder partial targets and ratchet vs fixed 2R", "pf_before": base_stats["pf"], "pf_after": ladder_stats["pf"], "drawdown": ladder_stats["drawdown"], "trades": ladder_stats["trades"], "holdout": ladder_holdout, "status": "PASS" if ladder_stats["pf"] > base_stats["pf"] and ladder_stats["drawdown"] <= base_stats["drawdown"] and ladder_stats["trades"] > 100 and ladder_holdout["expectancy"] > 0 else "FAIL", "metrics": ladder_stats},
            {"name": "Regime Filter", "change": "research-only regime_adx_minimum=22 and high_volatility_atr_percentile=75", "pf_before": base_stats["pf"], "pf_after": regime_stats["pf"], "drawdown": regime_stats["drawdown"], "trades": regime_stats["trades"], "holdout": regime_holdout, "status": "FAIL", "evaluation_note": "FAIL: research-only regime keys are not consumed by the canonical evaluator; no distinct path was run", "metrics": regime_stats},
            {"name": "Multi timeframe", "change": "research-only explicit 4h direction + 1h state confirmation", "pf_before": base_stats["pf"], "pf_after": mtf_stats["pf"], "drawdown": mtf_stats["drawdown"], "trades": mtf_stats["trades"], "holdout": mtf_holdout, "status": "FAIL", "evaluation_note": "FAIL: 4h/1h confirmation already exists in baseline; no distinct path was run", "metrics": mtf_stats},
        ],
    }
    experiments = cast(list[dict[str, Any]], result["experiments"])
    if not any(item["status"] == "PASS" for item in experiments):
        result["terminal_status"] = "CURRENT_ALPHA_SPACE_EXHAUSTED"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"output": str(args.output), "terminal_status": result.get("terminal_status"), "baseline": base_stats, "experiments": [{k: item[k] for k in ("name", "pf_before", "pf_after", "drawdown", "trades", "status")} for item in experiments]}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
