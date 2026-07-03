"""Trade signal + signal-fusion contracts.

TradeSignal: PDF §3.5 — Telegram Agent parse target.

Example:
  "BTC Long entry 65000 SL 63000 TP1 68000 TP2 72000 leverage 5x"
  -> TradeSignal(symbol="BTC/USDT", side=long, entry=65000, stoploss=63000,
                 takeprofits=[68000, 72000], leverage=5, source="channel_xyz")

SignalEnsemble / MetaLabel: Strategy Library 信号融合子模块契约
(domain-and-interfaces-design.md §3.5a/§3.5b)。多个策略/alpha 的候选信号在此
融合为单一交易候选，再经二级仓位判定（meta-labeling）决定是否下注及下注大小。
WorldQuant alpha 在 `raw_votes` 中只是权重较低的一票，不作为独立策略。
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import Field

from .base import PlatformModel
from .enums import BetDecision, EnsembleStatus, TradeSide, TripleBarrierOutcome


class TradeSignal(PlatformModel):
    symbol: str
    side: TradeSide
    entry: Decimal | None = None
    stoploss: Decimal | None = None
    takeprofits: list[Decimal] = Field(default_factory=list)
    leverage: Decimal | None = None
    source: str = Field(examples=["channel_xyz"])
    received_at: datetime | None = None
    signal_time: datetime | None = None
    reason: str | None = None
    confidence: float | None = None


class SignalVote(PlatformModel):
    """A single strategy/alpha's contribution to a SignalEnsemble."""

    strategy_id: str
    direction: TradeSide
    weight: float = Field(description="Initial weight; re-tuned by validation history, not hand-tuned")
    confidence: float | None = None


class SignalEnsemble(PlatformModel):
    """Fused trading candidate from multiple low-correlation signal sources."""

    ensemble_id: str
    strategy_refs: list[str] = Field(default_factory=list)
    fusion_method: str = Field(default="weighted_vote", examples=["weighted_vote"])
    correlation_matrix_ref: str | None = Field(
        default=None, description="Pointer to the correlation filter run that selected strategy_refs"
    )
    raw_votes: list[SignalVote] = Field(default_factory=list)
    fused_direction: TradeSide | None = None
    fused_confidence: float | None = None
    ensemble_status: EnsembleStatus = EnsembleStatus.FORMED
    created_at: datetime | None = None


class DecisionVetoResult(PlatformModel):
    """Structured output of the Decision Veto Agent."""

    veto: bool = False
    veto_reason: str | None = None
    checked_at: datetime | None = None
    agent_task_ref: str | None = None


class MetaLabel(PlatformModel):
    """Second-stage bet-sizing decision for a SignalEnsemble (meta-labeling)."""

    meta_label_id: str
    ensemble_id: str
    triple_barrier_result: TripleBarrierOutcome | None = None
    bet_decision: BetDecision = BetDecision.PENDING
    position_size_fraction: float | None = Field(
        default=None, description="Fraction of max allowed position size, 0..1"
    )
    model_ref: str | None = Field(default=None, description="Pointer to the trained meta-label model version")
    training_window_ref: str | None = None


class CandidateSignalSeries(PlatformModel):
    strategy_id: str
    direction: TradeSide
    weight: float = 1.0
    confidence: float | None = None
    validation_score: float | None = None
    series: list[float] = Field(default_factory=list)


class SignalEnsembleRequest(PlatformModel):
    signals: list[CandidateSignalSeries]
    correlation_threshold: float = 0.75
    min_history: int = 200
    fusion_method: str = "weighted_vote"


class MetaLabelSample(PlatformModel):
    sample_time: datetime
    net_return: float


class MetaLabelRequest(PlatformModel):
    ensemble_id: str
    training_samples: list[MetaLabelSample] = Field(default_factory=list)
    signal_time: datetime | None = None
    take_profit: float = 0.02
    stop_loss: float = -0.01
    time_limit_bars: int = 24
    min_win_rate: float = 0.55
    min_average_return: float = 0.0
    audit_context: dict[str, Any] = Field(default_factory=dict)
