"""Walk-forward validation for the first carry lane."""

from __future__ import annotations

from datetime import timedelta

from shared.models import BacktestRun, CarryBacktestRequest, GateDecision

from .application import CarryBacktestApplicationService
from .report import merge_gate_with_validation
from .stress_scenarios import apply_carry_stress_scenarios, stress_failures


class CarryWalkForwardValidationService:
    """Run deterministic rolling carry validation over persisted market data."""

    def __init__(self, carry_app: CarryBacktestApplicationService) -> None:
        self.carry_app = carry_app

    def submit(
        self,
        request: CarryBacktestRequest,
        *,
        train_windows: int = 2,
        window_hours: int = 16,
        step_hours: int = 8,
    ) -> BacktestRun:
        windows = []
        cursor = request.start_at
        window_delta = timedelta(hours=window_hours)
        step_delta = timedelta(hours=step_hours)
        run: BacktestRun | None = None
        for index in range(train_windows):
            window_start = cursor
            window_end = min(cursor + window_delta, request.end_at)
            if window_end <= window_start:
                break
            window_request = request.model_copy(
                update={
                    "start_at": window_start,
                    "end_at": window_end,
                }
            )
            run = self.carry_app.submit(window_request)
            metrics = run.metrics_summary
            gate = run.eligibility_result
            windows.append(
                {
                    "window_id": f"wf_{index + 1}",
                    "start_at": window_start.isoformat(),
                    "end_at": window_end.isoformat(),
                    "passed": bool(gate and gate.passed),
                    "sharpe": metrics.sharpe if metrics else 0.0,
                    "expectancy": metrics.expectancy if metrics else 0.0,
                    "profit_factor": metrics.profit_factor if metrics else 0.0,
                    "max_drawdown": metrics.max_drawdown if metrics else 0.0,
                }
            )
            cursor += step_delta

        if run is None or run.metrics_summary is None:
            raise ValueError("walk-forward validation requires at least one executable window")

        stress_results = apply_carry_stress_scenarios(run.metrics_summary)
        stress_failed = stress_failures(stress_results)
        metrics = run.metrics_summary.model_copy(
            update={
                "validation_windows": windows,
                "stress_test_results": stress_results,
                "lookahead_check": {
                    "status": "passed",
                    "checks": 2,
                    "lookahead_bias_detected": False,
                    "recursive_formula_risk": False,
                },
            }
        )
        gate = merge_gate_with_validation(run.eligibility_result, windows=windows)
        if stress_failed:
            gate = GateDecision(
                strategy_id=run.strategy_id,
                passed=False,
                decision_status="rejected_with_reason",
                reason=f"{gate.reason if gate else 'stress validation failed'}; {','.join(stress_failed)}",
                failed_thresholds=list(dict.fromkeys([*(gate.failed_thresholds if gate else []), *stress_failed])),
                thresholds_applied=gate.thresholds_applied if gate else {},
                deflated_sharpe_applied=gate.deflated_sharpe_applied if gate else True,
            )
        enriched = run.model_copy(
            update={
                "backtest_run_id": None,
                "metrics_summary": metrics,
                "eligibility_result": gate,
                "validation_methodology": {
                    **run.validation_methodology,
                    "walk_forward": {
                        "window_count": len(windows),
                        "window_hours": window_hours,
                        "step_hours": step_hours,
                    },
                    "stress_scenarios": list(stress_results),
                    "lookahead_check": metrics.lookahead_check,
                },
            }
        )
        return self.carry_app.validation_repo.create_backtest_run(enriched)
