"""Validation admission rules for Paper/Live promotion."""

from __future__ import annotations

from shared.models import (
    BacktestReport,
    BacktestRun,
    GateDecision,
    HypothesisRecord,
    PodRiskReport,
    ValidationBenchmarkResult,
)


class ValidationAdmissionService:
    """Evaluate whether a backtest has enough evidence for Paper/Live promotion."""

    def assess_backtest(
        self,
        *,
        report: BacktestReport,
        hypothesis: HypothesisRecord | None,
    ) -> GateDecision:
        failed_thresholds: list[str] = []
        gate_sharpe = report.deflated_sharpe if report.deflated_sharpe is not None else report.sharpe
        if gate_sharpe < 1.0:
            failed_thresholds.append("min_deflated_sharpe" if report.deflated_sharpe is not None else "min_sharpe")
        if report.profit_factor < 1.3:
            failed_thresholds.append("min_profit_factor")
        if report.max_drawdown > 0.25:
            failed_thresholds.append("max_drawdown")
        if report.expectancy <= 0:
            failed_thresholds.append("min_expectancy")

        if hypothesis is None or not report.hypothesis_id:
            failed_thresholds.append("missing_hypothesis")
        if not report.benchmark_results:
            failed_thresholds.append("missing_benchmark")
        elif any(not result.passed for result in report.benchmark_results):
            failed_thresholds.append("benchmark_control_failed")
        if not report.validation_windows or not any(bool(window.get("passed")) for window in report.validation_windows):
            failed_thresholds.append("missing_oos_evidence")
        if report.pod_risk_report is None:
            failed_thresholds.append("missing_pod_risk")
        elif not report.pod_risk_report.passed:
            failed_thresholds.append("pod_risk_failed")

        if failed_thresholds:
            return GateDecision(
                strategy_id=report.strategy_id,
                passed=False,
                decision_status="rejected_with_reason",
                reason="; ".join(failed_thresholds),
                failed_thresholds=failed_thresholds,
                deflated_sharpe_applied=report.deflated_sharpe is not None,
            )
        return GateDecision(
            strategy_id=report.strategy_id,
            passed=True,
            decision_status="accepted",
            reason="hypothesis + benchmark + OOS + pod risk evidence complete",
            failed_thresholds=[],
            deflated_sharpe_applied=report.deflated_sharpe is not None,
        )

    def assess_backtest_run(
        self,
        *,
        run: BacktestRun,
        hypothesis: HypothesisRecord | None,
    ) -> GateDecision:
        report = run.metrics_summary
        if report is None:
            return GateDecision(
                strategy_id=run.strategy_id,
                passed=False,
                decision_status="rejected_with_reason",
                reason="missing_metrics_summary",
                failed_thresholds=["missing_metrics_summary"],
            )

        if report.hypothesis_id is None:
            evidence = run.validation_methodology
            report = report.model_copy(
                update={
                    "hypothesis_id": evidence.get("hypothesis_id"),
                    "benchmark_results": report.benchmark_results
                    or (
                        [
                            ValidationBenchmarkResult(
                                benchmark_name="legacy_methodology",
                                benchmark_type="migration_bridge",
                                baseline_return=0.0,
                                strategy_return=0.0,
                                excess_return=0.0,
                                passed=bool(evidence.get("benchmark_passed", False)),
                            )
                        ]
                        if "benchmark_passed" in evidence
                        else []
                    ),
                    "validation_windows": report.validation_windows
                    or (
                        [{"window_id": "legacy_oos", "passed": bool(evidence.get("oos_passed", False))}]
                        if "oos_passed" in evidence
                        else []
                    ),
                    "pod_risk_report": report.pod_risk_report
                    or (
                        PodRiskReport(
                            pod_id=evidence.get("pod_id", "legacy-pod"),
                            passed=bool(evidence.get("pod_risk_passed", False)),
                            violations=[],
                            max_expected_loss=0.0,
                            max_expected_leverage=0.0,
                            data_freshness_ok=True,
                        )
                        if "pod_risk_passed" in evidence
                        else None
                    ),
                }
            )
        return self.assess_backtest(report=report, hypothesis=hypothesis)
