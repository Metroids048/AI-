from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from services.validation.strategy_promotion import (
    FinalHoldoutGuard,
    ProfitabilityRecoveryMetrics,
    PromotionMetrics,
    ResearchTrial,
    ResearchTrialRegistry,
    TrialLedger,
    evaluate_profitability_recovery,
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


def test_research_trial_registry_enforces_budget_and_immutable_definitions(tmp_path: Path) -> None:
    registry = ResearchTrialRegistry(tmp_path / "research-trials.jsonl")
    trial = ResearchTrial(
        hypothesis_id="g5-h1",
        hypothesis_family="REGIME_SELECTION",
        exact_change="Expansion-only closed-bar candidate; no geometry change.",
        economic_rationale="Expansion was the only positive observed regime slice.",
        development_period="2023-01-29..2025-07-29",
        validation_period="2025-07-29..2026-01-29",
        final_holdout_accessed=False,
        number_of_prior_trials=6,
    )

    registry.register(trial)
    registry.register(trial)

    assert registry.trial_count == 1
    with pytest.raises(ValueError, match="immutable"):
        registry.register(trial.model_copy(update={"exact_change": "a different change"}))


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


def _recovery_metrics(**overrides: object) -> ProfitabilityRecoveryMetrics:
    payload: dict[str, object] = {
        "total_trades": 80,
        "per_symbol_trades": {"BTC/USDT": 40, "ETH/USDT": 40},
        "net_return": 0.12,
        "net_expectancy": 0.0015,
        "profit_factor": 1.35,
        "per_symbol_profit_factor": {"BTC/USDT": 1.20, "ETH/USDT": 1.10},
        "positive_windows": 5,
        "total_windows": 8,
        "max_drawdown": 0.18,
        "final_holdout_net_expectancy": 0.001,
        "cost_stress_1_5x_net_expectancy": 0.0005,
        "cost_stress_1_5x_profit_factor": 1.05,
        "one_minute_net_expectancy": 0.0004,
        "freqtrade_lookahead_passed": True,
        "freqtrade_recursive_passed": True,
        "vectorbt_neighborhood_passed": True,
        "promotion_observations_complete": True,
        "funding_observed": True,
        "slippage_observed": True,
        "trade_attribution_complete": True,
        "expectancy_lcb": 0.0001,
    }
    payload.update(overrides)
    return ProfitabilityRecoveryMetrics.model_validate(payload)


def test_profitability_recovery_gate_accepts_complete_evidence() -> None:
    result = evaluate_profitability_recovery(_recovery_metrics())

    assert result.eligible is True
    assert result.failed_requirements == ()


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("total_trades", 79, "portfolio_trades_below_80"),
        ("per_symbol_trades", {"BTC/USDT": 29, "ETH/USDT": 50}, "symbol_trades_below_30:BTC/USDT"),
        ("profit_factor", 1.19, "profit_factor_below_1_20"),
        ("max_drawdown", 0.21, "max_drawdown_exceeds_20_percent"),
        ("cost_stress_1_5x_net_expectancy", 0.0, "cost_stress_1_5x_expectancy_not_positive"),
        ("one_minute_net_expectancy", -0.001, "one_minute_fidelity_not_positive"),
        ("vectorbt_neighborhood_passed", False, "vectorbt_neighborhood_not_stable"),
    ],
)
def test_profitability_recovery_gate_rejects_each_hard_requirement(field: str, value: object, reason: str) -> None:
    result = evaluate_profitability_recovery(_recovery_metrics(**{field: value}))

    assert result.eligible is False
    assert reason in result.failed_requirements
