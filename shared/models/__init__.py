"""Unified platform data contracts (single source of truth for all layers).

Import from here, never redefine these shapes inside a service:

    from shared.models import OHLCVBar, BacktestReport, RiskEvent
"""

from __future__ import annotations

from .alpha import AlphaOperator, AlphaPlan
from .api import ApiError, CollectionResponse, TaskSubmission
from .backtest import (
    GATE_MAX_DRAWDOWN,
    GATE_MIN_EXPECTANCY,
    GATE_MIN_PROFIT_FACTOR,
    GATE_MIN_SHARPE,
    BacktestReport,
    GateDecision,
)
from .base import PlatformModel
from .enums import (
    BacktestEngine,
    BetDecision,
    EnsembleStatus,
    Exchange,
    Market,
    RiskEventType,
    RiskLevel,
    RiskResolutionStatus,
    RiskSeverity,
    RunStatus,
    StrategyStatus,
    Timeframe,
    TradeSide,
    TripleBarrierOutcome,
)
from .macro import MacroEvent
from .market import MarketExtras, OHLCVBar
from .risk import RiskEvent, RiskProfile
from .signal import DecisionVetoResult, MetaLabel, SignalEnsemble, SignalVote, TradeSignal
from .strategy import (
    StrategyDraft,
    StrategyBase,
    StrategyContract,
    StrategyCreate,
    StrategyIdea,
    StrategyRead,
    StrategyRules,
    StrategyUpdate,
    StrategyVersion,
)
from .workflow import (
    AgentTask,
    AgentTaskRequest,
    BacktestRun,
    BacktestSubmissionRequest,
    CarryBacktestRequest,
    ExecutionOrderRequest,
    ExecutionSignal,
    FailureRecord,
    IngestionJob,
    IngestionJobRequest,
    LiveRun,
    LiveRunRequest,
    OptimizationRun,
    OptimizationSubmissionRequest,
    OrderExecution,
    PaperRun,
    PaperRunRequest,
    PositionSnapshot,
    ReviewReport,
)

__all__ = [
    # base
    "PlatformModel",
    # enums
    "BacktestEngine",
    "Exchange",
    "Market",
    "RiskEventType",
    "RiskLevel",
    "RiskResolutionStatus",
    "RiskSeverity",
    "RunStatus",
    "StrategyStatus",
    "Timeframe",
    "TradeSide",
    "EnsembleStatus",
    "BetDecision",
    "TripleBarrierOutcome",
    # market
    "OHLCVBar",
    "MarketExtras",
    # backtest
    "BacktestReport",
    "GateDecision",
    "GATE_MIN_SHARPE",
    "GATE_MIN_PROFIT_FACTOR",
    "GATE_MAX_DRAWDOWN",
    "GATE_MIN_EXPECTANCY",
    # risk
    "RiskEvent",
    "RiskProfile",
    # macro
    "MacroEvent",
    # signal
    "TradeSignal",
    "SignalVote",
    "SignalEnsemble",
    "DecisionVetoResult",
    "MetaLabel",
    # alpha
    "AlphaOperator",
    "AlphaPlan",
    # api
    "CollectionResponse",
    "TaskSubmission",
    "ApiError",
    # strategy
    "StrategyIdea",
    "StrategyDraft",
    "StrategyVersion",
    "StrategyBase",
    "StrategyContract",
    "StrategyCreate",
    "StrategyUpdate",
    "StrategyRead",
    "StrategyRules",
    # workflow
    "BacktestRun",
    "BacktestSubmissionRequest",
    "CarryBacktestRequest",
    "OptimizationRun",
    "OptimizationSubmissionRequest",
    "PaperRun",
    "PaperRunRequest",
    "LiveRun",
    "LiveRunRequest",
    "ExecutionSignal",
    "ExecutionOrderRequest",
    "OrderExecution",
    "PositionSnapshot",
    "ReviewReport",
    "FailureRecord",
    "IngestionJob",
    "IngestionJobRequest",
    "AgentTask",
    "AgentTaskRequest",
]
