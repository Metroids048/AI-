"""Stable, immutable contracts between strategy research and execution planning."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import Field, model_validator

from services.strategy_library.context import FrozenContract


class EntryTrigger(FrozenContract):
    entry_type: Literal["market_after_confirmation", "stop_confirmation", "limit_retest"]
    reference_price: Decimal = Field(gt=0)
    confirmation_price: Decimal | None = Field(default=None, gt=0)
    max_price_drift_bps: Decimal = Field(ge=0)


class InvalidationRule(FrozenContract):
    stop_price: Decimal = Field(gt=0)
    extreme_price: Decimal = Field(gt=0)
    reason: str


class TargetRule(FrozenContract):
    label: str
    price: Decimal = Field(gt=0)
    quantity_fraction: Decimal = Field(gt=0, le=1)


class StrategyProposal(FrozenContract):
    proposal_id: str
    strategy_id: str
    strategy_version: str
    symbol: str
    side: Literal["long", "short"]
    setup_type: str
    signal_bar_time: datetime
    expires_at: datetime
    entry_trigger: EntryTrigger
    invalidation: InvalidationRule
    targets: tuple[TargetRule, ...]
    regime_fit: float = Field(ge=0, le=1)
    setup_quality: float = Field(ge=0, le=1)
    cost_adjusted_rr: Decimal = Field(gt=0)
    confidence_components: dict[str, float]
    feature_snapshot_hash: str = Field(min_length=64, max_length=64)
    reasons: tuple[str, ...]

    @model_validator(mode="after")
    def validate_plan_geometry(self) -> StrategyProposal:
        if self.expires_at <= self.signal_bar_time:
            raise ValueError("proposal must expire after its signal bar")
        allocated = sum((target.quantity_fraction for target in self.targets), Decimal("0"))
        if allocated != Decimal("1"):
            raise ValueError("target quantity fractions must sum to 1")
        entry = self.entry_trigger.reference_price
        prices = [target.price for target in self.targets]
        if self.side == "long":
            if self.invalidation.stop_price >= entry or any(price <= entry for price in prices):
                raise ValueError("long proposal prices must be ordered around entry")
        elif self.invalidation.stop_price <= entry or any(price >= entry for price in prices):
            raise ValueError("short proposal prices must be ordered around entry")
        return self
