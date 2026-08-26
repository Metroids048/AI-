"""Research-only V4 external-context overlay gate.

V4 intentionally stops before external acquisition when the frozen baseline cannot
be reproduced.  This keeps a historical metric mismatch from being hidden by a new
overlay implementation.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from scripts.run_alpha_champion_master_loop import _load_technical_market_data
from services.data import DataRepository
from services.execution.decision_pipeline import DecisionPipeline
from services.strategy_library.candidates.registry import get_candidate
from services.validation.technical_replay import EXIT_MODE_FIXED_2R, TechnicalStrategyValidationService
from shared.models import StrategyContract, StrategyRules, Timeframe

EXPECTED = {
    "trades": 281,
    "profit_factor": 1.1576630479094718,
    "expectancy": 0.001512451049147131,
    "max_drawdown": 0.18736432836022304,
}
SYMBOLS = ("BTC/USDT", "ETH/USDT")
RESEARCH_START = datetime(2023, 1, 29, tzinfo=UTC)
RESEARCH_END = datetime(2025, 2, 22, 14, 24, tzinfo=UTC)
VALIDATION_START = RESEARCH_END
HOLDOUT_START = datetime(2026, 1, 29, tzinfo=UTC)


def _strategy() -> StrategyContract:
    candidate = get_candidate("volatility_expansion_v1")
    return StrategyContract(
        strategy_id="research:v4:volatility_expansion_v1:baseline",
        strategy_key="volatility_expansion_v1",
        source="research:alpha_recovery_v4",
        core_thesis=candidate.hypothesis,
        symbol_scope=["BTC/USDT", "ETH/USDT"],
        timeframe=Timeframe.M15,
        rules=StrategyRules(**deepcopy(candidate.get_config())),
    )


def _replay(database: Path):
    market_data = _load_technical_market_data(database, end_at=RESEARCH_END)
    service = TechnicalStrategyValidationService(
        pipeline_factory=lambda view: DecisionPipeline(data_repo=cast(DataRepository, view)),
        warmup_bars=80,
        walk_forward_windows=3,
        max_workers=1,
        exit_mode=EXIT_MODE_FIXED_2R,
    )
    return service.replay(
        strategy=_strategy(),
        market_data=market_data,
        start_at=RESEARCH_START,
        end_at=RESEARCH_END,
    )


def _metrics(metrics: Any) -> dict[str, Any]:
    trades = list(metrics.trades)
    wins = [trade.net_return for trade in trades if trade.net_return > 0]
    losses = abs(sum(trade.net_return for trade in trades if trade.net_return < 0))
    return {
        "trades": len(trades),
        "profit_factor": sum(wins) / losses if losses else 0.0,
        "expectancy": sum(trade.net_return for trade in trades) / len(trades) if trades else 0.0,
        "max_drawdown": float(metrics.max_drawdown),
        "net_return": float(metrics.net_return),
        "gross_return": float(metrics.gross_return),
        "win_rate": float(metrics.win_rate),
        "exit_reasons": dict(metrics.ladder_level_hits),
    }


def _split(opened_at: datetime) -> str:
    return "research" if opened_at < VALIDATION_START else "validation"


def _write_ledger(path: Path, metrics: Any) -> None:
    rows = []
    for trade in metrics.trades:
        rows.append(
            {
                "candidate_id": "volatility_expansion_v1",
                "symbol": trade.symbol,
                "direction": trade.side.value,
                "signal_time": trade.opened_at.isoformat(),
                "entry_time": trade.opened_at.isoformat(),
                "exit_time": trade.closed_at.isoformat(),
                "entry_price": trade.entry_price,
                "exit_price": trade.exit_price,
                "gross_r": trade.gross_return,
                "net_r": trade.net_return,
                "fees": trade.fee_bps,
                "slippage": trade.slippage_bps,
                "funding": 0.0,
                "win_loss": "win" if trade.net_return > 0 else "loss",
                "max_adverse_excursion": trade.mae_r,
                "max_favorable_excursion": trade.mfe_r,
                "split": _split(trade.opened_at),
            }
        )
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ModuleNotFoundError:
        # The declared project interpreter has no parquet engine; reuse the existing
        # local read-only environment rather than installing or changing dependencies.
        fallback = Path.home() / ".agent-reach-venv" / "Lib" / "site-packages"
        if not fallback.exists():
            raise RuntimeError("PYARROW_REQUIRED_FOR_BASELINE_EVENT_LEDGER") from None
        sys.path.insert(0, str(fallback))
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as exc:
        raise RuntimeError("PYARROW_REQUIRED_FOR_BASELINE_EVENT_LEDGER") from exc
    pq.write_table(pa.Table.from_pylist(rows), path)


def _database_end(database: Path) -> datetime | None:
    with sqlite3.connect(f"file:{database.resolve().as_posix()}?mode=ro", uri=True) as connection:
        row = connection.execute(
            "SELECT MAX(time) FROM ohlcv_bars WHERE symbol IN ('BTC/USDT','ETH/USDT') AND timeframe='15m'"
        ).fetchone()
    if not row or row[0] is None:
        return None
    parsed = datetime.fromisoformat(str(row[0]).replace("Z", "+00:00"))
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    baseline = _replay(args.database)
    actual = _metrics(baseline)
    _write_ledger(args.output_dir / "BASELINE_EVENT_LEDGER.parquet", baseline)
    comparison = {
        key: {"expected": EXPECTED[key], "actual": actual[key], "delta": actual[key] - EXPECTED[key]}
        for key in EXPECTED
    }
    reproduced = (
        actual["trades"] == EXPECTED["trades"]
        and abs(actual["profit_factor"] - EXPECTED["profit_factor"]) <= 1e-9
        and abs(actual["expectancy"] - EXPECTED["expectancy"]) <= 1e-9
        and abs(actual["max_drawdown"] - EXPECTED["max_drawdown"]) <= 1e-9
    )
    baseline_payload = {
        "candidate": "volatility_expansion_v1",
        "expected": EXPECTED,
        "actual": actual,
        "comparison": comparison,
        "reproduced": reproduced,
        "research_start": RESEARCH_START.isoformat(),
        "research_end": RESEARCH_END.isoformat(),
        "validation_start": VALIDATION_START.isoformat(),
        "holdout_start": HOLDOUT_START.isoformat(),
        "final_holdout_accessed": False,
        "signal_time_semantics": "ReplayTrade exposes entry time; signal_time is recorded as entry_time.",
    }
    (args.output_dir / "BASELINE.json").write_text(json.dumps(baseline_payload, indent=2), encoding="utf-8")
    (args.output_dir / "RESEARCH_PLAN.json").write_text(
        json.dumps(
            {
                "version": "alpha_research_recovery_v4",
                "name": "EXTERNAL_CONTEXT_ALPHA_OVERLAY",
                "baseline": "volatility_expansion_v1",
                "external_sources": ["ALTCOIN_MARKET_BREADTH_V1", "DERIBIT_DVOL_CONTEXT_V1", "FEAR_GREED_CONTEXT_V1"],
                "overlay_only": True,
                "create_trade": False,
                "change_direction": False,
                "final_holdout_accessed": False,
                "status": "BLOCKED_BASELINE_REPRODUCTION" if not reproduced else "READY_FOR_EXTERNAL_AUDIT",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    if not reproduced:
        database_end = _database_end(args.database)
        (args.output_dir / "FINAL_REPORT.json").write_text(
            json.dumps(
                {
                    "status": "BLOCKED_BASELINE_REPRODUCTION",
                    "baseline": baseline_payload,
                    "source_status": {
                        "ALTCOIN_MARKET_BREADTH_V1": "NOT_ACQUIRED",
                        "DERIBIT_DVOL_CONTEXT_V1": "NOT_ACQUIRED",
                        "FEAR_GREED_CONTEXT_V1": "NOT_ACQUIRED",
                    },
                    "final_holdout_accessed": False,
                    "runtime_modified": False,
                    "production_authority": "NOT_GRANTED",
                    "database_end": database_end.isoformat() if database_end else None,
                    "reason": "Frozen historical baseline metrics were not reproduced; external overlays were intentionally not run.",
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        print(json.dumps({"status": "BLOCKED_BASELINE_REPRODUCTION", "actual": actual, "expected": EXPECTED}, indent=2))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
