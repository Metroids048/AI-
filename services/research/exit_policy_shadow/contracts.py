"""Immutable contracts for P2-A exit-policy shadow evaluation.

Read-only research module. Nothing here may create an execution intent, submit an
order, mutate a managed position, or submit protection. See ADR-004 and
`.p2a-execution-manifest.yaml`.

Vocabulary
----------
A *real entry* is an already-filled `testnet_sampling_v2` position: its symbol,
side, average fill price, fill timestamp, filled quantity and actual entry fee are
facts and are never recomputed. P2-A holds these fixed and varies only the exit.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Side = Literal["long", "short"]


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ExitPolicyId(StrEnum):
    """The five policies P2-A compares. A is the production baseline."""

    CURRENT_CONTROL = "A_CURRENT_CONTROL"
    ATR_ADAPTIVE = "B_ATR_ADAPTIVE"
    STRUCTURE_INVALIDATION = "C_STRUCTURE_INVALIDATION"
    SCALE_OUT_RUNNER = "D_SCALE_OUT_RUNNER"
    REGIME_AWARE = "E_REGIME_AWARE"


class Regime(StrEnum):
    """Entry-time regime label. Never back-filled from post-exit data."""

    TREND = "TREND"
    RANGE = "RANGE"
    EXPANSION = "EXPANSION"
    UNKNOWN = "UNKNOWN"


class ExitReason(StrEnum):
    STOP = "STOP"
    TARGET = "TARGET"
    PARTIAL_TARGET = "PARTIAL_TARGET"
    RUNNER_TRAIL = "RUNNER_TRAIL"
    DATA_EXHAUSTED = "DATA_EXHAUSTED"
    STILL_OPEN = "STILL_OPEN"


class IntrabarResolution(StrEnum):
    """How a bar that touched both stop and target was resolved.

    UNAMBIGUOUS   - only one level was touched in the bar.
    STOP_FIRST    - both touched; conservative primary result.
    TARGET_FIRST  - both touched; optimistic sensitivity result only.
    """

    UNAMBIGUOUS = "UNAMBIGUOUS"
    STOP_FIRST = "STOP_FIRST"
    TARGET_FIRST = "TARGET_FIRST"


class Bar(FrozenModel):
    """One OHLCV bar. `time` is the bar open time."""

    time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal

    @model_validator(mode="after")
    def validate_range(self) -> Bar:
        if self.high < self.low:
            raise ValueError(f"bar high {self.high} below low {self.low}")
        return self


class RealEntry(FrozenModel):
    """An immutable real filled entry. P2-A never modifies these fields.

    `entry_fee_usdt` is the ACTUAL fee charged by the exchange for this entry.
    Exit fees are estimated per policy and tracked separately, never merged in.
    """

    position_id: str
    symbol: str
    side: Side
    average_fill_price: Decimal = Field(gt=0)
    fill_timestamp: datetime
    filled_quantity: Decimal = Field(gt=0)
    entry_fee_usdt: Decimal = Field(ge=0)
    candidate_key: str
    decision_bar_timestamp: datetime
    exchange_order_id: str

    @model_validator(mode="after")
    def validate_provenance(self) -> RealEntry:
        if self.candidate_key != "testnet_sampling_v2":
            raise ValueError(f"P2-A samples must come from testnet_sampling_v2, got {self.candidate_key!r}")
        if self.fill_timestamp < self.decision_bar_timestamp:
            raise ValueError("fill cannot precede its decision bar")
        return self

    @property
    def notional_usdt(self) -> Decimal:
        return self.average_fill_price * self.filled_quantity


class ExitLeg(FrozenModel):
    """One realised leg of an exit. Scale-out policies produce several."""

    label: str
    price: Decimal = Field(gt=0)
    quantity: Decimal = Field(gt=0)
    quantity_fraction: Decimal = Field(gt=0, le=1)
    filled_at: datetime
    reason: ExitReason
    intrabar: IntrabarResolution = IntrabarResolution.UNAMBIGUOUS


class ExcursionMetrics(FrozenModel):
    """Direction-aware excursions measured from entry until the policy's exit.

    Excursions are truncated at the exit bar: post-exit price action is not
    counted, because it was never available to that policy.
    """

    mfe_pct: Decimal
    mae_pct: Decimal
    mfe_r: Decimal | None
    mae_r: Decimal | None
    mfe_pnl_usdt: Decimal
    mae_pnl_usdt: Decimal

    @model_validator(mode="after")
    def validate_signs(self) -> ExcursionMetrics:
        if self.mfe_pct < 0:
            raise ValueError(f"mfe_pct must be >= 0, got {self.mfe_pct}")
        if self.mae_pct > 0:
            raise ValueError(f"mae_pct must be <= 0, got {self.mae_pct}")
        return self


class ShadowOutcome(FrozenModel):
    """Result of replaying one real entry under one exit policy."""

    position_id: str
    symbol: str
    side: Side
    policy: ExitPolicyId
    regime: Regime
    entry_price: Decimal
    entry_quantity: Decimal
    initial_stop_price: Decimal
    initial_target_price: Decimal | None
    legs: tuple[ExitLeg, ...]
    remaining_quantity: Decimal
    final_reason: ExitReason
    holding_time_minutes: Decimal
    excursions: ExcursionMetrics
    gross_pnl_usdt: Decimal
    entry_fee_usdt: Decimal
    estimated_exit_fee_usdt: Decimal
    estimated_slippage_usdt: Decimal
    net_pnl_usdt: Decimal
    ambiguous_intrabar: bool
    sensitivity_net_pnl_usdt: Decimal | None = None
    regime_selection_reason: str | None = None

    @property
    def total_cost_usdt(self) -> Decimal:
        return self.entry_fee_usdt + self.estimated_exit_fee_usdt + self.estimated_slippage_usdt

    @property
    def r_multiple(self) -> Decimal | None:
        """Net PnL expressed in initial-risk units."""
        risk_per_unit = abs(self.entry_price - self.initial_stop_price)
        if risk_per_unit <= 0:
            return None
        risk_usdt = risk_per_unit * self.entry_quantity
        if risk_usdt <= 0:
            return None
        return self.net_pnl_usdt / risk_usdt

    @property
    def profit_capture_ratio(self) -> Decimal | None:
        """net / MFE, defined only when a *capturable* profit actually existed.

        Requiring merely ``mfe_pnl_usdt > 0`` is not enough. A favourable excursion
        of a fraction of a basis point is arithmetically positive but produces a
        near-zero denominator: one real trade here had ``MFE = 0.0002%`` and a small
        loss, which yields ``-107242%``. That is exactly the "strange ratio from a
        zero/negative denominator" the P2-A contract forbids.

        The materiality floor is the trade's own round-trip cost: if the best
        favourable excursion never exceeded what exiting at that peak would cost,
        then no profit was ever capturable and the ratio is undefined rather than a
        large negative number. This uses only data P2-A already tracks, so it
        introduces no tuned constant.

        Ratios may still be negative when a genuinely capturable profit was given
        back — that is a real, interpretable signal and is retained.
        """
        mfe = self.excursions.mfe_pnl_usdt
        if mfe <= 0:
            return None
        if mfe <= self.total_cost_usdt:
            return None
        return self.net_pnl_usdt / mfe

    @property
    def capture_ratio_undefined_reason(self) -> str | None:
        """Why the capture ratio is undefined, for honest reporting."""
        mfe = self.excursions.mfe_pnl_usdt
        if mfe <= 0:
            return "no_favourable_excursion"
        if mfe <= self.total_cost_usdt:
            return "excursion_below_round_trip_cost"
        return None

    @model_validator(mode="after")
    def validate_quantities(self) -> ShadowOutcome:
        allocated = sum((leg.quantity for leg in self.legs), Decimal("0"))
        if allocated + self.remaining_quantity != self.entry_quantity:
            raise ValueError(
                f"leg quantities {allocated} + remaining {self.remaining_quantity} "
                f"!= entry quantity {self.entry_quantity}"
            )
        if self.side == "long":
            if self.initial_stop_price >= self.entry_price:
                raise ValueError("long stop must sit below entry")
            if self.initial_target_price is not None and self.initial_target_price <= self.entry_price:
                raise ValueError("long target must sit above entry")
        else:
            if self.initial_stop_price <= self.entry_price:
                raise ValueError("short stop must sit above entry")
            if self.initial_target_price is not None and self.initial_target_price >= self.entry_price:
                raise ValueError("short target must sit below entry")
        return self


class PolicyAggregate(FrozenModel):
    """Aggregated metrics for one policy over one slice of the sample."""

    policy: ExitPolicyId
    slice_key: str
    trade_count: int
    win_rate: Decimal | None
    gross_expectancy_usdt: Decimal | None
    net_expectancy_usdt: Decimal | None
    profit_factor: Decimal | None
    avg_r: Decimal | None
    median_r: Decimal | None
    max_drawdown_usdt: Decimal
    avg_mfe_r: Decimal | None
    avg_mae_r: Decimal | None
    median_profit_capture_ratio: Decimal | None
    capture_ratio_sample_count: int
    fee_drag_usdt: Decimal
    avg_holding_time_minutes: Decimal | None
    median_holding_time_minutes: Decimal | None
    ambiguous_intrabar_count: int


class Verdict(StrEnum):
    SUPPORTED = "SUPPORTED"
    NOT_SUPPORTED = "NOT_SUPPORTED"
    INSUFFICIENT_SAMPLE = "INSUFFICIENT_SAMPLE"


MINIMUM_TRADES_FOR_VERDICT = 30
"""Below this, every business verdict is INSUFFICIENT_SAMPLE.

Consistent with the repository's existing >=30-sample edge-statistics gate. This
threshold is a reporting honesty rule, not a promotion threshold.
"""
