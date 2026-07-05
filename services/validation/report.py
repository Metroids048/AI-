"""Validation report helpers for walk-forward, OOS, and stress diagnostics."""

from __future__ import annotations

from datetime import UTC, datetime
from statistics import mean
from typing import Any

from shared.models import BacktestRun, GateDecision, HypothesisRecord

from .admission import ValidationAdmissionService


def summarize_oos_windows(windows: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize walk-forward windows without hiding failed windows."""

    if not windows:
        return {
            "window_count": 0,
            "passed_windows": 0,
            "pass_rate": 0.0,
            "average_oos_sharpe": 0.0,
            "average_oos_expectancy": 0.0,
        }
    passed = [window for window in windows if window.get("passed")]
    return {
        "window_count": len(windows),
        "passed_windows": len(passed),
        "pass_rate": len(passed) / len(windows),
        "average_oos_sharpe": mean(float(window.get("sharpe", 0.0)) for window in windows),
        "average_oos_expectancy": mean(float(window.get("expectancy", 0.0)) for window in windows),
    }


def build_validation_report(run: BacktestRun, *, hypothesis: HypothesisRecord | None = None) -> dict[str, Any]:
    """Build the API-facing validation report for a persisted BacktestRun."""

    metrics = run.metrics_summary
    gate = run.eligibility_result
    windows = metrics.validation_windows if metrics is not None else []
    promotion_gate = (
        ValidationAdmissionService().assess_backtest_run(run=run, hypothesis=hypothesis).model_dump(mode="json")
        if metrics is not None
        else None
    )
    return {
        "backtest_run_id": run.backtest_run_id,
        "strategy_id": run.strategy_id,
        "run_status": run.run_status,
        "gate": gate.model_dump(mode="json") if gate is not None else None,
        "promotion_gate": promotion_gate,
        "metrics": metrics.model_dump(mode="json") if metrics is not None else None,
        "oos_summary": summarize_oos_windows(windows),
        "stress_test_results": metrics.stress_test_results if metrics is not None else {},
        "lookahead_check": metrics.lookahead_check if metrics is not None else {},
        "generated_at": datetime.now(UTC).isoformat(),
    }


def merge_gate_with_validation(base_gate: GateDecision | None, *, windows: list[dict[str, Any]]) -> GateDecision | None:
    """Reject a run when any OOS window fails, preserving the original gate evidence."""

    if base_gate is None:
        return None
    failed_windows = [window["window_id"] for window in windows if not window.get("passed")]
    if not failed_windows:
        return base_gate
    failed_thresholds = list(dict.fromkeys([*base_gate.failed_thresholds, "walk_forward_oos"]))
    return GateDecision(
        strategy_id=base_gate.strategy_id,
        passed=False,
        decision_status="rejected_with_reason",
        reason=f"{base_gate.reason or 'validation failed'}; failed_oos_windows={','.join(failed_windows)}",
        failed_thresholds=failed_thresholds,
        thresholds_applied=base_gate.thresholds_applied,
        deflated_sharpe_applied=base_gate.deflated_sharpe_applied,
    )
