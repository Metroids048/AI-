from __future__ import annotations

from services.validation.admission import ValidationAdmissionService
from shared.models import (
    BacktestEngine,
    BacktestReport,
    HypothesisRecord,
    PodRiskReport,
    ValidationBenchmarkResult,
)


def _hypothesis() -> HypothesisRecord:
    return HypothesisRecord(
        hypothesis_id="hyp-1",
        strategy_id="strategy-1",
        title="Funding carry stays positive after controls",
        statement="Positive funding carry should outperform passive hold after costs and randomized controls.",
        rationale="Validation admission must remain evidence-first before Paper or Live promotion.",
        benchmark_plan={"benchmarks": ["passive_hold", "randomized_control"]},
        acceptance_criteria={
            "min_deflated_sharpe": 1.0,
            "min_excess_return": 0.0,
            "pod_risk_required": True,
        },
        status="approved",
    )


def _benchmarks(*, passed: bool = True) -> list[ValidationBenchmarkResult]:
    return [
        ValidationBenchmarkResult(
            benchmark_name="passive_hold",
            benchmark_type="market_baseline",
            baseline_return=0.04,
            strategy_return=0.11,
            excess_return=0.07,
            passed=passed,
        ),
        ValidationBenchmarkResult(
            benchmark_name="randomized_control",
            benchmark_type="strict_random_control",
            baseline_return=-0.01,
            strategy_return=0.11,
            excess_return=0.12,
            passed=passed,
        ),
    ]


def _pod_risk(*, passed: bool = True) -> PodRiskReport:
    return PodRiskReport(
        pod_id="btc-perp-pod",
        passed=passed,
        violations=[] if passed else ["exposure_limit"],
        max_expected_loss=0.02,
        max_expected_leverage=1.5,
        data_freshness_ok=True,
    )


def test_validation_admission_rejects_high_raw_sharpe_when_dsr_fails() -> None:
    report = BacktestReport(
        strategy_id="strategy-1",
        engine=BacktestEngine.FREQTRADE,
        sharpe=2.4,
        deflated_sharpe=0.8,
        profit_factor=1.7,
        max_drawdown=0.08,
        win_rate=0.58,
        expectancy=0.12,
        validation_windows=[{"window_id": "oos-1", "passed": True, "sharpe": 1.2, "expectancy": 0.02}],
        benchmark_results=_benchmarks(),
        hypothesis_id="hyp-1",
        pod_risk_report=_pod_risk(),
    )

    decision = ValidationAdmissionService().assess_backtest(
        report=report,
        hypothesis=_hypothesis(),
    )

    assert decision.decision_status == "rejected_with_reason"
    assert "min_deflated_sharpe" in decision.failed_thresholds


def test_validation_admission_requires_hypothesis_benchmark_oos_and_pod_evidence() -> None:
    report = BacktestReport(
        strategy_id="strategy-1",
        engine=BacktestEngine.FREQTRADE,
        sharpe=1.8,
        deflated_sharpe=1.4,
        profit_factor=1.6,
        max_drawdown=0.10,
        win_rate=0.54,
        expectancy=0.08,
        validation_windows=[],
        benchmark_results=[],
        hypothesis_id=None,
        pod_risk_report=None,
    )

    decision = ValidationAdmissionService().assess_backtest(report=report, hypothesis=None)

    assert decision.decision_status == "rejected_with_reason"
    assert "missing_hypothesis" in decision.failed_thresholds
    assert "missing_benchmark" in decision.failed_thresholds
    assert "missing_oos_evidence" in decision.failed_thresholds
    assert "missing_pod_risk" in decision.failed_thresholds

