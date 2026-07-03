"""Validation-layer contracts: normalized backtest metrics + gate decision.

Naming reconciliation (see docs/architecture/v2-integration-reconciliation.md):
- `BacktestReport` is the normalized *metrics payload* emitted by ANY engine
  (Freqtrade / Jesse / VectorBT). It is exactly what fills the
  `BacktestRun.metrics_summary` field of the domain lifecycle object.
- The full `BacktestRun` lifecycle object lives in the domain layer; engines
  only ever produce/consume this metrics payload at the framework boundary.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .base import PlatformModel
from .enums import BacktestEngine


class BacktestReport(PlatformModel):
    """Engine-agnostic backtest result. Every runner normalizes to this.

    Cost and validation-methodology fields (see
    docs/architecture/validation-methodology.md): every reported metric here
    must already be net of trading costs, and `deflated_sharpe` should be
    preferred over raw `sharpe` for gate decisions once `trials_count` is
    tracked for a strategy.
    """

    strategy_id: str
    engine: BacktestEngine
    sharpe: float
    profit_factor: float
    max_drawdown: float = Field(description="Fraction, e.g. 0.18 == 18%")
    win_rate: float
    expectancy: float
    oos_sharpe: float | None = Field(default=None, description="Out-of-sample Sharpe")
    total_trades: int = 0
    timerange: str | None = Field(default=None, examples=["20210101-20240101"])
    raw_ref: str | None = Field(default=None, description="Pointer to raw engine output")
    deflated_sharpe: float | None = Field(
        default=None,
        description="Sharpe corrected for multiple-testing bias; see validation-methodology.md §02",
    )
    trials_count: int | None = Field(
        default=None,
        description="Number of parameter combos/variants tried historically for this strategy/signal family",
    )
    total_cost_bps: float | None = Field(
        default=None,
        description="Combined maker/taker fees + slippage + net funding rate, in basis points",
    )
    cost_breakdown_bps: dict[str, float] | None = Field(
        default=None,
        description="fee/slippage/funding components in basis points",
    )
    stress_test_results: dict[str, float] | None = Field(
        default=None,
        description="Scenario label -> outcome metric, from validation-methodology.md §03 scenario library",
    )
    validation_windows: list[dict[str, float | str | int | bool]] = Field(
        default_factory=list,
        description="Walk-forward/OOS window summaries used to justify the gate decision",
    )
    lookahead_check: dict[str, float | str | int | bool] = Field(
        default_factory=dict,
        description="Freqtrade-inspired lookahead/recursive formula diagnostic report",
    )


# Validation gates from AGENTS.md §4 (默认门槛).
GATE_MIN_SHARPE = 1.0
GATE_MIN_PROFIT_FACTOR = 1.3
GATE_MAX_DRAWDOWN = 0.25
GATE_MIN_EXPECTANCY = 0.0


class GateDecision(PlatformModel):
    """Output of the GateKeeper hard-threshold check."""

    strategy_id: str
    passed: bool
    decision_status: Literal["accepted", "conditional", "rejected_with_reason"] = "accepted"
    reason: str | None = None
    failed_thresholds: list[str] = Field(default_factory=list)
    thresholds_applied: dict[str, float] = Field(
        default_factory=lambda: {
            "min_sharpe": GATE_MIN_SHARPE,
            "min_profit_factor": GATE_MIN_PROFIT_FACTOR,
            "max_drawdown": GATE_MAX_DRAWDOWN,
            "min_expectancy": GATE_MIN_EXPECTANCY,
        }
    )
    deflated_sharpe_applied: bool = True

    @model_validator(mode="after")
    def _validate_status(self) -> GateDecision:
        if self.decision_status == "rejected_with_reason":
            self.passed = False
            if not self.reason:
                raise ValueError("reason is required when decision_status is rejected_with_reason")
        elif self.decision_status in {"accepted", "conditional"}:
            self.passed = True
        return self
