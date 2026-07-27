"""Typed contracts for exchange-first execution truth."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import Field, field_validator

from .base import PlatformModel


class ExecutionMode(StrEnum):
    LOCAL_PAPER = "local_paper"
    BINANCE_TESTNET = "binance_testnet"


class ExchangeOrderState(StrEnum):
    INTENT_CREATED = "INTENT_CREATED"
    PRETRADE_APPROVED = "PRETRADE_APPROVED"
    EXCHANGE_SUBMITTING = "EXCHANGE_SUBMITTING"
    EXCHANGE_ACKNOWLEDGED = "EXCHANGE_ACKNOWLEDGED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    POSITION_PROJECTED = "POSITION_PROJECTED"
    PROTECTION_SUBMITTING = "PROTECTION_SUBMITTING"
    PROTECTED = "PROTECTED"
    PRETRADE_REJECTED = "PRETRADE_REJECTED"
    EXCHANGE_REJECTED = "EXCHANGE_REJECTED"
    EXCHANGE_UNKNOWN = "EXCHANGE_UNKNOWN"
    PROTECTION_FAILED = "PROTECTION_FAILED"
    EMERGENCY_CLOSE_PENDING = "EMERGENCY_CLOSE_PENDING"
    DUST_REMAINS = "DUST_REMAINS"
    CLOSED = "CLOSED"


class Commission(PlatformModel):
    asset: str
    amount: Decimal = Field(ge=0)


class SimulatedFill(PlatformModel):
    simulated_fill_id: str
    symbol: str
    side: Literal["buy", "sell"]
    filled_quantity: Decimal = Field(gt=0)
    average_fill_price: Decimal = Field(gt=0)
    event_time: datetime


class ExchangeFillReceipt(PlatformModel):
    receipt_id: str
    exchange_account: str
    exchange_order_id: str
    client_order_id: str = Field(min_length=1, max_length=36)
    trade_ids: list[str] = Field(min_length=1)
    symbol: str
    side: Literal["buy", "sell"]
    reduce_only: bool
    filled_quantity: Decimal = Field(gt=0)
    average_fill_price: Decimal = Field(gt=0)
    commissions: list[Commission] = Field(default_factory=list)
    event_time: datetime
    projected_quantity: Decimal = Field(default=Decimal("0"), ge=0)

    @field_validator("trade_ids")
    @classmethod
    def unique_non_empty_trade_ids(cls, value: list[str]) -> list[str]:
        normalized = [item.strip() for item in value]
        if any(not item for item in normalized):
            raise ValueError("trade_ids must not contain empty identifiers")
        if len(set(normalized)) != len(normalized):
            raise ValueError("trade_ids must be unique")
        return normalized


class ExchangeOrderRecord(PlatformModel):
    exchange_order_record_id: str | None = None
    local_order_execution_id: str
    exchange_account: str
    execution_mode: ExecutionMode
    client_order_id: str = Field(min_length=1, max_length=36)
    exchange_order_id: str | None = None
    symbol: str
    side: Literal["buy", "sell"]
    reduce_only: bool = False
    state: ExchangeOrderState = ExchangeOrderState.INTENT_CREATED
    requested_quantity: Decimal = Field(gt=0)
    acknowledged_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ReconciliationStatus(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


class DecisionFunnelStage(StrEnum):
    DATA_AVAILABLE = "data_available"
    DATA_FRESH = "data_fresh"
    REGIME_CONFIRMED = "regime_confirmed"
    ENTRY_SIGNAL = "entry_signal"
    CANDIDATE_CREATED = "candidate_created"
    META_LABEL_PASSED = "meta_label_passed"
    MANIFEST_ELIGIBLE = "manifest_eligible"
    RECONCILIATION_HEALTHY = "reconciliation_healthy"
    RISK_APPROVED = "risk_approved"
    AI_REVIEWED = "ai_reviewed"
    PRICE_DRIFT_PASSED = "price_drift_passed"
    EXCHANGE_SUBMITTED = "exchange_submitted"
    EXCHANGE_FILLED = "exchange_filled"
    PROTECTION_CONFIRMED = "protection_confirmed"


class DecisionFunnelStatus(StrEnum):
    PASSED = "PASSED"
    SKIPPED = "SKIPPED"
    REJECTED = "REJECTED"
    ERROR = "ERROR"


class DecisionFunnelTerminal(PlatformModel):
    terminal_id: str | None = None
    paper_run_id: str
    cycle_id: str
    decision_id: str
    symbol: str
    timeframe: str
    bar_time: datetime
    terminal_stage: DecisionFunnelStage
    status: DecisionFunnelStatus
    reason_code: str
    details: dict = Field(default_factory=dict)
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ReconciliationResult(PlatformModel):
    status: ReconciliationStatus
    entry_blocked_symbols: set[str] = Field(default_factory=set)
    actions: list[dict] = Field(default_factory=list)
    error: str | None = None
    snapshot_time: datetime


class PretradeMarketSnapshot(PlatformModel):
    server_time: datetime
    bid: Decimal = Field(gt=0)
    ask: Decimal = Field(gt=0)
    mark_price: Decimal = Field(gt=0)
    decision_bar_close_time: datetime
    decision_age_seconds: float = Field(ge=0)
    atr: Decimal = Field(gt=0)
    tick_size: Decimal = Field(gt=0)
    step_size: Decimal = Field(gt=0)


class RuntimeDatum(PlatformModel):
    value: object | None = None
    source: str
    observed_at: datetime | None = None
    freshness: str
    status: Literal["available", "stale", "unavailable"]
    error: str | None = None


class LlmInvocationStage(StrEnum):
    MARKET_REVIEW = "MARKET_REVIEW"
    TRADE_REVIEW = "TRADE_REVIEW"
    SMOKE = "SMOKE"


class LlmInvocation(PlatformModel):
    invocation_id: str | None = None
    cycle_id: str | None = None
    decision_id: str | None = None
    symbol: str | None = None
    called: bool
    skip_reason: str | None = None
    provider: str | None = None
    model: str | None = None
    stage: LlmInvocationStage
    status: str
    input_hash: str | None = None
    output_hash: str | None = None
    latency_ms: int | None = Field(default=None, ge=0)
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    error: str | None = None
    created_at: datetime | None = None
