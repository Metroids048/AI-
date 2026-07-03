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
from .market import (
    ConsoleOverview,
    ExchangeCapability,
    MarketExtras,
    MarketSnapshot,
    OHLCVBar,
    OhlcvSeriesResponse,
)
from .research_source import (
    ResearchSourceIdeaExtractionRequest,
    ResearchSourceImportRequest,
    ResearchSourceImportResult,
    StrategySourceManifest,
)
from .risk import RiskEvent, RiskEventResolutionUpdate, RiskProfile
from .signal import (
    CandidateSignalSeries,
    DecisionVetoResult,
    MetaLabel,
    MetaLabelRequest,
    MetaLabelSample,
    SignalEnsemble,
    SignalEnsembleRequest,
    SignalVote,
    TradeSignal,
)
from .strategy import (
    StrategyBase,
    StrategyContract,
    StrategyCreate,
    StrategyDraft,
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
    NotificationOutboxItem,
    OptimizationRun,
    OptimizationSubmissionRequest,
    OrderExecution,
    PaperRun,
    PaperRunRequest,
    PaperRunStatusUpdate,
    PaperRunStepRequest,
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
    "MarketSnapshot",
    "OhlcvSeriesResponse",
    "ConsoleOverview",
    "ExchangeCapability",
    # backtest
    "BacktestReport",
    "GateDecision",
    "GATE_MIN_SHARPE",
    "GATE_MIN_PROFIT_FACTOR",
    "GATE_MAX_DRAWDOWN",
    "GATE_MIN_EXPECTANCY",
    # risk
    "RiskEvent",
    "RiskEventResolutionUpdate",
    "RiskProfile",
    "StrategySourceManifest",
    "ResearchSourceImportRequest",
    "ResearchSourceIdeaExtractionRequest",
    "ResearchSourceImportResult",
    # macro
    "MacroEvent",
    # signal
    "TradeSignal",
    "CandidateSignalSeries",
    "SignalVote",
    "SignalEnsemble",
    "SignalEnsembleRequest",
    "DecisionVetoResult",
    "MetaLabel",
    "MetaLabelSample",
    "MetaLabelRequest",
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
    "PaperRunStatusUpdate",
    "PaperRunStepRequest",
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
    "NotificationOutboxItem",
]
