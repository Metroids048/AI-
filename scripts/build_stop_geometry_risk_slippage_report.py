"""Build read-only stop geometry and realized-R attribution evidence."""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "artifacts/strategy_refactor/reports/2026-08-15-loss-structure-table.json"
OUTPUT = ROOT / "docs/evidence/strategy-research/2026-08-17-stop-geometry-risk-slippage.json"


def main() -> int:
    source = json.loads(SOURCE.read_text(encoding="utf-8"))
    rows = source["rows"]
    stop_rows = [row for row in rows if row["exit_reason"] == "STOP"]
    realized_r = [Decimal(str(row["realized_r"])) for row in stop_rows]
    by_symbol: dict[str, list[dict]] = defaultdict(list)
    for row in stop_rows:
        by_symbol[row["symbol"]].append(row)

    floor_hits: dict[str, dict[str, object]] = {}
    for symbol, symbol_rows in by_symbol.items():
        distances = [
            abs(Decimal(str(row["entry_price"])) - Decimal(str(row["decision_close"])))
            / Decimal(str(row["entry_price"]))
            for row in symbol_rows
        ]
        floor_hits[symbol] = {
            "stop_count": len(symbol_rows),
            "raw_distance_available": False,
            "final_stop_distance_available": False,
            "floor_hit_rate": None,
            "reason": "source table has entry/decision OHLCV but not persisted initial stop price",
            "observed_entry_to_decision_close_pct_median": str(statistics.median(distances)) if distances else None,
        }

    report = {
        "status": "RESEARCH_COMPLETE_WITH_LIMITATIONS",
        "holdout_accessed": False,
        "source": str(SOURCE.relative_to(ROOT)),
        "stop_geometry_001": {
            "stop_episode_count": len(stop_rows),
            "floor": "0.35%",
            "floor_hit_rate": None,
            "grouped_by_symbol": floor_hits,
            "limitation": "The immutable loss structure table does not contain the persisted initial stop geometry or ATR snapshot; floor dominance cannot be truthfully computed from this artifact alone.",
            "live_geometry_changed": False,
        },
        "risk_slippage_001": {
            "stop_realized_r_distribution": {
                "count": len(realized_r),
                "median": str(statistics.median(realized_r)) if realized_r else None,
                "p90": str(statistics.quantiles(realized_r, n=10, method="inclusive")[8])
                if len(realized_r) >= 2
                else None,
                "p95": str(statistics.quantiles(realized_r, n=20, method="inclusive")[18])
                if len(realized_r) >= 2
                else None,
                "max_loss": str(min(realized_r)) if realized_r else None,
            },
            "component_breakdown": {
                "gross_price_move": "UNKNOWN",
                "trigger_to_fill_slippage": "UNKNOWN",
                "fees": "UNKNOWN",
                "funding": "UNKNOWN",
                "rounding": "UNKNOWN",
                "protection_latency": "UNKNOWN",
            },
            "eth_minus_1_2336R": "not present at exactly this value in the source artifact; closest confirmed stop rows are retained in source evidence",
            "xrp_minus_1_2356R": "not present in the 30-episode source artifact; no fabricated attribution",
            "limitation": "The available report contains realized R, MFE, MAE and net PnL but lacks per-fill fee/funding/trigger/fill timestamps needed for causal decomposition.",
        },
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
