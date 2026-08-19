"""Deterministic, engine-neutral contracts for external research runs."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import Field, model_validator

from shared.models import PlatformModel


def stable_hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class ResearchExperimentSpec(PlatformModel):
    strategy_id: str
    strategy_version: str | None = None
    strategy_hash: str
    dataset_id: str
    dataset_hash: str
    symbols: list[str] = Field(default_factory=list)
    timeframes: list[str] = Field(default_factory=list)
    window: dict[str, Any] = Field(default_factory=dict)
    split_plan: dict[str, Any] = Field(default_factory=dict)
    cost_model: dict[str, Any] = Field(default_factory=dict)
    parameter_space: dict[str, Any] = Field(default_factory=dict)
    engine_options: dict[str, Any] = Field(default_factory=dict)
    random_seed: int = 0
    source_database: str | None = None

    @property
    def split_plan_hash(self) -> str:
        return stable_hash(self.split_plan)

    @property
    def cost_model_hash(self) -> str:
        return stable_hash(self.cost_model)

    @property
    def input_spec_hash(self) -> str:
        return stable_hash(self.model_dump(mode="json"))


class ResearchExperimentResult(PlatformModel):
    run_id: str
    engine: str
    engine_sha: str | None = None
    input_spec_hash: str
    dataset_hash: str
    cost_model_hash: str
    strategy_hash: str
    status: Literal["completed", "failed", "unavailable"]
    metrics_by_symbol: dict[str, dict[str, Any]] = Field(default_factory=dict)
    metrics_by_window: dict[str, dict[str, Any]] = Field(default_factory=dict)
    trade_count: int = 0
    win_rate: float | None = None
    payoff_ratio: float | None = None
    profit_factor: float | None = None
    expectancy_net_r: float | None = None
    expectancy_lcb: float | None = None
    max_drawdown: float | None = None
    cost_stress: dict[str, Any] = Field(default_factory=dict)
    parameter_plateau: dict[str, Any] = Field(default_factory=dict)
    lookahead_status: str = "NOT_RUN"
    recursive_status: str = "NOT_RUN"
    artifacts: list[str] = Field(default_factory=list)
    failure_reason: str | None = None
    provenance: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _hashes_are_present(self) -> ResearchExperimentResult:
        for field in ("input_spec_hash", "dataset_hash", "cost_model_hash", "strategy_hash"):
            if not getattr(self, field):
                raise ValueError(f"{field} is required for cross-engine comparison")
        return self
