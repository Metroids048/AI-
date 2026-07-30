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
    win_rate: float
    average_profit_loss_ratio: float
    profit_factor: float
    net_expectancy: float
    max_drawdown: float
    expectancy_lcb: float


class PromotionResult(PlatformModel):
    eligible: bool
    failed_requirements: tuple[str, ...]


class FinalHoldoutGuard:
    """Prevent parameter selection from reading the frozen Final Holdout window."""

    def __init__(self, sealed_start: datetime) -> None:
        self.sealed_start = sealed_start

    def assert_development_end(self, end_at: datetime) -> None:
        if end_at > self.sealed_start:
            raise ValueError("Final Holdout is sealed and cannot be read during optimization")


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
    if metrics.win_rate < 0.50:
        failures.append("win_rate_below_50_percent")
    if metrics.average_profit_loss_ratio < 1.20:
        failures.append("average_profit_loss_ratio_below_1_20")
    if metrics.profit_factor < 1.50:
        failures.append("profit_factor_below_1_50")
    if metrics.net_expectancy <= 0:
        failures.append("net_expectancy_not_positive")
    if metrics.max_drawdown > 0.15:
        failures.append("max_drawdown_exceeds_15_percent")
    if metrics.expectancy_lcb <= 0:
        failures.append("expectancy_lcb_not_positive")
    return PromotionResult(eligible=not failures, failed_requirements=tuple(failures))
