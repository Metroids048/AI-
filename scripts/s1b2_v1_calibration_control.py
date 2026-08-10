"""S1B-2: V1_HISTORICAL_REPLAY_CALIBRATION_EXCEPTION (aggregate-only control).

=============================================================================
CALIBRATION RESULTS MUST NOT BE USED TO:
  - modify trend_momentum_v1
  - modify trend_momentum_v2_enriched
  - modify proposal candidates
  - design Trend Pullback V3
  - tune replay parameters
  - tune fee/slippage assumptions
  - tune exits
  - tune indicator parameters
  - select features
  - modify shared replay implementation to improve matching

Purpose is REPRODUCE_EXISTING_ARTIFACT_ONLY. This is NOT a Final Holdout
unseal and NOT a new OOS validation. Final Holdout remains SEALED for
trend_pullback_v2, range_sweep_reversion_v1, failed_breakout_reversal_v1 and
any future Trend Pullback V3.

If reproduction fails, the only permitted follow-up is to investigate
provenance (commit, data version, cost config, engine semantics, warmup, split
boundary) and explain the difference. Tuning anything to close the gap would
convert this control into holdout tuning.
=============================================================================

Scope lock (hardcoded, not parameterised):
  candidate_id : trend_momentum_v1 only
  rules_hash   : must equal the historical artifact's hash, else abort
  full window  : 2025-07-18 00:00Z -> 2026-07-18 00:00Z (days=365 from the
                 closed-4h boundary at the artifact's computed_at)
  OOS window   : trailing 30% of the replayed bar range, which resolves to
                 2026-03-30 -> 2026-07-18 and matches the artifact
  data source  : the runtime database the original artifact was computed from
  output       : AGGREGATE METRICS ONLY - no per-trade, no MAE/MFE, no
                 per-regime, no per-bar series
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from services.execution.signal_edge_stats import strategy_rules_hash
from services.strategy_library.candidates.registry import get_candidate
from services.validation.technical_replay import TechnicalStrategyValidationService
from shared.models import StrategyRules, Timeframe

CANDIDATE_ID = "trend_momentum_v1"
EXPECTED_RULES_HASH = "41a4c796502b5d6d2a739714bc945d455882acd0e7ab97c21a8d00c2938124b2"
FULL_WINDOW_END = datetime(2026, 7, 18, 0, 0, tzinfo=UTC)
FULL_WINDOW_DAYS = 365
STRATEGY_KEY = "auto_paper_mature_templates"
SYMBOLS = ("BTC/USDT", "ETH/USDT")
WARMUP_BARS = 80
ARTIFACT_ROOT = Path("artifacts/signal_edge_stats/auto_paper_mature_templates/trend_momentum_v1")

# Only these fields may leave this script. Anything finer-grained (trades,
# MAE/MFE, regime splits) is deliberately withheld.
AGGREGATE_FIELDS = (
    "net_expectancy",
    "oos_sample_count",
    "win_rate",
    "profit_factor",
    "sharpe",
    "max_drawdown",
)


def _artifact_key(symbol: str) -> str:
    return symbol.replace("/", "")


def _load_historical(symbol: str) -> dict[str, Any]:
    path = ARTIFACT_ROOT / _artifact_key(symbol) / "active.json"
    raw = path.read_bytes()
    payload = json.loads(raw.decode("utf-8"))
    return {
        "artifact_path": str(path),
        "artifact_sha256": hashlib.sha256(raw).hexdigest(),
        "rules_hash": payload["rules_hash"],
        "computed_at": payload.get("computed_at"),
        "evaluation_start": payload.get("evaluation_start"),
        "evaluation_end": payload.get("evaluation_end"),
        "cost_model": payload.get("cost_model"),
        "metrics": {
            "net_expectancy": payload.get("net_expectancy"),
            "oos_sample_count": payload.get("oos_sample_count"),
            "win_rate": payload.get("win_rate"),
            "profit_factor": payload.get("profit_factor"),
            "sharpe": payload.get("sharpe"),
            "max_drawdown": payload.get("max_drawdown"),
            "sample_count": payload.get("sample_count"),
        },
    }


def _oos_split(service: TechnicalStrategyValidationService, metrics):  # noqa: ANN001, ANN202
    """Mirror compute_signal_edge_stats._oos_metrics exactly (trailing 30%)."""
    if metrics.evaluation_start is None or metrics.evaluation_end is None:
        return metrics
    split_at = metrics.evaluation_start + (metrics.evaluation_end - metrics.evaluation_start) * 0.70
    handler = getattr(service, "_metrics_for_period", None)
    if handler is None:
        return metrics
    return handler(metrics, start_at=split_at, end_at=metrics.evaluation_end)


def _delta(historical: Any, reproduced: Any) -> Any:
    if isinstance(historical, int | float) and isinstance(reproduced, int | float):
        return reproduced - historical
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="S1B-2 v1 replay calibration control (aggregate only).")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit(f"refusing to overwrite calibration output: {args.output}")

    config = get_candidate(CANDIDATE_ID).get_config()
    actual_hash = strategy_rules_hash(StrategyRules(**config))
    if actual_hash != EXPECTED_RULES_HASH:
        raise SystemExit(
            "ABORT: candidate rules drifted from the historical artifact hash; "
            f"expected {EXPECTED_RULES_HASH}, got {actual_hash}. "
            "Reproduction requires the original fixed rules."
        )

    from scripts.run_top20_technical_validation import _load_stored, _template

    historical = {symbol: _load_historical(symbol) for symbol in SYMBOLS}
    for symbol, record in historical.items():
        if record["rules_hash"] != EXPECTED_RULES_HASH:
            raise SystemExit(f"ABORT: {symbol} artifact rules_hash mismatch: {record['rules_hash']}")

    strategy = _template(strategy_key=STRATEGY_KEY, rules=config, timeframe=Timeframe.M15)
    service = TechnicalStrategyValidationService(oos_fraction=0.30, walk_forward_windows=3, max_workers=1)
    stored = _load_stored(days=FULL_WINDOW_DAYS, end_at=FULL_WINDOW_END, symbols=SYMBOLS)

    comparison: dict[str, Any] = {}
    for symbol in SYMBOLS:
        sym_data = stored.get(symbol, {})
        entry_bars = sym_data.get("15m", [])
        if len(entry_bars) <= WARMUP_BARS:
            comparison[symbol] = {"error": "insufficient_entry_bars", "bar_count": len(entry_bars)}
            continue
        start_at = entry_bars[WARMUP_BARS].timestamp
        end_at = entry_bars[-1].timestamp
        full = service.replay(strategy=strategy, market_data={symbol: sym_data}, start_at=start_at, end_at=end_at)
        oos = _oos_split(service, full)
        full_dict = full.as_dict()
        oos_dict = oos.as_dict()
        reproduced = {
            "net_expectancy": oos_dict.get("net_expectancy"),
            "oos_sample_count": oos_dict.get("total_trades"),
            "win_rate": oos_dict.get("win_rate"),
            "profit_factor": oos_dict.get("profit_factor"),
            "sharpe": oos_dict.get("sharpe"),
            "max_drawdown": oos_dict.get("max_drawdown"),
            "sample_count": full_dict.get("total_trades"),
        }
        hist_metrics = historical[symbol]["metrics"]
        comparison[symbol] = {
            "historical": hist_metrics,
            "reproduced": reproduced,
            "delta": {key: _delta(hist_metrics.get(key), reproduced.get(key)) for key in AGGREGATE_FIELDS},
            "window": {
                "historical_evaluation_start": historical[symbol]["evaluation_start"],
                "historical_evaluation_end": historical[symbol]["evaluation_end"],
                "reproduced_full_start": full_dict.get("evaluation_start"),
                "reproduced_full_end": full_dict.get("evaluation_end"),
                "reproduced_oos_start": oos_dict.get("evaluation_start"),
                "reproduced_oos_end": oos_dict.get("evaluation_end"),
            },
            "cost": {
                "historical": historical[symbol]["cost_model"],
                # Historical artifact stamps cost_model from OOS metrics
                # (compute_signal_edge_stats.py uses oos_metrics.total_fee_bps),
                # so the reproduced side must be read from the OOS split too.
                "reproduced_oos_total_fee_bps": oos_dict.get("total_fee_bps"),
                "reproduced_oos_total_slippage_bps": oos_dict.get("total_slippage_bps"),
                "reproduced_full_total_fee_bps": full_dict.get("total_fee_bps"),
                "reproduced_full_total_slippage_bps": full_dict.get("total_slippage_bps"),
            },
            "artifact_provenance": {
                "artifact_path": historical[symbol]["artifact_path"],
                "artifact_sha256": historical[symbol]["artifact_sha256"],
                "artifact_computed_at": historical[symbol]["computed_at"],
            },
        }

    commit = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False).stdout.strip()
    report = {
        "schema_version": 1,
        "artifact_type": "s1b2_v1_replay_calibration_control",
        "authorization": "V1_HISTORICAL_REPLAY_CALIBRATION_EXCEPTION",
        "purpose": "REPRODUCE_EXISTING_ARTIFACT_ONLY",
        "prohibitions": [
            "must_not_modify_trend_momentum_v1",
            "must_not_modify_trend_momentum_v2_enriched",
            "must_not_modify_proposal_candidates",
            "must_not_design_trend_pullback_v3",
            "must_not_tune_replay_parameters",
            "must_not_tune_fee_or_slippage_assumptions",
            "must_not_tune_exits",
            "must_not_tune_indicator_parameters",
            "must_not_select_features",
            "must_not_modify_shared_replay_implementation_to_improve_matching",
        ],
        "final_holdout_status": {
            "trend_pullback_v2": "SEALED",
            "range_sweep_reversion_v1": "SEALED",
            "failed_breakout_reversal_v1": "SEALED",
            "future_trend_pullback_v3": "SEALED",
            "note": (
                "This control reads the legacy v1 artifact window only. The v1 artifact was itself "
                "computed on 2026-03-30 -> 2026-07-18, which lies inside the current protocol's sealed "
                "region; it is therefore LEGACY_VALIDATED_OOS_ARTIFACT under the old protocol, not an "
                "OOS artifact under the current one."
            ),
        },
        "provenance": {
            "git_commit": commit,
            "candidate_id": CANDIDATE_ID,
            "candidate_rules_hash": actual_hash,
            "expected_rules_hash": EXPECTED_RULES_HASH,
            "rules_hash_locked": actual_hash == EXPECTED_RULES_HASH,
            "full_window_nominal_start": (FULL_WINDOW_END - timedelta(days=FULL_WINDOW_DAYS)).isoformat(),
            "full_window_nominal_end": FULL_WINDOW_END.isoformat(),
            "full_window_days": FULL_WINDOW_DAYS,
            "engine": "services.validation.technical_replay.TechnicalStrategyValidationService",
            "engine_settings": {
                "oos_fraction": 0.30,
                "walk_forward_windows": 3,
                "warmup_bars": WARMUP_BARS,
                "oos_definition": "trailing 30% of replayed bar range",
            },
            "writes_edge_stats_artifact": False,
            "writes_manifest_or_config": False,
            "per_trade_data_exported": False,
            "per_regime_data_exported": False,
            "generated_at": datetime.now(UTC).isoformat(),
        },
        "comparison": comparison,
        "verdict": "PENDING_HUMAN_CLASSIFICATION",
        "verdict_options": [
            "STRICT_REPRODUCTION_PASS",
            "STRICT_REPRODUCTION_EXPLAINED_DRIFT",
            "STRICT_REPRODUCTION_UNEXPLAINED_FAILURE",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(json.dumps(report["comparison"], indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
