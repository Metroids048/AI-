"""Build a read-only Donchian-24 structure table for real V2 losses."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from services.research.exit_policy_shadow.loader import load_bars, load_real_entries


def _dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _row_for_entry(entry: Any, outcome: dict[str, Any], db_path: Path) -> dict[str, Any]:
    bars = load_bars(
        db_path,
        symbol=entry.symbol,
        timeframe="15m",
        start=entry.decision_bar_timestamp - timedelta(minutes=15 * 30),
        end=entry.decision_bar_timestamp + timedelta(minutes=15),
    )
    current_index = next((index for index, bar in enumerate(bars) if bar.time == entry.decision_bar_timestamp), None)
    if current_index is None or current_index < 25:
        return {
            "position_id": entry.position_id,
            "symbol": entry.symbol,
            "side": entry.side,
            "decision_bar": entry.decision_bar_timestamp.isoformat(),
            "exit_reason": outcome.get("exit_reason"),
            "structure_status": "[未知] insufficient point-in-time bars",
        }
    prior = bars[current_index - 24 : current_index]
    current = bars[current_index]
    following = bars[current_index + 1] if current_index + 1 < len(bars) else None
    upper = max(bar.high for bar in prior)
    lower = min(bar.low for bar in prior)
    upper_sweep = current.high > upper and current.close < upper
    lower_sweep = current.low < lower and current.close > lower
    against_entry = (entry.side == "long" and upper_sweep) or (entry.side == "short" and lower_sweep)
    same_side_reclaim = (entry.side == "long" and lower_sweep) or (entry.side == "short" and upper_sweep)
    confirmation_against = False
    if following is not None:
        confirmation_against = (
            entry.side == "long" and upper_sweep and following.low < current.low and following.close < upper
        ) or (entry.side == "short" and lower_sweep and following.high > current.high and following.close > lower)
    return {
        "position_id": entry.position_id,
        "symbol": entry.symbol,
        "side": entry.side,
        "decision_bar": entry.decision_bar_timestamp.isoformat(),
        "entry_price": str(entry.average_fill_price),
        "exit_reason": outcome.get("exit_reason"),
        "net_pnl_usdt": outcome.get("net_pnl_usdt"),
        "realized_r": outcome.get("realized_r"),
        "mfe_r": outcome.get("mfe_r"),
        "mae_r": outcome.get("mae_r"),
        "donchian24_upper": str(upper),
        "donchian24_lower": str(lower),
        "decision_high": str(current.high),
        "decision_low": str(current.low),
        "decision_close": str(current.close),
        "upper_sweep_reclaimed": upper_sweep,
        "lower_sweep_reclaimed": lower_sweep,
        "entry_against_sweep": against_entry,
        "same_side_reclaim": same_side_reclaim,
        "next_bar_confirmation_against_entry": confirmation_against,
        "structure_fact": "[事实] decision-bar OHLCV and Donchian-24 relation from local sealed history",
        "structure_inference": (
            "[推断] trend entry occurred where a failed breakout reversal setup was present"
            if against_entry and confirmation_against
            else "[未知] no confirmed opposite-side failed-breakout pattern at the entry bar"
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=Path(".local_paper_console.db"))
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("artifacts/strategy_refactor/reports/2026-08-15-live-loss-structure-attribution.json"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/strategy_refactor/reports/2026-08-15-loss-structure-table.json"),
    )
    args = parser.parse_args()
    source = json.loads(args.input.read_text(encoding="utf-8"))
    outcomes = {str(row["position_id"]): row for row in source.get("trades", [])}
    entries = {entry.position_id: entry for entry in load_real_entries(args.database.resolve())}
    rows = [
        _row_for_entry(entries[position_id], outcome, args.database.resolve())
        for position_id, outcome in outcomes.items()
        if position_id in entries
    ]
    stop_rows = [row for row in rows if row.get("exit_reason") == "STOP"]
    report = {
        "status": "READ_ONLY",
        "holdout_accessed": False,
        "source_report": str(args.input),
        "episode_count": len(rows),
        "stop_episode_count": len(stop_rows),
        "opposite_sweep_confirmed_count": sum(
            bool(row.get("entry_against_sweep") and row.get("next_bar_confirmation_against_entry")) for row in stop_rows
        ),
        "rows": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                key: report[key]
                for key in ("episode_count", "stop_episode_count", "opposite_sweep_confirmed_count", "holdout_accessed")
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
