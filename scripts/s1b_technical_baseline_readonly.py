"""S1B: read-only technical replay baseline for armed/legacy candidates.

Research-only. This script MUST NOT write any edge-stats artifact, manifest,
ConfigSnapshot, or runtime state. It reads the sealed history database in
read-only mode, replays a candidate over an explicit window that ends strictly
before FINAL_HOLDOUT_START, and prints/writes a provenance-stamped report.

Why not scripts/compute_signal_edge_stats.py: that helper writes
artifacts/signal_edge_stats/<key>/<candidate>/<symbol>/active.json, which is the
exact path load_active_edge_stats() reads at runtime. Writing it would both
overwrite the trend_momentum_v1 comparison baseline and unblock the
currently fail-closed production directional lane.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import subprocess
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from services.execution.signal_edge_stats import strategy_rules_hash
from services.strategy_library.candidates.registry import get_candidate
from services.validation.technical_replay import TechnicalStrategyValidationService
from shared.models import Exchange, OHLCVBar, StrategyRules, Timeframe

FINAL_HOLDOUT_START = datetime(2026, 1, 29, tzinfo=UTC)
SYMBOLS = ("BTC/USDT", "ETH/USDT")
TIMEFRAMES = ("15m", "1h", "4h")
WARMUP_BARS = 80
STRATEGY_KEY = "auto_paper_mature_templates"


def _load_bars(connection: sqlite3.Connection, *, symbol: str, timeframe: str, end_at: datetime) -> list[OHLCVBar]:
    rows = connection.execute(
        """
        SELECT time, open, high, low, close, volume
        FROM ohlcv_bars
        WHERE symbol = ? AND timeframe = ? AND time < ?
        ORDER BY time
        """,
        (symbol, timeframe, end_at.replace(tzinfo=None).isoformat(sep=" ")),
    ).fetchall()
    bars: list[OHLCVBar] = []
    for row in rows:
        raw = str(row[0])
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        bars.append(
            OHLCVBar(
                symbol=symbol,
                exchange=Exchange.BINANCE,
                timeframe=Timeframe(timeframe),
                time=parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC),
                open=Decimal(str(row[1])),
                high=Decimal(str(row[2])),
                low=Decimal(str(row[3])),
                close=Decimal(str(row[4])),
                volume=Decimal(str(row[5])),
            )
        )
    return bars


def _template(rules: dict[str, Any]):  # noqa: ANN202
    from scripts.run_top20_technical_validation import _template as build

    return build(strategy_key=STRATEGY_KEY, rules=rules, timeframe=Timeframe.M15)


def _oos_split(service: TechnicalStrategyValidationService, metrics):  # noqa: ANN001, ANN202
    """Reproduce compute_signal_edge_stats._oos_metrics: last 30% of the window."""
    if metrics.evaluation_start is None or metrics.evaluation_end is None:
        return metrics
    split_at = metrics.evaluation_start + (metrics.evaluation_end - metrics.evaluation_start) * 0.70
    handler = getattr(service, "_metrics_for_period", None)
    if handler is None:
        return metrics
    return handler(metrics, start_at=split_at, end_at=metrics.evaluation_end)


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only technical replay baseline (S1B).")
    parser.add_argument("--database", type=Path, default=Path(".strategy_refactor_history.db"))
    parser.add_argument("--candidate", action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--end-at",
        type=lambda v: datetime.fromisoformat(v).replace(tzinfo=UTC),
        default=FINAL_HOLDOUT_START,
        help="Exclusive upper bound; must be <= final holdout start.",
    )
    args = parser.parse_args()

    if args.end_at > FINAL_HOLDOUT_START:
        raise SystemExit(f"refusing to read sealed holdout: end_at={args.end_at} > {FINAL_HOLDOUT_START}")
    if args.output.exists():
        raise SystemExit(f"refusing to overwrite research output: {args.output}")
    if not args.database.is_file():
        raise SystemExit(f"database not found: {args.database}")

    commit = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False).stdout.strip()
    digest = hashlib.sha256()
    with args.database.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 22), b""):
            digest.update(chunk)
    database_sha256 = digest.hexdigest()

    service = TechnicalStrategyValidationService(oos_fraction=0.30, walk_forward_windows=3, max_workers=1)
    results: dict[str, Any] = {}

    uri = f"file:{args.database.resolve().as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        market: dict[str, dict[str, list[OHLCVBar]]] = {
            symbol: {
                timeframe: _load_bars(connection, symbol=symbol, timeframe=timeframe, end_at=args.end_at)
                for timeframe in TIMEFRAMES
            }
            for symbol in SYMBOLS
        }

    for candidate_id in args.candidate:
        config = get_candidate(candidate_id).get_config()
        rules_hash = strategy_rules_hash(StrategyRules(**config))
        strategy = _template(config)
        per_symbol: dict[str, Any] = {}
        for symbol in SYMBOLS:
            entry_bars = market[symbol]["15m"]
            if len(entry_bars) <= WARMUP_BARS:
                per_symbol[symbol] = {"error": "insufficient_entry_bars", "bar_count": len(entry_bars)}
                continue
            start_at = entry_bars[WARMUP_BARS].timestamp
            end_at = entry_bars[-1].timestamp
            full = service.replay(
                strategy=strategy,
                market_data={symbol: market[symbol]},
                start_at=start_at,
                end_at=end_at,
            )
            oos = _oos_split(service, full)
            per_symbol[symbol] = {
                "full": full.as_dict(),
                "oos_last_30pct": oos.as_dict(),
                "replay_start_at": start_at.isoformat(),
                "replay_end_at": end_at.isoformat(),
                "entry_bar_count": len(entry_bars),
            }
        results[candidate_id] = {
            "candidate_id": candidate_id,
            "rules_hash": rules_hash,
            "entry_rules": config.get("entry_rules"),
            "exit_rules": config.get("exit_rules"),
            "stoploss_rules": config.get("stoploss_rules"),
            "takeprofit_rules": config.get("takeprofit_rules"),
            "symbols": per_symbol,
        }

    report = {
        "schema_version": 1,
        "artifact_type": "s1b_readonly_technical_baseline",
        "provenance": {
            "git_commit": commit,
            "database_path": str(args.database),
            "database_sha256": database_sha256,
            "evaluation_end_bound": args.end_at.isoformat(),
            "final_holdout_start": FINAL_HOLDOUT_START.isoformat(),
            "final_holdout_accessed": False,
            "engine": "services.validation.technical_replay.TechnicalStrategyValidationService",
            "engine_settings": {"oos_fraction": 0.30, "walk_forward_windows": 3, "warmup_bars": WARMUP_BARS},
            "oos_definition": "trailing 30% of the replay window (matches compute_signal_edge_stats._oos_metrics)",
            "timeframes_loaded": list(TIMEFRAMES),
            "writes_edge_stats_artifact": False,
            "writes_manifest_or_config": False,
            "generated_at": datetime.now(UTC).isoformat(),
        },
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(json.dumps(report, indent=2, default=str)[:4000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
