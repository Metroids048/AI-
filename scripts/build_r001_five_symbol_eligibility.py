"""Build the R-001 five-symbol research eligibility report from immutable evidence."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
R001_ARTIFACT = ROOT / "artifacts/strategy_refactor/r001-20260817-development/proposal-research-report.json"
FAILED_BREAKOUT = ROOT / "artifacts/strategy_refactor/reports/2026-08-15-failed-breakout-reversal-edge.json"
RANGE_SWEEP = ROOT / "artifacts/strategy_refactor/reports/2026-08-15-range-sweep-reversion-edge.json"
DB_PATH = ROOT / ".local_paper_console.db"
OUTPUT = ROOT / "docs/evidence/strategy-research/2026-08-17-r001-five-symbol-eligibility.json"

EXECUTION_SYMBOLS = ("BTC/USDT", "ETH/USDT", "BNB/USDT", "SOL/USDT", "XRP/USDT")
DEVELOPMENT_END = "2026-01-29 00:00:00"
DEVELOPMENT_END = "2026-01-29 00:00:00"
MINIMUM_15M_BARS_FOR_EIGHT_WINDOWS = 12 * 30 * 24 * 4


def _coverage() -> dict[str, dict[str, Any]]:
    connection = sqlite3.connect(f"file:{DB_PATH.resolve().as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        result: dict[str, dict[str, Any]] = {}
        for symbol in EXECUTION_SYMBOLS:
            rows = connection.execute(
                """
                select timeframe, count(*) as bars, min(time) as first_bar, max(time) as last_bar
                from ohlcv_bars
                where symbol = ? and timeframe in ('15m', '1h', '4h') and time < ?
                group by timeframe
                """,
                (symbol, DEVELOPMENT_END),
            ).fetchall()
            result[symbol] = {row["timeframe"]: dict(row) for row in rows}
        return result
    finally:
        connection.close()


def _candidate_status(report: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for candidate_id, payload in report["results"].items():
        portfolio = payload["portfolio"]
        promotion = payload["promotion"]
        observations_complete = bool(portfolio["promotion_observations_complete"])
        has_positive_edge = portfolio["net_expectancy"] > 0 and portfolio["profit_factor"] >= 1.15
        result[candidate_id] = {
            "status": (
                "RESEARCH_VALIDATED"
                if promotion["eligible"]
                else "INSUFFICIENT_EVIDENCE"
                if has_positive_edge or not observations_complete
                else "REJECTED"
            ),
            "total_trades": portfolio["total_trades"],
            "profit_factor": portfolio["profit_factor"],
            "net_expectancy": portfolio["net_expectancy"],
            "positive_windows": sum(
                1
                for window in payload["walk_forward_oos"].values()
                if sum(item["net_expectancy"] for item in window["symbols"].values()) > 0
            ),
            "promotion_observations_complete": observations_complete,
            "failed_requirements": promotion["failed_requirements"],
        }
    return result


def main() -> int:
    replay = json.loads(R001_ARTIFACT.read_text(encoding="utf-8"))
    failed_breakout = json.loads(FAILED_BREAKOUT.read_text(encoding="utf-8"))
    range_sweep = json.loads(RANGE_SWEEP.read_text(encoding="utf-8"))
    coverage = _coverage()
    candidate_results = _candidate_status(replay)

    eligibility: dict[str, dict[str, Any]] = {}
    for symbol in EXECUTION_SYMBOLS:
        bars_15m = int(coverage[symbol].get("15m", {}).get("bars", 0))
        if symbol in {"BNB/USDT", "XRP/USDT"} or bars_15m < MINIMUM_15M_BARS_FOR_EIGHT_WINDOWS:
            eligibility[symbol] = {
                "status": "INSUFFICIENT_SAMPLE_NO_TRADE",
                "reason": "insufficient 15m history for the frozen eight-window walk-forward contract",
                "candidate": None,
            }
        elif symbol == "SOL/USDT":
            eligibility[symbol] = {
                "status": "RESEARCH_PENDING_NO_TRADE",
                "reason": "history exists but no sealed-holdout-safe candidate replay evidence is available",
                "candidate": None,
            }
        else:
            eligible = [
                candidate_id
                for candidate_id, result in candidate_results.items()
                if result["status"] == "RESEARCH_VALIDATED"
            ]
            eligibility[symbol] = {
                "status": "RESEARCH_VALIDATED" if eligible else "NO_VALIDATED_CANDIDATE_NO_TRADE",
                "reason": "candidate replay evidence" if eligible else "all replayed candidates failed promotion gates",
                "candidate": eligible[0] if eligible else None,
            }

    report = {
        "report_type": "R-001_FIVE_SYMBOL_REGIME_RESEARCH_ELIGIBILITY",
        "status": "RESEARCH_COMPLETE_NO_PROMOTION",
        "holdout_results_accessed": False,
        "scope": {"execution_symbols": EXECUTION_SYMBOLS, "development_end": "2026-01-29T00:00:00+00:00"},
        "data_coverage": coverage,
        "candidate_results": candidate_results,
        "preexisting_candidate_evidence": {
            "failed_breakout_reversal_v1": failed_breakout["status"],
            "range_sweep_reversion_v1": range_sweep["status"],
        },
        "eligibility": eligibility,
        "follow_up_research": [
            "STOP_GEOMETRY_001: measure 0.35% floor dominance by symbol/regime without changing live geometry.",
            "RISK_SLIPPAGE_001: decompose realized losses beyond -1R from fill/slippage/fee evidence.",
        ],
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "eligibility": eligibility}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
