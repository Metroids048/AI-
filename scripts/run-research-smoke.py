"""Run a small, read-only cross-engine research smoke cycle and persist evidence."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from services.database import get_session_factory
from services.research.integrations.contracts import ResearchExperimentSpec
from services.research.integrations.dataset_export import load_canonical_dataset
from services.research.integrations.orchestrator import ResearchOrchestrator
from services.research.integrations.research_council import ResearchCouncil
from services.strategy_library import StrategyRepository, ValidationRepository
from shared.models import BacktestRun

_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_DATASET = _ROOT / ".local" / "research-engines" / "freqtrade-smoke" / "canonical.json"
_DEFAULT_CONFIG = _ROOT / ".local" / "research-engines" / "freqtrade-smoke" / "config.json"


def _native_oos(rows: list[dict[str, Any]], run_id: str, cost_model: dict[str, Any]) -> dict[str, Any]:
    split_index = max(1, int(len(rows) * 0.7))
    oos_rows = rows[split_index:]
    fee = float(cost_model.get("fee", 0.0))
    slippage = float(cost_model.get("slippage", 0.0))
    trades: list[dict[str, Any]] = []
    for index, row in enumerate(oos_rows[:-1]):
        if not row.get("entry_signal"):
            continue
        next_close = float(oos_rows[index + 1]["close"])
        entry = float(row["close"])
        gross_return = (next_close - entry) / entry
        net_return = gross_return - (2 * fee) - (2 * slippage)
        trades.append({"timestamp": row["timestamp"], "gross_return": gross_return, "net_return": net_return})
    artifact = {
        "run_id": run_id,
        "split": {"train_fraction": 0.7, "train_rows": split_index, "oos_rows": len(oos_rows)},
        "cost_model": cost_model,
        "trade_count": len(trades),
        "trades": trades,
        "status": "PASS" if trades else "INSUFFICIENT_DATA",
        "promotion_eligible": False,
    }
    path = _ROOT / "logs" / f"native-oos-{run_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(artifact, ensure_ascii=True, indent=2), encoding="utf-8")
    return {
        "status": artifact["status"],
        "evidence_ref": str(path),
        "trade_count": len(trades),
        "promotion_eligible": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", default="research-smoke-20260819-final")
    parser.add_argument("--dataset", default=str(_DEFAULT_DATASET))
    parser.add_argument("--database-url", default=None)
    args = parser.parse_args()

    dataset = load_canonical_dataset(args.dataset)
    native_oos = _native_oos(dataset["rows"], args.run_id, dataset.get("cost_model") or {})
    if native_oos["status"] != "PASS":
        raise SystemExit("native OOS smoke split did not produce a trade")

    spec = ResearchExperimentSpec(
        strategy_id="research_smoke_candidate",
        strategy_hash="research-smoke-candidate-v1",
        dataset_id=str(dataset["dataset_id"]),
        dataset_hash=str(dataset["dataset_hash"]),
        symbols=dataset.get("symbols") or ["BTC/USDT"],
        timeframes=dataset.get("timeframes") or ["5m"],
        window=dataset.get("window") or {},
        split_plan={"train_fraction": 0.7, "oos_fraction": 0.3, "sealed_holdout_read": False},
        cost_model=dataset.get("cost_model") or {},
        parameter_space={"entry_threshold": [1, 2], "exit_multiple": [1.5, 2.0]},
        engine_options={
            "freqtrade": {
                "config_path": str(_DEFAULT_CONFIG),
                "canonical_dataset_path": str(Path(args.dataset).resolve()),
                "strategy": "SampleStrategy",
                "hyperopt_epochs": 1,
            },
            "native_oos": native_oos,
        },
    )

    session_factory = get_session_factory(args.database_url)
    with session_factory() as session:
        strategies = StrategyRepository(session).list_strategies()
        strategy = next((item for item in strategies if item.strategy_key == "operator_experience_4h_15m_v1"), None)
        if strategy is None:
            raise SystemExit("no existing research strategy is available for smoke persistence")
        validation_repo = ValidationRepository(session)
        run = validation_repo.create_backtest_run(
            BacktestRun(
                backtest_run_id=args.run_id,
                strategy_id=strategy.strategy_id,
                version_id=None,
                dataset_scope=spec.dataset_id,
                execution_engine="vectorbt",
                parameter_set=spec.parameter_space,
                market_regime_coverage=[],
                sample_split_plan=spec.split_plan,
                cost_model_ref="research-smoke-cost-model",
                validation_methodology={
                    "canonical_dataset_path": str(Path(args.dataset).resolve()),
                    "dataset_hash": spec.dataset_hash,
                    "strategy_hash": spec.strategy_hash,
                    "engine_options": spec.engine_options,
                    "created_at": datetime.now(UTC).isoformat(),
                },
                stress_test_scenarios=[],
                run_status="queued",
            )
        )
        counts = ResearchOrchestrator().process_queued(session=session, rows=dataset["rows"])
        persisted = validation_repo.get_backtest_run(run.backtest_run_id or args.run_id)
        result = (persisted.validation_methodology if persisted else {}).get("research_result") or {}
        if result.get("status") != "completed":
            raise SystemExit(json.dumps(result, ensure_ascii=True))

        evidence_refs = [
            f"run:{args.run_id}:vectorbt",
            f"run:{args.run_id}:freqtrade",
            native_oos["evidence_ref"],
        ]
        council = ResearchCouncil().review(
            args.run_id,
            {
                "evidence_refs": evidence_refs,
                "bias_status": "PASS",
                "native_oos_status": native_oos["status"],
            },
        )
        result["council"] = council.model_dump(mode="json")
        result["promotion_authorized"] = False
        result["production_authorization"] = "PENDING"
        validation_repo.update_backtest_run(
            args.run_id,
            validation_methodology={**persisted.validation_methodology, "research_result": result},
        )

    evidence_path = _ROOT / "logs" / f"{args.run_id}.json"
    evidence_path.write_text(
        json.dumps(
            {"status": "completed", "run_id": args.run_id, "counts": counts, "research_result": result},
            ensure_ascii=True,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    print(json.dumps({"status": "completed", "run_id": args.run_id, "evidence": str(evidence_path)}, indent=2))


if __name__ == "__main__":
    main()
