from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from services.validation.strategy_promotion import (
    FinalHoldoutGuard,
    PromotionMetrics,
    TrialLedger,
    evaluate_promotion,
    stationary_cluster_bootstrap_lcb,
)


def test_stationary_bootstrap_preserves_trade_clusters() -> None:
    clusters = ((Decimal("0.02"), Decimal("0.01")), (Decimal("-0.03"), Decimal("-0.01")))

    result = stationary_cluster_bootstrap_lcb(clusters, n_resamples=100, seed=7)

    assert result.sample_size == 4
    assert result.cluster_count == 2
    assert result.method == "stationary_cluster_bootstrap"
    assert result.expectancy_lcb <= result.expectancy


def test_all_parameter_trials_are_persisted(tmp_path: Path) -> None:
    ledger = TrialLedger(tmp_path / "trials.jsonl")
    ledger.record(
        trial_id="a", strategy_id="failed_breakout_reversal_v1", parameters={"atr_buffer": "0.25"}, status="failed"
    )
    ledger.record(
        trial_id="b", strategy_id="failed_breakout_reversal_v1", parameters={"atr_buffer": "0.35"}, status="completed"
    )

    records = ledger.read_all()

    assert [record["trial_id"] for record in records] == ["a", "b"]
    assert records[0]["status"] == "failed"


def test_optimizer_cannot_read_final_holdout() -> None:
    guard = FinalHoldoutGuard(datetime(2026, 1, 29, tzinfo=UTC))

    guard.assert_development_end(datetime(2026, 1, 29, tzinfo=UTC))
    with pytest.raises(ValueError, match="Final Holdout is sealed"):
        guard.assert_development_end(datetime(2026, 1, 29, 0, 1, tzinfo=UTC))


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("total_trades", 59, "closed_trades_below_60"),
        ("positive_windows", 4, "positive_windows_below_5_of_8"),
        ("profit_factor", 1.34, "profit_factor_below_1_35"),
        ("net_expectancy", -0.0001, "net_expectancy_not_positive"),
        ("max_drawdown", 0.301, "max_drawdown_exceeds_30_percent"),
        ("expectancy_lcb", 0.0, "expectancy_lcb_not_positive"),
    ],
)
def test_promotion_gate_rejects_each_joint_requirement(field: str, value: float, reason: str) -> None:
    metrics = PromotionMetrics(
        win_rate=0.50,
        average_profit_loss_ratio=1.20,
        profit_factor=1.50,
        net_expectancy=0.001,
        max_drawdown=0.15,
        expectancy_lcb=0.0001,
        total_trades=60,
        positive_windows=5,
        net_return=0.1,
        canary_net_return=0.0,
        canary_net_expectancy=0.0,
    ).model_copy(update={field: value})

    result = evaluate_promotion(metrics)

    assert result.eligible is False
    assert reason in result.failed_requirements
