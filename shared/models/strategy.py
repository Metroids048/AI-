"""Strategy Layer contract — the 18-field strategy asset (AGENTS.md §2).

`StrategyContract` is the canonical Pydantic shape. The SQLAlchemy ORM in
`services/strategy_library/models.py` maps to/from this contract (ORM is NOT a
cross-layer contract — this is). Field names follow domain-and-interfaces-
design.md §3.3.

The 18 AGENTS.md fields are: source, core_thesis, market, timeframe,
market_regime, entry_rules, exit_rules, stoploss_rules, takeprofit_rules,
position_rules, risk_level, backtest_status, paper_status, live_status,
failure_reasons, iteration_history (+ strategy_id). `strategy_key`,
`symbol_scope` and `strategy_status` come from the domain doc superset.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from .base import PlatformModel
from .enums import Market, RiskLevel, RunStatus, StrategyStatus, Timeframe


class StrategyRules(PlatformModel):
    """Rule blocks are kept as structured payloads, not free text."""

    entry_rules: dict[str, Any] = Field(default_factory=dict)
    exit_rules: dict[str, Any] = Field(default_factory=dict)
    stoploss_rules: dict[str, Any] = Field(default_factory=dict)
    takeprofit_rules: dict[str, Any] = Field(default_factory=dict)
    position_rules: dict[str, Any] = Field(default_factory=dict)


class StrategyIdea(PlatformModel):
    """Research intake item before it becomes a rules-based draft."""

    idea_id: str | None = None
    title: str
    source: str = Field(examples=["manual_note", "worldquant", "github"])
    market: Market = Market.CRYPTO_PERP
    symbol_scope: list[str] = Field(default_factory=lambda: ["BTC/USDT"])
    hypothesis_summary: str
    source_ref: str | None = None
    rationale: str | None = None
    intake_bucket: str = Field(
        default="rule_candidate",
        description="rule_candidate / metric_to_validate / subjective_to_drop",
    )
    created_at: datetime | None = None


class StrategyDraft(PlatformModel):
    """Rule-oriented draft derived from a StrategyIdea."""

    draft_id: str | None = None
    idea_id: str | None = None
    title: str
    source: str
    core_thesis: str
    market: Market = Market.CRYPTO_PERP
    symbol_scope: list[str] = Field(default_factory=lambda: ["BTC/USDT"])
    timeframe: Timeframe = Timeframe.H1
    market_regime: str | None = None
    risk_level: RiskLevel = RiskLevel.MEDIUM
    rules: StrategyRules = Field(default_factory=StrategyRules)
    draft_status: str = "drafting"
    review_notes: list[str] = Field(default_factory=list)
    created_at: datetime | None = None
    updated_at: datetime | None = None


class StrategyVersion(PlatformModel):
    """Version metadata for a materialized strategy asset."""

    version_id: str | None = None
    strategy_id: str
    version_label: str = Field(examples=["v1", "v1.1"])
    change_summary: str
    code_artifact_ref: str | None = None
    created_at: datetime | None = None


class StrategyBase(PlatformModel):
    """Shared writable fields for create/update/read."""

    strategy_key: str = Field(examples=["BTC_FakeBreakdown_v2"])
    source: str = Field(examples=["worldquant", "github", "a_share_self", "manual"])
    core_thesis: str
    market: Market = Market.CRYPTO_PERP
    symbol_scope: list[str] = Field(default_factory=lambda: ["BTC/USDT"])
    timeframe: Timeframe = Timeframe.H1
    market_regime: str | None = None
    risk_level: RiskLevel = RiskLevel.MEDIUM
    rules: StrategyRules = Field(default_factory=StrategyRules)


class StrategyCreate(StrategyBase):
    """POST /strategies body."""


class StrategyUpdate(PlatformModel):
    """PUT /strategies/{id} body — all optional (partial update)."""

    core_thesis: str | None = None
    market_regime: str | None = None
    risk_level: RiskLevel | None = None
    rules: StrategyRules | None = None
    strategy_status: StrategyStatus | None = None


class StrategyContract(StrategyBase):
    """Full canonical strategy contract (read shape + lifecycle bookkeeping)."""

    strategy_id: str
    strategy_status: StrategyStatus = StrategyStatus.DRAFTING
    backtest_status: RunStatus = RunStatus.NOT_STARTED
    paper_status: RunStatus = RunStatus.NOT_STARTED
    live_status: RunStatus = RunStatus.NOT_STARTED
    failure_reasons: list[str] = Field(default_factory=list)
    iteration_history: list[dict[str, Any]] = Field(default_factory=list)
    created_at: datetime | None = None
    updated_at: datetime | None = None


# Read shape returned by the API (== full contract for now).
StrategyRead = StrategyContract
