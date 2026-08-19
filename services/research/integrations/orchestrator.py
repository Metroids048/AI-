"""Queue consumer for research runs; no production-manifest writes."""

from __future__ import annotations

from typing import Any

from .contracts import ResearchExperimentSpec
from .dataset_export import load_canonical_dataset
from .freqtrade_adapter import FreqtradeValidationAdapter
from .vectorbt_adapter import VectorbtScreenAdapter


class ResearchOrchestrator:
    def __init__(
        self,
        *,
        vectorbt: VectorbtScreenAdapter | None = None,
        freqtrade: FreqtradeValidationAdapter | None = None,
    ) -> None:
        self.vectorbt = vectorbt or VectorbtScreenAdapter()
        self.freqtrade = freqtrade or FreqtradeValidationAdapter()

    def run_pipeline(
        self,
        spec: ResearchExperimentSpec,
        rows: list[dict[str, Any]],
        *,
        run_id: str,
    ) -> dict[str, Any]:
        vector_result = self.vectorbt.screen(spec, rows, run_id=run_id)
        if vector_result.status != "completed":
            return {
                "status": "failed",
                "stage": "vectorbt_screen",
                "vectorbt": vector_result.model_dump(mode="json"),
                "failure_reason": vector_result.failure_reason,
            }
        freq_result = self.freqtrade.validate(
            spec,
            rows,
            run_id=run_id,
            candidate=(vector_result.parameter_plateau.get("top_candidates") or [{}])[0],
        )
        if freq_result.status != "completed":
            return {
                "status": "failed",
                "stage": "freqtrade_validation",
                "vectorbt": vector_result.model_dump(mode="json"),
                "freqtrade": freq_result.model_dump(mode="json"),
                "failure_reason": freq_result.failure_reason or "FREQTRADE_VALIDATION_FAILED",
            }
        if freq_result.lookahead_status != "PASS" or freq_result.recursive_status != "PASS":
            return {
                "status": "failed",
                "stage": "freqtrade_bias_validation",
                "vectorbt": vector_result.model_dump(mode="json"),
                "freqtrade": freq_result.model_dump(mode="json"),
                "failure_reason": "BIAS_GATE_FAILED",
            }
        native_oos = spec.engine_options.get("native_oos")
        if not isinstance(native_oos, dict) or native_oos.get("status") != "PASS" or not native_oos.get("evidence_ref"):
            return {
                "status": "failed",
                "stage": "native_oos",
                "vectorbt": vector_result.model_dump(mode="json"),
                "freqtrade": freq_result.model_dump(mode="json"),
                "failure_reason": "NATIVE_OOS_EVIDENCE_REQUIRED",
            }
        return {
            "status": "completed",
            "stage": "native_oos",
            "vectorbt": vector_result.model_dump(mode="json"),
            "freqtrade": freq_result.model_dump(mode="json"),
            "native_oos": native_oos,
            "promotion_authorized": False,
        }

    def process_queued(self, *, session: Any, rows: list[dict[str, Any]] | None = None) -> dict[str, int]:
        """Process queued records using existing repositories when a worker runs."""
        from services.strategy_library import OptimizationRepository, ValidationRepository

        supplied_rows = rows
        validation_repo = ValidationRepository(session)
        optimization_repo = OptimizationRepository(session)
        completed = failed = 0
        for run in validation_repo.list_backtest_runs():
            if run.run_status != "queued" or run.execution_engine not in {"vectorbt", "freqtrade"}:
                continue
            validation_repo.update_backtest_run(run.backtest_run_id or "", run_status="running")
            methodology = run.validation_methodology
            dataset_path = methodology.get("canonical_dataset_path")
            if not dataset_path or not methodology.get("dataset_hash"):
                validation_repo.update_backtest_run(
                    run.backtest_run_id or "",
                    run_status="failed",
                    validation_methodology={
                        **run.validation_methodology,
                        "research_result": {
                            "status": "failed",
                            "stage": "canonical_dataset",
                            "failure_reason": "CANONICAL_DATASET_REQUIRED",
                        },
                    },
                )
                failed += 1
                continue
            try:
                dataset = load_canonical_dataset(dataset_path)
            except (OSError, ValueError) as exc:
                validation_repo.update_backtest_run(
                    run.backtest_run_id or "",
                    run_status="failed",
                    validation_methodology={
                        **methodology,
                        "research_result": {
                            "status": "failed",
                            "stage": "canonical_dataset",
                            "failure_reason": f"CANONICAL_DATASET_INVALID: {exc}",
                        },
                    },
                )
                failed += 1
                continue
            if dataset["dataset_hash"] != methodology["dataset_hash"]:
                validation_repo.update_backtest_run(
                    run.backtest_run_id or "",
                    run_status="failed",
                    validation_methodology={
                        **methodology,
                        "research_result": {
                            "status": "failed",
                            "stage": "canonical_dataset",
                            "failure_reason": "CANONICAL_DATASET_HASH_MISMATCH",
                        },
                    },
                )
                failed += 1
                continue
            spec = ResearchExperimentSpec(
                strategy_id=run.strategy_id,
                strategy_version=run.version_id,
                strategy_hash=str(methodology.get("strategy_hash") or run.strategy_id),
                dataset_id=str(run.dataset_scope or "database"),
                dataset_hash=str(methodology["dataset_hash"]),
                split_plan=run.sample_split_plan,
                cost_model={"ref": run.cost_model_ref},
                parameter_space=run.parameter_set,
                engine_options=methodology.get("engine_options") or {},
            )
            result = self.run_pipeline(
                spec, supplied_rows if supplied_rows is not None else dataset["rows"], run_id=run.backtest_run_id or ""
            )
            status = "completed" if result["status"] == "completed" else "failed"
            validation_repo.update_backtest_run(
                run.backtest_run_id or "",
                run_status=status,
                validation_methodology={**run.validation_methodology, "research_result": result},
            )
            completed += status == "completed"
            failed += status == "failed"
        for optimization_run in optimization_repo.list_runs():
            if optimization_run.run_status != "queued":
                continue
            summary = optimization_run.best_candidate_summary
            dataset_path = summary.get("canonical_dataset_path")
            dataset_hash = summary.get("dataset_hash")
            if dataset_path and dataset_hash:
                optimization_repo.update_run(optimization_run.optimization_run_id or "", run_status="running")
                try:
                    dataset = load_canonical_dataset(dataset_path)
                    if dataset["dataset_hash"] != dataset_hash:
                        raise ValueError("CANONICAL_DATASET_HASH_MISMATCH")
                    spec = ResearchExperimentSpec(
                        strategy_id=optimization_run.strategy_id,
                        strategy_version=optimization_run.version_id,
                        strategy_hash=str(summary.get("strategy_hash") or optimization_run.strategy_id),
                        dataset_id=str(summary.get("dataset_id") or "database"),
                        dataset_hash=str(dataset_hash),
                        cost_model=summary.get("cost_model") or {},
                        parameter_space=summary.get("parameter_space") or {},
                        engine_options=summary.get("engine_options") or {},
                    )
                    result = self.run_pipeline(
                        spec,
                        supplied_rows if supplied_rows is not None else dataset["rows"],
                        run_id=optimization_run.optimization_run_id or "",
                    )
                    status = "completed" if result["status"] == "completed" else "failed"
                    optimization_repo.update_run(
                        optimization_run.optimization_run_id or "",
                        run_status=status,
                        best_candidate_summary={**summary, "research_result": result},
                    )
                    completed += status == "completed"
                    failed += status == "failed"
                    continue
                except (OSError, ValueError) as exc:
                    failure_reason = f"CANONICAL_DATASET_INVALID: {exc}"
            else:
                failure_reason = "research_worker_requires_canonical_dataset"
            optimization_repo.update_run(
                optimization_run.optimization_run_id or "",
                run_status="failed",
                best_candidate_summary={**summary, "failure_reason": failure_reason},
            )
            failed += 1
        return {"completed": completed, "failed": failed}
