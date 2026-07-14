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
from datetime import UTC, datetime

MIN_TRADE_SAMPLES = 30


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

    if args.strategy_key != AUTO_PAPER_TECHNICAL_KEY:
        raise SystemExit(
            f"only {AUTO_PAPER_TECHNICAL_KEY!r} entry rules are wired up for this replay right now"
        )

    end_at = _closed_four_hour_boundary(datetime.now(UTC))
    market_data = (
        _load_stored(days=args.days, end_at=end_at)
        if args.reuse_stored_data
        else _load_or_backfill(days=args.days, end_at=end_at)
    )
    strategy = _template(
        strategy_key=AUTO_PAPER_TECHNICAL_KEY,
        rules=AUTO_PAPER_TECHNICAL_RULES,
        timeframe=Timeframe.M15,
    )
    metrics = TechnicalStrategyValidationService(max_workers=8).replay(strategy=strategy, market_data=market_data)

    print(
        f"total_trades={metrics.total_trades} win_rate={metrics.win_rate:.4f} "
        f"average_win={metrics.average_win:.6f} average_loss={metrics.average_loss:.6f}"
    )

    if metrics.total_trades < args.min_trade_samples:
        print(
            f"REJECTED: {metrics.total_trades} trades < required minimum {args.min_trade_samples} -- "
            "not enough real trade history for a reliable edge estimate. "
            "net_edge_after_cost will keep using the raw-bar-return proxy."
        )
        return 1

    model_dir = EDGE_STATS_ARTIFACT_DIR / args.strategy_key
    model_dir.mkdir(parents=True, exist_ok=True)
    pointer = {
        "computed_at": datetime.now(UTC).isoformat(),
        "sample_count": metrics.total_trades,
        "win_rate": metrics.win_rate,
        "average_win": metrics.average_win,
        "average_loss": metrics.average_loss,
        "evaluation_start": metrics.evaluation_start.isoformat() if metrics.evaluation_start else None,
        "evaluation_end": metrics.evaluation_end.isoformat() if metrics.evaluation_end else None,
        "max_age_days": args.max_age_days,
    }
    (model_dir / "active.json").write_text(json.dumps(pointer, indent=2), encoding="utf-8")
    print(f"ACCEPTED: wrote {model_dir / 'active.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
