"""Validation-evidence contracts for promotion gates and benchmark controls."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from .base import PlatformModel


class ValidationBenchmarkResult(PlatformModel):
    benchmark_name: str
    benchmark_type: str = Field(
        description="market_baseline / strict_random_control / vibe_benchmark / custom"
    )
    baseline_return: float
    strategy_return: float
    excess_return: float
    passed: bool
    notes: str | None = None


class PodRiskReport(PlatformModel):
    pod_id: str
    passed: bool
    violations: list[str] = Field(default_factory=list)
    max_expected_loss: float
    max_expected_leverage: float
    data_freshness_ok: bool
    generated_at: datetime | None = None


class HypothesisRecord(PlatformModel):
    hypothesis_id: str | None = None
    strategy_id: str | None = None
    idea_id: str | None = None
    title: str
    statement: str
    rationale: str | None = None
    benchmark_plan: dict[str, Any] = Field(default_factory=dict)
    acceptance_criteria: dict[str, Any] = Field(default_factory=dict)
    status: str = "draft"
    created_at: datetime | None = None
    updated_at: datetime | None = None

