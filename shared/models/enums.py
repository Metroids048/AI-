"""Enumerations shared across the platform contracts.

Enums are intentionally extensible: AGENTS.md「Initial Market Scope」requires the
data model to support future expansion to ETH / SOL / A股 / 美股 / 黄金 / 纳指
from day one, so do not hardcode BTC-only assumptions downstream.
"""

from __future__ import annotations

from enum import StrEnum


class Market(StrEnum):
    """Tradable market families. Phase-1 main market is crypto perpetual."""

    CRYPTO_PERP = "crypto_perp"
    CRYPTO_SPOT = "crypto_spot"
    A_SHARE = "a_share"
    US_STOCK = "us_stock"
    GOLD = "gold"
    NASDAQ = "nasdaq"


class Exchange(StrEnum):
    BINANCE = "binance"
    OKX = "okx"
    BYBIT = "bybit"


class Timeframe(StrEnum):
    M1 = "1m"
    M5 = "5m"
    M15 = "15m"
    H1 = "1h"
    H4 = "4h"
    D1 = "1d"


class RiskSeverity(StrEnum):
    """RiskEvent severity. PDF field `level` maps onto this `severity`."""

    LOW = "low"
    MID = "mid"
    HIGH = "high"
    CRITICAL = "critical"


class RiskEventType(StrEnum):
    """Event types from domain-and-interfaces-design.md §3.14."""

    MACRO_EVENT = "macro_event"
    NEWS_RISK = "news_risk"
    SOCIAL_EVENT = "social_event"
    MARKET_STRUCTURE_RISK = "market_structure_risk"
    EXCHANGE_INCIDENT = "exchange_incident"
    API_FAILURE = "api_failure"
    DATA_GAP = "data_gap"
    DATA_STALE = "data_stale"
    RISK_LIMIT_BREACH = "risk_limit_breach"
    EXECUTION_ANOMALY = "execution_anomaly"


class RiskResolutionStatus(StrEnum):
    """RiskEvent processing states (domain doc §5.3)."""

    DETECTED = "detected"
    ACKNOWLEDGED = "acknowledged"
    MITIGATED = "mitigated"
    RESOLVED = "resolved"
    ARCHIVED = "archived"


class BacktestEngine(StrEnum):
    """Which validation engine produced a BacktestReport."""

    FREQTRADE = "freqtrade"
    JESSE = "jesse"
    VECTORBT = "vectorbt"
    LEAN = "lean"


class StrategyStatus(StrEnum):
    """Strategy main state machine (domain doc §5.1)."""

    DRAFTING = "drafting"
    READY_FOR_CODEGEN = "ready_for_codegen"
    CODE_GENERATED = "code_generated"
    IN_BACKTEST = "in_backtest"
    BACKTEST_REJECTED = "backtest_rejected"
    PAPER_CANDIDATE = "paper_candidate"
    IN_PAPER_RUN = "in_paper_run"
    PAPER_REJECTED = "paper_rejected"
    LIVE_CANDIDATE = "live_candidate"
    IN_LIVE_RUN = "in_live_run"
    ACTIVE = "active"
    PAUSED = "paused"
    RETIRED = "retired"


class RunStatus(StrEnum):
    """Per-stage progress status for backtest/paper/live (the 18-field
    `backtest_status` / `paper_status` / `live_status` columns)."""

    NOT_STARTED = "not_started"
    QUEUED = "queued"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class TradeSide(StrEnum):
    LONG = "long"
    SHORT = "short"


class EnsembleStatus(StrEnum):
    """SignalEnsemble lifecycle (domain doc §3.5a)."""

    FORMED = "formed"
    PASSED_TO_META_LABEL = "passed_to_meta_label"
    DISCARDED_LOW_CONFIDENCE = "discarded_low_confidence"


class BetDecision(StrEnum):
    """MetaLabel two-stage bet-sizing outcome (domain doc §3.5b)."""

    PENDING = "pending"
    BET_TAKEN = "bet_taken"
    BET_SKIPPED = "bet_skipped"


class TripleBarrierOutcome(StrEnum):
    """Which barrier a price path hit first when labeling training samples."""

    TAKE_PROFIT = "take_profit"
    STOP_LOSS = "stop_loss"
    TIMEOUT = "timeout"
