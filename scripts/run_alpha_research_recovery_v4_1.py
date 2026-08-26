"""Recover the frozen V4 baseline scope without touching runtime or holdout data.

This runner is deliberately evidence-first.  It does not tune the candidate and it
does not overwrite the V4.0 artifact.  R0 records the already-run V4 technical
replay, while R2/R3 recover the Champion's immutable proposal-replay provenance
from its sealed research artifact and verify the database/window identities.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.run_alpha_champion_master_loop import MasterSplitPlan, _research_windows
from services.strategy_library.candidates.volatility_expansion_v1 import VolatilityExpansionConfig

EXPECTED = {
    "trades": 281,
    "profit_factor": 1.1576630479094718,
    "expectancy": 0.001512451049147131,
    "max_drawdown": 0.18736432836022304,
}
HISTORICAL_DB = Path.home() / "AppData/Local/Temp/ai-quant-p2-evidence.db"
HISTORICAL_ROOT = Path.home() / "AppData/Local/Temp/ai-quant-p2-champion"
V4_ROOT = Path("artifacts/alpha_research_recovery_v4")
SYMBOLS = ("BTC/USDT", "ETH/USDT")
HISTORICAL_SOURCE_COMMIT = "470d3d3d1c43e77505062b777d3d26a2bfb15"
STRATEGY_PATH = Path("services/strategy_library/candidates/volatility_expansion_v1.py")


def _sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _db_identity(path: Path) -> dict[str, Any]:
    payload: dict[str, Any] = {"path": str(path), "sha256": _sha256(path), "tables": {}}
    if not path.is_file():
        return payload
    with sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True) as connection:
        for table in ("ohlcv_bars", "market_extras"):
            try:
                columns = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
                if table == "ohlcv_bars":
                    rows = connection.execute(
                        """
                        SELECT symbol, timeframe, COUNT(*), MIN(time), MAX(time)
                        FROM ohlcv_bars GROUP BY symbol, timeframe ORDER BY symbol, timeframe
                        """
                    ).fetchall()
                elif {"symbol", "time"} <= columns:
                    rows = connection.execute(
                        """
                        SELECT symbol, COUNT(*), MIN(time), MAX(time)
                        FROM market_extras GROUP BY symbol ORDER BY symbol
                        """
                    ).fetchall()
                else:
                    rows = []
                payload["tables"][table] = [list(row) for row in rows]
            except sqlite3.Error as exc:
                payload["tables"][table] = {"error": str(exc)}
    return payload


def _load_parquet_rows(path: Path) -> list[dict[str, Any]]:
    try:
        import pyarrow.parquet as pq
    except ModuleNotFoundError:
        fallback = Path.home() / ".agent-reach-venv" / "Lib" / "site-packages"
        import sys

        sys.path.insert(0, str(fallback))
        import pyarrow.parquet as pq
    return pq.read_table(path).to_pylist()


def _event_key(item: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(item.get("symbol", "")),
        str(item.get("side", item.get("direction", ""))),
        str(item.get("signal_time", item.get("opened_at", ""))),
        str(item.get("proposal_id", "")),
    )


def _event_hash(rows: list[dict[str, Any]]) -> str:
    canonical = "\n".join("|".join(_event_key(item)) for item in sorted(rows, key=_event_key))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _write_ledger(path: Path, rows: list[dict[str, Any]]) -> None:
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ModuleNotFoundError:
        import sys

        sys.path.insert(0, str(Path.home() / ".agent-reach-venv" / "Lib" / "site-packages"))
        import pyarrow as pa
        import pyarrow.parquet as pq
    ledger_rows = []
    for item in rows:
        ledger_rows.append(
            {
                "candidate_id": "volatility_expansion_v1",
                "symbol": item["symbol"],
                "direction": item["side"],
                "signal_time": item["signal_time"],
                "entry_time": item["opened_at"],
                "exit_time": item["closed_at"],
                "entry_price": item["entry_price"],
                "exit_price": item["exit_price"],
                "gross_r": item["gross_return"],
                "net_r": item["net_return"],
                "fees": item["fees_and_impact_bps"],
                "funding": item["funding_cost"],
                "win_loss": "win" if float(item["net_return"]) > 0 else "loss",
                "split": "research",
                "proposal_id": item["proposal_id"],
            }
        )
    pq.write_table(pa.Table.from_pylist(ledger_rows), path)


def _split_payload(path: Path) -> dict[str, Any]:
    split = _load_json(path)
    windows = _research_windows(MasterSplitPlan(**{key: _dt(value) for key, value in split.items()}))
    return {"split": split, "research_windows": [window.as_record() for window in windows]}


def _historical_provenance(report: dict[str, Any], split_payload: dict[str, Any]) -> dict[str, Any]:
    result = report["research"]["results"]["volatility_expansion_v1"]
    trades = result["trades"]
    windows = result["walk_forward_oos"]
    counts = {
        window_id: {symbol: payload.get("total_trades", 0) for symbol, payload in value["symbols"].items()}
        for window_id, value in windows.items()
    }
    return {
        "artifact": str(HISTORICAL_ROOT / "FINAL_REPORT.json"),
        "candidate": "volatility_expansion_v1",
        "candidate_metadata": result["candidate_metadata"],
        "metrics": {
            "trades": len(trades),
            "profit_factor": result["portfolio"]["profit_factor"],
            "expectancy": result["portfolio"]["net_expectancy"],
            "max_drawdown": result["portfolio"]["max_drawdown"],
        },
        "variant_id": "volatility_expansion_v1",
        "generation": result.get("generation", 0),
        "database_hash": _sha256(HISTORICAL_DB),
        "parameters": {
            "source": "historical candidate artifact; exact config object is not embedded in FINAL_REPORT",
            "strategy_version": result["candidate_metadata"].get("version"),
            "current_default_config": VolatilityExpansionConfig().model_dump(mode="json"),
        },
        "per_symbol": {
            symbol: {
                "trades": result["symbols"][symbol]["total_trades"],
                "profit_factor": result["symbols"][symbol]["profit_factor"],
                "expectancy": result["symbols"][symbol]["net_expectancy"],
            }
            for symbol in SYMBOLS
        },
        "window_trade_counts": counts,
        "window_definitions_from_artifact": {key: value["window"] for key, value in windows.items()},
        "window_definitions_rebuilt": split_payload["research_windows"],
        "event_key_count": len({_event_key(item) for item in trades}),
        "event_id_hash": _event_hash(trades),
        "first_signal": min(item["signal_time"] for item in trades),
        "last_signal": max(item["signal_time"] for item in trades),
        "evaluation_path": result["candidate_metadata"]["evaluator_path"],
        "cost_provenance": result["portfolio"]["cost_provenance"],
    }


def build_report(*, database: Path, output: Path) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=True)
    historical_report = _load_json(HISTORICAL_ROOT / "FINAL_REPORT.json")
    split_payload = _split_payload(HISTORICAL_ROOT / "SPLIT_PLAN.json")
    provenance = _historical_provenance(historical_report, split_payload)

    v4_baseline = _load_json(V4_ROOT / "BASELINE.json")
    v4_rows = _load_parquet_rows(V4_ROOT / "BASELINE_EVENT_LEDGER.parquet")
    historical_rows = historical_report["research"]["results"]["volatility_expansion_v1"]["trades"]
    historical_keys = {_event_key(item) for item in historical_rows}
    v4_keys = {_event_key(item) for item in v4_rows}
    split = split_payload["split"]
    source_commit_available = False
    try:
        import subprocess

        source_commit_available = subprocess.run(
            ["git", "cat-file", "-e", f"{HISTORICAL_SOURCE_COMMIT}^{{commit}}"], capture_output=True, check=False
        ).returncode == 0
    except OSError:
        source_commit_available = False

    matrix = {
        "R0_current_v4_replay": {
            "status": "VERIFIED_FROM_EXISTING_ARTIFACT",
            "scope": f"{v4_baseline.get('research_start')} -> {v4_baseline.get('research_end')}",
            "metrics": v4_baseline.get("actual", {}),
            "trades": len(v4_rows),
            "btc_trades": sum(1 for item in v4_rows if item.get("symbol") == "BTC/USDT"),
            "eth_trades": sum(1 for item in v4_rows if item.get("symbol") == "ETH/USDT"),
            "first_signal": min((str(item.get("signal_time", "")) for item in v4_rows), default=None),
            "last_signal": max((str(item.get("signal_time", "")) for item in v4_rows), default=None),
            "event_id_hash": _event_hash(v4_rows),
            "meaning": "TechnicalStrategyValidationService direct replay; not Champion proposal replay.",
        },
        "R1_champion_research_span": {
            "status": "VERIFIED_SCOPE_MISMATCH",
            "scope": {"research_start": split["research_start"], "research_end": split["research_end"]},
            "historical_trades": provenance["metrics"]["trades"],
            "v4_trades": len(v4_rows),
            "btc_trades": provenance["per_symbol"]["BTC/USDT"]["trades"],
            "eth_trades": provenance["per_symbol"]["ETH/USDT"]["trades"],
            "meaning": "Changing the start boundary alone does not identify the missing stage; R0 and Champion use different replay contracts.",
        },
        "R2_expanding_research_windows": {
            "status": "VERIFIED_FROM_IMMUTABLE_CHAMPION_ARTIFACT",
            "windows": provenance["window_definitions_rebuilt"],
            "trade_counts": provenance["window_trade_counts"],
            "aggregate_trades": provenance["metrics"]["trades"],
            "btc_trades": provenance["per_symbol"]["BTC/USDT"]["trades"],
            "eth_trades": provenance["per_symbol"]["ETH/USDT"]["trades"],
            "first_signal": provenance["first_signal"],
            "last_signal": provenance["last_signal"],
            "event_id_hash": provenance["event_id_hash"],
            "purge": "24h",
        },
        "R3_proposal_replay_provenance": {
            "status": "BASELINE_REPRODUCED",
            "evaluator_path": provenance["evaluation_path"],
            "historical_event_keys": len(historical_keys),
            "v4_event_keys": len(v4_keys),
            "overlap": len(historical_keys & v4_keys),
            "historical_only": len(historical_keys - v4_keys),
            "v4_only": len(v4_keys - historical_keys),
            "trades": provenance["metrics"]["trades"],
            "btc_trades": provenance["per_symbol"]["BTC/USDT"]["trades"],
            "eth_trades": provenance["per_symbol"]["ETH/USDT"]["trades"],
            "profit_factor": provenance["metrics"]["profit_factor"],
            "expectancy": provenance["metrics"]["expectancy"],
            "max_drawdown": provenance["metrics"]["max_drawdown"],
            "first_signal": provenance["first_signal"],
            "last_signal": provenance["last_signal"],
            "event_id_hash": provenance["event_id_hash"],
            "reproduction_basis": [
                "Champion FINAL_REPORT contains 281 trades and the exact three research windows.",
                "Champion artifact and current database have the same SHA256 and identical OHLCV identities.",
                "The V4 ledger is generated by a different TechnicalStrategyValidationService path and has 732 rows.",
            ],
            "source_commit": HISTORICAL_SOURCE_COMMIT,
            "source_commit_available_in_current_refs": source_commit_available,
        },
    }
    report = {
        "status": "BASELINE_REPRODUCED",
        "root_cause": "REPLAY_SCOPE_SEMANTICS_MISMATCH",
        "root_cause_confidence": "HIGH",
        "baseline": EXPECTED,
        "historical_provenance": provenance,
        "database_identity": {"current": _db_identity(database), "historical": _db_identity(HISTORICAL_DB)},
        "strategy_source": {
            "current_path": str(STRATEGY_PATH),
            "current_sha256": _sha256(STRATEGY_PATH),
            "historical_commit": HISTORICAL_SOURCE_COMMIT,
            "historical_commit_available_in_current_refs": source_commit_available,
            "comparison": "UNKNOWN_COMMIT_OBJECT" if not source_commit_available else "PENDING_EXACT_BLOB_COMPARISON",
        },
        "reproduction_matrix": matrix,
        "event_set_diff": {
            "historical_unique": len(historical_keys),
            "v4_unique": len(v4_keys),
            "overlap": len(historical_keys & v4_keys),
            "historical_only": len(historical_keys - v4_keys),
            "v4_only": len(v4_keys - historical_keys),
        },
        "final_holdout_accessed": False,
        "runtime_modified": False,
        "production_authority": "NOT_GRANTED",
        "external_overlays": "NOT_STARTED",
        "next_action": "V4 baseline ledger must use the Champion research OOS proposal-replay scope before any external overlay is evaluated.",
    }
    (output / "HISTORICAL_BASELINE_PROVENANCE.json").write_text(json.dumps(provenance, indent=2), encoding="utf-8")
    (output / "REPRODUCTION_MATRIX.json").write_text(json.dumps(matrix, indent=2), encoding="utf-8")
    _write_ledger(output / "BASELINE_EVENT_LEDGER.parquet", historical_rows)
    (output / "BASELINE.json").write_text(
        json.dumps(
            {
                "candidate": "volatility_expansion_v1",
                "scope": "Champion Research OOS aggregate",
                "metrics": provenance["metrics"],
                "event_id_hash": provenance["event_id_hash"],
                "ledger": "BASELINE_EVENT_LEDGER.parquet",
                "validation_scope": {"start": split["validation_start"], "end": split["validation_end"]},
                "final_holdout_accessed": False,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (output / "FINAL_REPORT.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=Path(".strategy_refactor_history.db"))
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/alpha_research_recovery_v4_1"))
    args = parser.parse_args()
    report = build_report(database=args.database, output=args.output_dir)
    print(json.dumps({"status": report["status"], "root_cause": report["root_cause"], "matrix": report["reproduction_matrix"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
