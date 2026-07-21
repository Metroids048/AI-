"""Compute symbol-scoped, out-of-sample edge evidence from stored OHLCV only."""

from __future__ import annotations

import argparse
import json
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

MIN_TRADE_SAMPLES = 30


def evidence_failure_reasons(metrics, *, min_oos_trades: int = MIN_TRADE_SAMPLES) -> list[str]:  # noqa: ANN001
    from services.validation.policy import default_policy

    failed: list[str] = []
    if metrics.total_trades < min_oos_trades:
        failed.append("insufficient_oos_trades")
    if metrics.sharpe <= default_policy.min_sharpe:
        failed.append("sharpe_not_above_1")
    if metrics.profit_factor <= default_policy.min_profit_factor:
        failed.append("profit_factor_not_above_1_3")
    if metrics.max_drawdown >= default_policy.max_drawdown:
        failed.append("max_drawdown_not_below_25pct")
    if metrics.net_expectancy <= default_policy.min_expectancy:
        failed.append("net_expectancy_not_positive")
    if metrics.data_issues:
        failed.append("data_issues_present")
    return failed


def build_artifact_payload(
    *,
    strategy_key: str,
    candidate_id: str,
    symbol: str,
    rules: Any,
    full_metrics,
    oos_metrics,
    min_oos_trades: int,
    max_age_days: int,
    computed_at: datetime,
) -> dict[str, Any]:
    from services.execution.signal_edge_stats import EDGE_STATS_SCHEMA_VERSION, strategy_rules_hash

    failed = evidence_failure_reasons(oos_metrics, min_oos_trades=min_oos_trades)
    entry_rules = rules.get("entry_rules", {}) if isinstance(rules, dict) else rules.entry_rules
    return {
        "schema_version": EDGE_STATS_SCHEMA_VERSION,
        "strategy_key": strategy_key,
        "candidate_id": candidate_id,
        "rules_hash": strategy_rules_hash(rules),
        "symbol": symbol,
        "timeframes": {
            "direction": entry_rules.get("direction_timeframe", "4h"),
            "state": entry_rules.get("state_timeframe", "1h"),
            "entry": entry_rules.get("entry_timeframe", "15m"),
        },
        "computed_at": computed_at.isoformat(),
        "sample_count": int(full_metrics.total_trades),
        "oos_sample_count": int(oos_metrics.total_trades),
        "win_rate": float(oos_metrics.win_rate),
        "average_net_win": float(oos_metrics.average_win),
        "average_net_loss_magnitude": abs(float(oos_metrics.average_loss)),
        "net_expectancy": float(oos_metrics.net_expectancy),
        "sharpe": float(oos_metrics.sharpe),
        "profit_factor": float(oos_metrics.profit_factor),
        "max_drawdown": float(oos_metrics.max_drawdown),
        "evaluation_start": oos_metrics.evaluation_start.isoformat() if oos_metrics.evaluation_start else None,
        "evaluation_end": oos_metrics.evaluation_end.isoformat() if oos_metrics.evaluation_end else None,
        "cost_model": {
            "returns_are_net_of_costs": True,
            "total_fee_bps": float(oos_metrics.total_fee_bps),
            "total_slippage_bps": float(oos_metrics.total_slippage_bps),
        },
        "eligible": not failed,
        "failed_reasons": failed,
        "max_age_days": max_age_days,
    }


def select_best_candidate(results: list[dict[str, Any]]) -> str | None:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for result in results:
        if result.get("eligible"):
            grouped.setdefault(str(result["candidate_id"]), []).append(result)
    if not grouped:
        return None

    def score(item: tuple[str, list[dict[str, Any]]]) -> tuple[int, float, int, str]:
        candidate_id, rows = item
        return (
            len({str(row["symbol"]) for row in rows}),
            min(float(row["net_expectancy"]) for row in rows),
            -int(rows[0].get("signal_count", 999)),
            candidate_id,
        )

    return max(grouped.items(), key=score)[0]


@dataclass(frozen=True)
class EdgeStatsComputationResult:
    accepted: bool
    strategy_key: str
    total_trades: int
    win_rate: float
    average_win: float
    average_loss: float
    min_trade_samples: int
    artifact_path: str | None = None
    evaluation_start: str | None = None
    evaluation_end: str | None = None
    report_path: str | None = None
    selected_candidate_id: str | None = None
    results: tuple[dict[str, Any], ...] = ()


