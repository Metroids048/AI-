"""S1B-2 supplement: attribute v1 replay drift to the next-bar fill parity fix.

This is PROVENANCE INVESTIGATION ONLY, run under the same
V1_HISTORICAL_REPLAY_CALIBRATION_EXCEPTION authorization as
scripts/s1b2_v1_calibration_control.py.

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

What it does: monkey-patches ONLY the entry-fill price back to the pre-8c94676
semantics (same-bar decision.reference_price) in an in-process copy of the
engine, replays the identical window/rules/data, and reports aggregate deltas.
The patch is local to this process and is never written to the engine source.

Purpose: answer "does the next-bar fill parity fix account for the observed
drift?" — nothing else. Aggregate output only, no per-trade export.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.execution.signal_edge_stats import strategy_rules_hash  # noqa: E402
from services.strategy_library.candidates.registry import get_candidate  # noqa: E402
from services.validation.technical_replay import (  # noqa: E402
    TechnicalStrategyValidationService,
)
from shared.models import StrategyRules, Timeframe  # noqa: E402

STRATEGY_KEY = "auto_paper_mature_templates"
CANDIDATE_ID = "trend_momentum_v1"
EXPECTED_RULES_HASH = "41a4c796502b5d6d2a739714bc945d455882acd0e7ab97c21a8d00c2938124b2"
SYMBOLS = ("BTC/USDT", "ETH/USDT")
FULL_WINDOW_END = datetime(2026, 7, 18, 0, 0, tzinfo=UTC)
FULL_WINDOW_DAYS = 365
WARMUP_BARS = 80
PARITY_FIX_COMMIT = "8c94676"

AGGREGATE_FIELDS = (
    "net_expectancy",
    "total_trades",
    "win_rate",
    "profit_factor",
    "sharpe",
    "max_drawdown",
)


_LEGACY_SUBSTITUTIONS = (
    # (current source fragment, pre-8c94676 fragment)
    (
        """        if index + 1 >= len(bars):
            continue
        fill_bar = bars[index + 1]
        if end_at is not None and fill_bar.timestamp > end_at:
            continue
        entry_price = float(fill_bar.open)""",
        """        fill_bar = bar
        entry_price = float(decision.reference_price)""",
    ),
    ("reference_price=fill_bar.open,", "reference_price=decision.reference_price,"),
    ("opened_at=fill_bar.timestamp,", "opened_at=bar.timestamp,"),
)


def _patch_legacy_fill(service: TechnicalStrategyValidationService) -> None:
    """Bind a pre-8c94676 copy of ``_replay_symbol`` onto ``service`` only.

    The engine source file is never written. Each substitution is asserted so a
    silent no-op patch cannot masquerade as a legacy run.
    """
    import inspect
    import textwrap
    import types

    import services.validation.technical_replay as engine

    source = textwrap.dedent(inspect.getsource(TechnicalStrategyValidationService._replay_symbol))
    for current, legacy in _LEGACY_SUBSTITUTIONS:
        if current not in source:
            raise SystemExit(
                "ABORT: engine source no longer contains the expected current-fill fragment; "
                f"cannot construct a trustworthy legacy comparison. Missing:\n{current}"
            )
        source = source.replace(current, legacy)
    for current, _legacy in _LEGACY_SUBSTITUTIONS:
        if current in source:
            raise SystemExit(f"ABORT: substitution did not apply:\n{current}")

    namespace: dict[str, Any] = dict(vars(engine))
    exec(compile(source, "<legacy_replay_symbol>", "exec"), namespace)  # noqa: S102
    legacy_fn = namespace["_replay_symbol"]
    service._replay_symbol = types.MethodType(legacy_fn, service)  # type: ignore[method-assign]


def _oos_split(service: TechnicalStrategyValidationService, metrics):  # noqa: ANN001, ANN202
    if metrics.evaluation_start is None or metrics.evaluation_end is None:
        return metrics
    split_at = metrics.evaluation_start + (metrics.evaluation_end - metrics.evaluation_start) * 0.70
    handler = getattr(service, "_metrics_for_period", None)
    if handler is None:
        return metrics
    return handler(metrics, start_at=split_at, end_at=metrics.evaluation_end)


def _pick(metrics_dict: dict[str, Any]) -> dict[str, Any]:
    return {field: metrics_dict.get(field) for field in AGGREGATE_FIELDS}


def _delta(old: Any, new: Any) -> Any:
    if isinstance(old, int | float) and isinstance(new, int | float):
        return new - old
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Fill-semantics ablation for v1 replay drift (aggregate only).")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit(f"refusing to overwrite output: {args.output}")

    config = get_candidate(CANDIDATE_ID).get_config()
    actual_hash = strategy_rules_hash(StrategyRules(**config))
    if actual_hash != EXPECTED_RULES_HASH:
        raise SystemExit(f"ABORT: rules drifted; expected {EXPECTED_RULES_HASH}, got {actual_hash}")

    from scripts.run_top20_technical_validation import _load_stored, _template

    strategy = _template(strategy_key=STRATEGY_KEY, rules=config, timeframe=Timeframe.M15)
    stored = _load_stored(days=FULL_WINDOW_DAYS, end_at=FULL_WINDOW_END, symbols=SYMBOLS)

    source = ROOT / "services" / "validation" / "technical_replay.py"
    engine_sha = hashlib.sha256(source.read_bytes()).hexdigest()

    results: dict[str, dict[str, Any]] = {}
    for mode in ("current_next_bar_open", "legacy_same_bar_reference"):
        service = TechnicalStrategyValidationService(oos_fraction=0.30, walk_forward_windows=3, max_workers=1)
        if mode == "legacy_same_bar_reference":
            _patch_legacy_fill(service)
        per_symbol: dict[str, Any] = {}
        for symbol in SYMBOLS:
            sym_data = stored.get(symbol, {})
            entry_bars = sym_data.get("15m", [])
            if len(entry_bars) <= WARMUP_BARS:
                per_symbol[symbol] = {"error": "insufficient_entry_bars"}
                continue
            full = service.replay(
                strategy=strategy,
                market_data={symbol: sym_data},
                start_at=entry_bars[WARMUP_BARS].timestamp,
                end_at=entry_bars[-1].timestamp,
            )
            oos = _oos_split(service, full)
            per_symbol[symbol] = {"full": _pick(full.as_dict()), "oos": _pick(oos.as_dict())}
        results[mode] = per_symbol

    comparison: dict[str, Any] = {}
    for symbol in SYMBOLS:
        cur = results["current_next_bar_open"].get(symbol, {})
        leg = results["legacy_same_bar_reference"].get(symbol, {})
        if "error" in cur or "error" in leg:
            comparison[symbol] = {"error": "insufficient_entry_bars"}
            continue
        comparison[symbol] = {
            scope: {
                "legacy_same_bar_reference": leg[scope],
                "current_next_bar_open": cur[scope],
                "delta_current_minus_legacy": {
                    field: _delta(leg[scope].get(field), cur[scope].get(field)) for field in AGGREGATE_FIELDS
                },
            }
            for scope in ("full", "oos")
        }

    commit = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False).stdout.strip()
    report = {
        "schema_version": 1,
        "artifact_type": "s1b2_fill_semantics_ablation",
        "authorization": "V1_HISTORICAL_REPLAY_CALIBRATION_EXCEPTION",
        "purpose": "PROVENANCE_INVESTIGATION_ONLY",
        "question": "Does the next-bar fill parity fix (8c94676) account for the v1 replay drift?",
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
        "provenance": {
            "git_commit": commit,
            "candidate_id": CANDIDATE_ID,
            "candidate_rules_hash": actual_hash,
            "rules_hash_locked": actual_hash == EXPECTED_RULES_HASH,
            "engine_source_sha256": engine_sha,
            "parity_fix_commit": PARITY_FIX_COMMIT,
            "legacy_patch_scope": "entry fill price + risk-price anchor + opened_at only; in-process, source unmodified",
            "full_window_nominal_end": FULL_WINDOW_END.isoformat(),
            "full_window_days": FULL_WINDOW_DAYS,
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
            "final_holdout_accessed": False,
            "generated_at": datetime.now(UTC).isoformat(),
        },
        "comparison": comparison,
        "verdict": "PENDING_HUMAN_CLASSIFICATION",
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report["comparison"], indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
