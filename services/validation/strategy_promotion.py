"""Sealed-holdout validation primitives for research candidate promotion."""

from __future__ import annotations

import json
import random
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from statistics import mean
from typing import Any

from pydantic import Field

from shared.models import PlatformModel


class BootstrapResult(PlatformModel):
    method: str
    sample_size: int = Field(ge=0)
    cluster_count: int = Field(ge=0)
    expectancy: float
    expectancy_lcb: float
    confidence: float = Field(ge=0, le=1)


class PromotionMetrics(PlatformModel):
    total_trades: int = 0
    positive_windows: int = 0
    total_windows: int = 8
    net_return: float = 0.0
    canary_net_return: float = 0.0
    canary_net_expectancy: float = 0.0
    win_rate: float
    average_profit_loss_ratio: float
    profit_factor: float
    net_expectancy: float
    max_drawdown: float
    expectancy_lcb: float


class PromotionResult(PlatformModel):
    eligible: bool
    failed_requirements: tuple[str, ...]


class ProfitabilityRecoveryMetrics(PlatformModel):
    """Evidence required before a research Champion may enter Production review."""

    total_trades: int = Field(default=0, ge=0)
    per_symbol_trades: dict[str, int] = Field(default_factory=dict)
    net_return: float = 0.0
    net_expectancy: float = 0.0
    profit_factor: float = 0.0
    per_symbol_profit_factor: dict[str, float] = Field(default_factory=dict)
    positive_windows: int = Field(default=0, ge=0)
    total_windows: int = Field(default=8, ge=1)
    max_drawdown: float = 0.0
    final_holdout_net_expectancy: float = 0.0
    cost_stress_1_5x_net_expectancy: float = 0.0
    cost_stress_1_5x_profit_factor: float = 0.0
    one_minute_net_expectancy: float = 0.0
    freqtrade_lookahead_passed: bool = False
    freqtrade_recursive_passed: bool = False
    vectorbt_neighborhood_passed: bool = False
    promotion_observations_complete: bool = False
    funding_observed: bool = False
    slippage_observed: bool = False
    trade_attribution_complete: bool = False
    expectancy_lcb: float = 0.0
    # Additive Forward Density Gate.  These values are derived from sealed
    # historical OOS/validation trade facts; they never tune strategy rules.
    forward_closed_trade_target: int = Field(default=30, ge=1)
    estimated_days_to_forward_closed_trade_target: float = float("inf")


def evaluate_profitability_recovery(metrics: ProfitabilityRecoveryMetrics) -> PromotionResult:
    """Apply the stricter dual-lane profitability recovery promotion gate."""

    failures: list[str] = []
    if metrics.total_trades < 80:
        failures.append("portfolio_trades_below_80")
    for symbol in ("BTC/USDT", "ETH/USDT"):
        if metrics.per_symbol_trades.get(symbol, 0) < 30:
            failures.append(f"symbol_trades_below_30:{symbol}")
        if metrics.per_symbol_profit_factor.get(symbol, 0.0) < 1.0:
            failures.append(f"symbol_profit_factor_below_1:{symbol}")
    if metrics.net_expectancy <= 0:
        failures.append("net_expectancy_not_positive")
    if metrics.net_return <= 0:
        failures.append("net_return_not_positive")
    if metrics.profit_factor < 1.20:
        failures.append("profit_factor_below_1_20")
    if metrics.positive_windows < (metrics.total_windows + 1) // 2:
        failures.append("positive_windows_below_majority")
    if metrics.final_holdout_net_expectancy <= 0:
        failures.append("final_holdout_expectancy_not_positive")
    if metrics.max_drawdown > 0.20:
        failures.append("max_drawdown_exceeds_20_percent")
    if metrics.cost_stress_1_5x_net_expectancy <= 0:
        failures.append("cost_stress_1_5x_expectancy_not_positive")
    if metrics.cost_stress_1_5x_profit_factor <= 1.0:
        failures.append("cost_stress_1_5x_profit_factor_not_above_1")
    if metrics.one_minute_net_expectancy <= 0:
        failures.append("one_minute_fidelity_not_positive")
    if not metrics.freqtrade_lookahead_passed:
        failures.append("freqtrade_lookahead_analysis_failed")
    if not metrics.freqtrade_recursive_passed:
        failures.append("freqtrade_recursive_analysis_failed")
    if not metrics.vectorbt_neighborhood_passed:
        failures.append("vectorbt_neighborhood_not_stable")
    if not metrics.promotion_observations_complete:
        failures.append("promotion_observations_incomplete")
    if not metrics.funding_observed:
        failures.append("funding_observations_missing")
    if not metrics.slippage_observed:
        failures.append("slippage_observations_missing")
    if not metrics.trade_attribution_complete:
        failures.append("trade_attribution_incomplete")
    if metrics.expectancy_lcb <= 0:
        failures.append("expectancy_lcb_not_positive")
    if metrics.estimated_days_to_forward_closed_trade_target > 60:
        failures.append("STRATEGY_TOO_SPARSE_FOR_FORWARD_VALIDATION")
    return PromotionResult(eligible=not failures, failed_requirements=tuple(failures))


class FinalHoldoutGuard:
    """Prevent parameter selection from reading the frozen Final Holdout window."""

    def __init__(self, sealed_start: datetime) -> None:
        self.sealed_start = sealed_start

    def assert_development_end(self, end_at: datetime) -> None:
        if end_at > self.sealed_start:
            raise ValueError("Final Holdout is sealed and cannot be read during optimization")


