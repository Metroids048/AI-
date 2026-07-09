"""Market Intelligence contracts.

These objects keep message/on-chain/derivatives context as a bounded Strategy
Layer vote. They do not grant AI or providers any direct execution authority.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from .base import PlatformModel
from .enums import RiskLevel, RiskSeverity, TradeSide

MAX_MARKET_INTELLIGENCE_VOTE_WEIGHT = 0.30


class MarketEvent(PlatformModel):
    event_id: str
    source: str
    event_type: Literal["news", "macro", "derivatives", "onchain", "defi", "market", "risk"]
    symbol: str | None = None
    occurred_at: datetime | None = None
    title: str
    summary: str | None = None
    importance: float = Field(default=0.0, ge=0.0, le=1.0)
    severity: RiskSeverity = RiskSeverity.LOW
    sentiment: Literal["bullish", "bearish", "neutral"] = "neutral"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence_ref: str | None = None
    raw_payload: dict[str, Any] = Field(default_factory=dict)


class MarketIntelligenceFeatureSnapshot(PlatformModel):
    symbol: str
    generated_at: datetime
    data_status: Literal["ok", "partial", "empty", "cooldown"] = "empty"
    funding_rate: float | None = None
    open_interest: float | None = None
    long_ratio: float | None = None
    short_ratio: float | None = None
    liquidation_usd: float | None = None
    exchange_inflow_score: float | None = None
    exchange_outflow_score: float | None = None
    stablecoin_reserve_score: float | None = None
    defi_growth_score: float | None = None
    news_risk_score: float = Field(default=0.0, ge=0.0, le=1.0)
    macro_risk_score: float = Field(default=0.0, ge=0.0, le=1.0)
    active_event_cooldown: bool = False
    cooldown_reason: str | None = None
    provider_status: dict[str, Any] = Field(default_factory=dict)
    evidence_refs: list[str] = Field(default_factory=list)
    component_scores: dict[str, float] = Field(default_factory=dict)


class MarketIntelligenceSignal(PlatformModel):
    symbol: str
    generated_at: datetime
    long_probability: float = Field(ge=0.0, le=1.0)
    short_probability: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    direction: TradeSide | None = None
    risk_level: RiskLevel = RiskLevel.MEDIUM
    vote_weight: float = Field(default=MAX_MARKET_INTELLIGENCE_VOTE_WEIGHT, ge=0.0)
    component_scores: dict[str, float] = Field(default_factory=dict)
    evidence_refs: list[str] = Field(default_factory=list)
    rationale: list[str] = Field(default_factory=list)
    provider_status: dict[str, Any] = Field(default_factory=dict)
    active_event_cooldown: bool = False
    should_participate: bool = False

    @field_validator("vote_weight")
    @classmethod
    def _cap_vote_weight(cls, value: float) -> float:
        if value > MAX_MARKET_INTELLIGENCE_VOTE_WEIGHT:
            raise ValueError("market intelligence vote_weight must be <= 0.30")
        return value

    @model_validator(mode="after")
    def _probabilities_are_balanced(self) -> MarketIntelligenceSignal:
        total = self.long_probability + self.short_probability
        if total > 1.01:
            raise ValueError("long_probability + short_probability must be <= 1.01")
        return self


class MarketIntelligenceProviderStatus(PlatformModel):
    provider: str
    enabled: bool
    configured: bool
    status: Literal["ok", "disabled", "missing_credentials", "error"] = "disabled"
    last_error: str | None = None
    detail: dict[str, Any] = Field(default_factory=dict)