def _oos_metrics(service, metrics):  # noqa: ANN001, ANN202
    if metrics.evaluation_start is None or metrics.evaluation_end is None:
        return metrics
    split_at = metrics.evaluation_start + (metrics.evaluation_end - metrics.evaluation_start) * 0.70
    metrics_for_period = getattr(service, "_metrics_for_period", None)
    if metrics_for_period is None:
        return metrics
    return metrics_for_period(metrics, start_at=split_at, end_at=metrics.evaluation_end)


def compute_and_write_edge_stats(
    *,
    strategy_key: str,
    days: int = 365,
    min_trade_samples: int = MIN_TRADE_SAMPLES,
    max_age_days: int = 30,
    reuse_stored_data: bool = True,
    symbols: list[str] | tuple[str, ...] | None = None,
    candidate_ids: list[str] | tuple[str, ...] | None = None,
    end_at: datetime | None = None,
) -> EdgeStatsComputationResult:
    from scripts.run_top20_technical_validation import _closed_four_hour_boundary, _load_stored, _template
    from services.data.universe import AUTO_PAPER_RESEARCH_SYMBOLS
    from services.execution.bootstrap import AUTO_PAPER_TECHNICAL_KEY
    from services.execution.signal_edge_stats import EDGE_STATS_ARTIFACT_DIR, strategy_rules_hash, symbol_artifact_key
    from services.strategy_library.candidates.registry import get_candidate
    from services.validation.technical_replay import TechnicalStrategyValidationService
    from shared.models import Timeframe

    if strategy_key != AUTO_PAPER_TECHNICAL_KEY:
        raise ValueError(f"only {AUTO_PAPER_TECHNICAL_KEY!r} is wired for evidence replay")
    if not reuse_stored_data:
        raise ValueError("edge evidence computation is local-data-only; backfill data separately")

    resolved_end = _closed_four_hour_boundary(end_at or datetime.now(UTC))
    resolved_symbols = list(symbols or AUTO_PAPER_RESEARCH_SYMBOLS)
    resolved_candidates = list(candidate_ids or ("operator_heuristic_v1", "trend_momentum_v1", "trend_breakout_v1"))
    stored = _load_stored(days=days, end_at=resolved_end)
    computed_at = datetime.now(UTC)
    service = TechnicalStrategyValidationService(oos_fraction=0.30, walk_forward_windows=3, max_workers=1)
    _WARMUP_BARS = 80
    replay_jobs: list[tuple[str, str, Any, Any, int, Any, Any, Any]] = []
    for candidate_id in resolved_candidates:
        config = get_candidate(candidate_id).get_config()
        strategy = _template(strategy_key=strategy_key, rules=config, timeframe=Timeframe.M15)
        signal_count = len(set(config["entry_rules"].get("direction_signals", []))) + len(
            set(config["entry_rules"].get("entry_signals", []))
        )
        for symbol in resolved_symbols:
            sym_data = stored.get(symbol, {})
            entry_bars = sym_data.get("15m", [])
            _bar_ts = lambda b: b.timestamp if hasattr(b, "timestamp") else b["timestamp"]  # noqa: E731
            start_at = _bar_ts(entry_bars[_WARMUP_BARS]) if len(entry_bars) > _WARMUP_BARS else None
            end_at = _bar_ts(entry_bars[-1]) if entry_bars else None
            replay_jobs.append(
                (candidate_id, symbol, strategy, strategy.rules, signal_count, sym_data, start_at, end_at)
            )

    def replay_job(job: tuple[str, str, Any, Any, int, Any, Any, Any]) -> dict[str, Any]:
        candidate_id, symbol, strategy, rules, signal_count, sym_data, job_start_at, job_end_at = job
        full_metrics = service.replay(
            strategy=strategy,
            market_data={symbol: sym_data},
            start_at=job_start_at,
            end_at=job_end_at,
        )
        oos_metrics = _oos_metrics(service, full_metrics)
        payload = build_artifact_payload(
            strategy_key=strategy_key,
            candidate_id=candidate_id,
            symbol=symbol,
            rules=rules,
            full_metrics=full_metrics,
            oos_metrics=oos_metrics,
            min_oos_trades=min_trade_samples,
            max_age_days=max_age_days,
            computed_at=computed_at,
        )
        payload["signal_count"] = signal_count
        return payload

    # Each replay owns its historical view.  Bounded concurrency makes the
    # three-symbol OOS refresh practical without changing replay semantics.
    with ThreadPoolExecutor(max_workers=min(3, len(replay_jobs) or 1)) as executor:
        results = list(executor.map(replay_job, replay_jobs))

    for payload in results:
        active_path = (
            EDGE_STATS_ARTIFACT_DIR
            / strategy_key
            / str(payload["candidate_id"])
            / symbol_artifact_key(str(payload["symbol"]))
            / "active.json"
        )
        if payload["eligible"]:
            active_path.parent.mkdir(parents=True, exist_ok=True)
            active_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        elif active_path.exists():
            active_path.unlink()

    selected_candidate_id = select_best_candidate(results)
    strategy_dir = EDGE_STATS_ARTIFACT_DIR / strategy_key
    reports_dir = strategy_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    report_path = reports_dir / f"{computed_at.strftime('%Y%m%dT%H%M%SZ')}.json"
    report_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "strategy_key": strategy_key,
                "computed_at": computed_at.isoformat(),
                "days": days,
                "symbols": resolved_symbols,
                "candidate_ids": resolved_candidates,
                "selected_candidate_id": selected_candidate_id,
                "results": results,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    manifest_path = strategy_dir / "active-manifest.json"
    if selected_candidate_id is not None:
        selected_rows = [row for row in results if row["candidate_id"] == selected_candidate_id and row["eligible"]]
        selected_strategy = _template(
            strategy_key=strategy_key,
            rules=get_candidate(selected_candidate_id).get_config(),
            timeframe=Timeframe.M15,
        )
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "strategy_key": strategy_key,
                    "candidate_id": selected_candidate_id,
                    "rules_hash": strategy_rules_hash(selected_strategy.rules),
                    "eligible_symbols": [row["symbol"] for row in selected_rows],
                    "computed_at": computed_at.isoformat(),
                    "report_path": report_path.as_posix(),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    elif manifest_path.exists():
        manifest_path.unlink()

    chosen_id = selected_candidate_id or resolved_candidates[0]
    chosen_rows = [row for row in results if row["candidate_id"] == chosen_id]
    representative = chosen_rows[0] if chosen_rows else {}
    return EdgeStatsComputationResult(
        accepted=selected_candidate_id is not None,
        strategy_key=strategy_key,
        total_trades=sum(int(row["sample_count"]) for row in chosen_rows),
        win_rate=float(representative.get("win_rate", 0.0)),
        average_win=float(representative.get("average_net_win", 0.0)),
        average_loss=float(representative.get("average_net_loss_magnitude", 0.0)),
        min_trade_samples=min_trade_samples,
        artifact_path=str(manifest_path) if selected_candidate_id else None,
        evaluation_start=representative.get("evaluation_start"),
        evaluation_end=representative.get("evaluation_end"),
        report_path=str(report_path),
        selected_candidate_id=selected_candidate_id,
        results=tuple(results),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strategy-key", required=True)
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument(
        "--database-url",
        required=True,
        help="Target database containing the persisted OHLCV history; required to avoid reading the wrong store.",
    )
    parser.add_argument("--symbols", nargs="+", default=None)
    parser.add_argument("--candidate-id", action="append", dest="candidate_ids", default=None)
    parser.add_argument("--min-trade-samples", type=int, default=MIN_TRADE_SAMPLES)
    parser.add_argument("--max-age-days", type=int, default=30)
    args = parser.parse_args()

    os.environ["POSTGRES_URL"] = args.database_url
    result = compute_and_write_edge_stats(
        strategy_key=args.strategy_key,
        days=args.days,
        min_trade_samples=args.min_trade_samples,
        max_age_days=args.max_age_days,
        symbols=args.symbols,
        candidate_ids=args.candidate_ids,
    )
    print(
        f"accepted={result.accepted} selected_candidate={result.selected_candidate_id or 'none'} "
        f"total_trades={result.total_trades} report={result.report_path}"
    )
    return 0 if result.accepted else 1


if __name__ == "__main__":
    raise SystemExit(main())
