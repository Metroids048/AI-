"""Offline computation of real signal-conditioned edge stats for the
net_edge_after_cost gate.

Replays the exact same entry signal + stop/take rules a strategy uses through
`TechnicalStrategyValidationService` (the same engine already used for the
Top20 baseline comparison and the ExitLadder-vs-fixed-2R comparison), over
real persisted OHLCV history, and writes the resulting win_rate/average_win/
average_loss to a local artifact. `services/execution/signal_edge_stats.py`
reads that artifact read-only at decision time; `net_edge_after_cost` in
decision_pipeline.py uses it instead of the raw-bar-return proxy whenever it
exists and is fresh, falling back to the proxy otherwise.

This script never talks to an LLM and never writes to any table Execution/
Gatekeeper reads directly -- it only ever produces a local JSON artifact.

Usage:
    python scripts/compute_signal_edge_stats.py --strategy-key auto_paper_mature_templates \
        --database-url sqlite:///.local_paper_console.db --days 60
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime

MIN_TRADE_SAMPLES = 30


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


def compute_and_write_edge_stats(
    *,
    strategy_key: str,
    days: int = 60,
    min_trade_samples: int = MIN_TRADE_SAMPLES,
    max_age_days: int = 30,
    reuse_stored_data: bool = False,
) -> EdgeStatsComputationResult:
    """Replay `strategy_key`'s real entry/exit rules over persisted OHLCV
    history and, if enough trade samples exist, write the resulting
    win_rate/average_win/average_loss as the active edge-stats artifact.

    Never writes to a table Execution/Gatekeeper read directly -- only ever
    produces/overwrites the local `artifacts/signal_edge_stats/<strategy_key>/
    active.json` pointer that `services/execution/signal_edge_stats.py` reads
    read-only, and which fails closed to the raw-bar-return proxy whenever
    missing, stale, or malformed.
    """
    from scripts.run_top20_technical_validation import (
        _closed_four_hour_boundary,
        _load_or_backfill,
        _load_stored,
        _template,
    )
    from services.execution.bootstrap import AUTO_PAPER_TECHNICAL_KEY, AUTO_PAPER_TECHNICAL_RULES
    from services.execution.signal_edge_stats import EDGE_STATS_ARTIFACT_DIR
    from services.validation.technical_replay import TechnicalStrategyValidationService
    from shared.models import Timeframe

    if strategy_key != AUTO_PAPER_TECHNICAL_KEY:
        raise ValueError(f"only {AUTO_PAPER_TECHNICAL_KEY!r} entry rules are wired up for this replay right now")

    end_at = _closed_four_hour_boundary(datetime.now(UTC))
    market_data = (
        _load_stored(days=days, end_at=end_at) if reuse_stored_data else _load_or_backfill(days=days, end_at=end_at)
    )
    strategy = _template(
        strategy_key=AUTO_PAPER_TECHNICAL_KEY,
        rules=AUTO_PAPER_TECHNICAL_RULES,
        timeframe=Timeframe.M15,
    )
    metrics = TechnicalStrategyValidationService(max_workers=8).replay(strategy=strategy, market_data=market_data)

    if metrics.total_trades < min_trade_samples:
        return EdgeStatsComputationResult(
            accepted=False,
            strategy_key=strategy_key,
            total_trades=metrics.total_trades,
            win_rate=metrics.win_rate,
            average_win=metrics.average_win,
            average_loss=metrics.average_loss,
            min_trade_samples=min_trade_samples,
        )

    model_dir = EDGE_STATS_ARTIFACT_DIR / strategy_key
    model_dir.mkdir(parents=True, exist_ok=True)
    evaluation_start = metrics.evaluation_start.isoformat() if metrics.evaluation_start else None
    evaluation_end = metrics.evaluation_end.isoformat() if metrics.evaluation_end else None
    pointer = {
        "computed_at": datetime.now(UTC).isoformat(),
        "sample_count": metrics.total_trades,
        "win_rate": metrics.win_rate,
        "average_win": metrics.average_win,
        "average_loss": metrics.average_loss,
        "evaluation_start": evaluation_start,
        "evaluation_end": evaluation_end,
        "max_age_days": max_age_days,
    }
    artifact_path = model_dir / "active.json"
    artifact_path.write_text(json.dumps(pointer, indent=2), encoding="utf-8")

    return EdgeStatsComputationResult(
        accepted=True,
        strategy_key=strategy_key,
        total_trades=metrics.total_trades,
        win_rate=metrics.win_rate,
        average_win=metrics.average_win,
        average_loss=metrics.average_loss,
        min_trade_samples=min_trade_samples,
        artifact_path=str(artifact_path),
        evaluation_start=evaluation_start,
        evaluation_end=evaluation_end,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strategy-key", required=True)
    parser.add_argument("--days", type=int, default=60)
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--reuse-stored-data", action="store_true")
    parser.add_argument("--min-trade-samples", type=int, default=MIN_TRADE_SAMPLES)
    parser.add_argument("--max-age-days", type=int, default=30)
    args = parser.parse_args()

    if args.database_url:
        os.environ["POSTGRES_URL"] = args.database_url

    try:
        result = compute_and_write_edge_stats(
            strategy_key=args.strategy_key,
            days=args.days,
            min_trade_samples=args.min_trade_samples,
            max_age_days=args.max_age_days,
            reuse_stored_data=args.reuse_stored_data,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    print(
        f"total_trades={result.total_trades} win_rate={result.win_rate:.4f} "
        f"average_win={result.average_win:.6f} average_loss={result.average_loss:.6f}"
    )

    if not result.accepted:
        print(
            f"REJECTED: {result.total_trades} trades < required minimum {result.min_trade_samples} -- "
            "not enough real trade history for a reliable edge estimate. "
            "net_edge_after_cost will keep using the raw-bar-return proxy."
        )
        return 1

    print(f"ACCEPTED: wrote {result.artifact_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
