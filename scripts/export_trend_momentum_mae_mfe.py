"""Export MAE/MFE diagnostics for the validated trend-momentum baseline.

This is an offline audit. It never updates an active manifest or execution
configuration.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from statistics import median

from services.data import DataRepository
from services.database import get_session_factory
from services.strategy_library.candidates.registry import get_candidate
from services.validation.technical_replay import TechnicalStrategyValidationService
from shared.models import StrategyContract, StrategyRules, Timeframe

SYMBOLS = ("BTC/USDT", "ETH/USDT")


def _closed_boundary(now: datetime) -> datetime:
    return now.astimezone(UTC).replace(minute=(now.minute // 15) * 15, second=0, microsecond=0)


def _load_stored(*, days: int, end_at: datetime) -> dict:
    start_at = end_at - timedelta(days=days)
    with get_session_factory()() as session:
        repository = DataRepository(session)
        return {
            symbol: {
                timeframe: repository.list_ohlcv_bars(
                    symbol=symbol,
                    timeframe=timeframe,
                    start_at=start_at,
                    end_at=end_at,
                )
                for timeframe in ("15m", "1h", "4h")
            }
            for symbol in SYMBOLS
        }


def _quantile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * percentile
    lower, upper = int(index), min(int(index) + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (index - lower)


def _summary(trades: list[dict]) -> dict:
    wins = [item["mfe_r"] for item in trades if float(item["net_return"]) > 0]
    losses = [item["mae_r"] for item in trades if float(item["net_return"]) < 0]
    takeprofits = [item for item in trades if item["exit_reason"] == "takeprofit"]
    lagging_losses = [
        item
        for item in trades
        if float(item["net_return"]) < 0 and float(item["mfe_r"]) <= 0.25 and int(item["bars_held"]) <= 4
    ]
    return {
        "trade_count": len(trades),
        "winning_mfe_r": {
            "count": len(wins),
            "median": median(wins) if wins else None,
            "p75": _quantile(wins, 0.75),
            "p90": _quantile(wins, 0.90),
        },
        "losing_mae_r": {
            "count": len(losses),
            "median": median(losses) if losses else None,
            "p25": _quantile(losses, 0.25),
            "p10": _quantile(losses, 0.10),
        },
        "takeprofit_mfe": {
            "count": len(takeprofits),
            "above_2r_count": sum(float(item["mfe_r"]) > 2.0 for item in takeprofits),
            "above_2r_fraction": (
                sum(float(item["mfe_r"]) > 2.0 for item in takeprofits) / len(takeprofits) if takeprofits else None
            ),
            "median": median([float(item["mfe_r"]) for item in takeprofits]) if takeprofits else None,
        },
        "recommendations": {
            "test_trailing": len(takeprofits) >= 10 and (median([float(item["mfe_r"]) for item in takeprofits]) >= 3.0),
            "test_early_entry": len(losses) >= 10 and len(lagging_losses) / len(losses) >= 0.6,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--output-dir", type=Path, default=Path("docs/audits"))
    args = parser.parse_args()
    os.environ["POSTGRES_URL"] = args.database_url
    end_at = _closed_boundary(datetime.now(UTC))
    market_data = _load_stored(days=args.days, end_at=end_at)
    candidate = get_candidate("trend_momentum_v1")
    strategy = StrategyContract(
        strategy_id="offline:trend_momentum_v1",
        strategy_key="trend_momentum_v1",
        source="offline:mae_mfe_audit",
        core_thesis=candidate.hypothesis,
        symbol_scope=list(SYMBOLS),
        timeframe=Timeframe.M15,
        rules=StrategyRules(**candidate.get_config()),
    )
    metrics = TechnicalStrategyValidationService(max_workers=2).replay(
        strategy=strategy,
        market_data=market_data,
    )
    rows = [trade.as_dict() for trade in metrics.trades]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{datetime.now(UTC).date().isoformat()}-trend_momentum_v1-mae_mfe"
    csv_path = args.output_dir / f"{stem}.csv"
    json_path = args.output_dir / f"{stem}.json"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]) if rows else ["symbol", "mae_r", "mfe_r"])
        writer.writeheader()
        writer.writerows(rows)
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "candidate_id": candidate.candidate_id,
        "symbols": list(SYMBOLS),
        "days_requested": args.days,
        "metrics": metrics.as_dict(),
        "summary": _summary(rows),
        "csv_path": str(csv_path),
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(csv_path)
    print(json_path)
    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