class ResearchTrial(PlatformModel):
    """Immutable pre-result definition for one bounded strategy hypothesis."""

    hypothesis_id: str
    hypothesis_family: str
    exact_change: str
    economic_rationale: str
    development_period: str
    validation_period: str
    final_holdout_accessed: bool = False
    created_before_result: bool = True
    number_of_prior_trials: int = Field(ge=0)


class ResearchTrialRegistry:
    """Append-only budget gate for strategy research, not parameter optimization."""

    MAX_FAMILIES = 4
    MAX_VARIANTS_PER_FAMILY = 2
    MAX_TOTAL_VARIANTS = 8

    def __init__(self, path: Path) -> None:
        self.path = path

    def read_all(self) -> list[ResearchTrial]:
        if not self.path.exists():
            return []
        return [
            ResearchTrial.model_validate_json(line)
            for line in self.path.read_text(encoding="utf-8").splitlines()
            if line
        ]

    @property
    def trial_count(self) -> int:
        return len(self.read_all())

    def selection_bias_control(self) -> dict[str, Any]:
        return {
            "trial_count": self.trial_count,
            "max_families": self.MAX_FAMILIES,
            "max_variants_per_family": self.MAX_VARIANTS_PER_FAMILY,
            "max_total_variants": self.MAX_TOTAL_VARIANTS,
            "development_selection_only": True,
            "bootstrap_expectancy_lcb_required": True,
            "final_holdout_access": "LOCKED_UNTIL_DEVELOPMENT_AND_VALIDATION_PASS",
        }

    def register(self, trial: ResearchTrial) -> None:
        if not trial.created_before_result:
            raise ValueError("research trial must be registered before any result exists")
        if trial.final_holdout_accessed:
            raise ValueError("final holdout cannot be accessed when registering a hypothesis")
        existing = self.read_all()
        same_id = next((item for item in existing if item.hypothesis_id == trial.hypothesis_id), None)
        if same_id is not None:
            if same_id.model_dump(mode="json") != trial.model_dump(mode="json"):
                raise ValueError("research trial definition is immutable")
            return
        families = {item.hypothesis_family for item in existing}
        family_trials = sum(item.hypothesis_family == trial.hypothesis_family for item in existing)
        if trial.hypothesis_family not in families and len(families) >= self.MAX_FAMILIES:
            raise ValueError("research family budget exhausted")
        if family_trials >= self.MAX_VARIANTS_PER_FAMILY:
            raise ValueError("research variant budget exhausted for family")
        if len(existing) >= self.MAX_TOTAL_VARIANTS:
            raise ValueError("total research variant budget exhausted")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(trial.model_dump_json() + "\n")


class TrialLedger:
    """Append-only JSONL ledger that retains successful and failed parameter trials."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def record(
        self,
        *,
        trial_id: str,
        strategy_id: str,
        parameters: dict[str, Any],
        status: str,
    ) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "trial_id": trial_id,
            "strategy_id": strategy_id,
            "parameters": parameters,
            "status": status,
        }
        with self.path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")

    def read_all(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line]


def stationary_cluster_bootstrap_lcb(
    clusters: tuple[tuple[Decimal, ...], ...],
    *,
    n_resamples: int = 1_000,
    confidence: float = 0.95,
    restart_probability: float = 0.25,
    seed: int | None = 42,
) -> BootstrapResult:
    """Bootstrap contiguous trade clusters without treating every trade as IID."""

    if not clusters or any(not cluster for cluster in clusters):
        raise ValueError("clusters must contain at least one return each")
    if n_resamples < 1 or not 0 < confidence < 1 or not 0 < restart_probability <= 1:
        raise ValueError("invalid bootstrap configuration")
    returns = tuple(value for cluster in clusters for value in cluster)
    rng = random.Random(seed)
    samples: list[float] = []
    for _ in range(n_resamples):
        sampled: list[Decimal] = []
        cluster_index = rng.randrange(len(clusters))
        while len(sampled) < len(returns):
            sampled.extend(clusters[cluster_index])
            if rng.random() < restart_probability:
                cluster_index = rng.randrange(len(clusters))
            else:
                cluster_index = (cluster_index + 1) % len(clusters)
        samples.append(float(mean(sampled)))
    samples.sort()
    lower_index = max(0, int((1 - confidence) * n_resamples))
    return BootstrapResult(
        method="stationary_cluster_bootstrap",
        sample_size=len(returns),
        cluster_count=len(clusters),
        expectancy=float(mean(returns)),
        expectancy_lcb=samples[lower_index],
        confidence=confidence,
    )


def evaluate_promotion(metrics: PromotionMetrics) -> PromotionResult:
    """Apply the strategy task's joint, non-negotiable promotion requirements."""

    failures: list[str] = []
    if metrics.total_trades < 60:
        failures.append("closed_trades_below_60")
    if metrics.positive_windows < 5:
        failures.append("positive_windows_below_5_of_8")
    if metrics.net_return <= 0:
        failures.append("net_return_not_positive")
    if metrics.profit_factor < 1.35:
        failures.append("profit_factor_below_1_35")
    if metrics.net_expectancy <= 0:
        failures.append("net_expectancy_not_positive")
    if metrics.max_drawdown > 0.30:
        failures.append("max_drawdown_exceeds_30_percent")
    if metrics.expectancy_lcb <= 0:
        failures.append("expectancy_lcb_not_positive")
    if metrics.net_expectancy <= metrics.canary_net_expectancy:
        failures.append("net_expectancy_not_above_canary")
    if metrics.canary_net_return > 0 and metrics.net_return < metrics.canary_net_return * 1.25:
        failures.append("net_return_improvement_below_25_percent")
    return PromotionResult(eligible=not failures, failed_requirements=tuple(failures))
